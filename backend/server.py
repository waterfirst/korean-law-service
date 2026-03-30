#!/usr/bin/env python3
"""
법률 도우미 백엔드 API 서버 v2
- 법제처 API 프록시 (CORS 우회)
- korean-law-mcp 64개 도구 통합 (CLI 서브프로세스)
- Q&A AI 자동 답변 (Gemini Flash)
- 판례 검색, 법령 원문, 법률 용어 사전
- 이메일 구독 관리 (SQLite)
"""

import os, json, sqlite3, time, hashlib, subprocess, re, html
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from flask import Flask, request, jsonify
from flask_cors import CORS
import requests
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
CORS(app)

# ========== CONFIG ==========
DB_PATH = Path(__file__).parent / "data" / "law_service.db"
LAW_API_BASE = "https://www.law.go.kr/DRF/lawSearch.do"
LAW_OC = os.getenv("LAW_OC", "test")
GEMINI_KEY = os.getenv("GEMINI_API_KEY", "")
KOREAN_LAW_CLI = "korean-law"  # npm -g 설치된 CLI

DB_PATH.parent.mkdir(parents=True, exist_ok=True)

# ========== 법제처 XML API 헬퍼 ==========
def law_api_xml(target, query=None, mst=None, display=20, extra_params=None):
    """법제처 API를 XML로 호출하고 파싱하여 dict 리스트 반환.
    JSON이 빈 {}를 반환하는 버그를 우회."""
    params = {"OC": LAW_OC, "target": target, "type": "XML"}
    if query:
        params["query"] = query
    if mst:
        params["MST"] = mst
    if display:
        params["display"] = display
    if extra_params:
        params.update(extra_params)

    base_url = "https://www.law.go.kr/DRF/lawService.do" if mst and not query else LAW_API_BASE
    resp = requests.get(base_url, params=params, timeout=12)
    resp.raise_for_status()
    root = ET.fromstring(resp.content)
    return root


def xml_find_text(el, tag, default=""):
    """XML 엘리먼트에서 태그 텍스트를 안전하게 추출"""
    child = el.find(tag)
    return child.text.strip() if child is not None and child.text else default


# ========== DATABASE ==========
def get_db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS subscribers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            created_at TEXT DEFAULT (datetime('now')),
            active INTEGER DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS qa_posts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT DEFAULT '익명',
            question TEXT NOT NULL,
            answer TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            ip_hash TEXT
        );
        CREATE TABLE IF NOT EXISTS daily_tips_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT UNIQUE,
            tip_index INTEGER,
            sent_count INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS search_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            query TEXT,
            category TEXT DEFAULT 'law',
            result_count INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now'))
        );
    """)
    conn.close()

init_db()

# ========== KOREAN-LAW-MCP CLI WRAPPER ==========
def law_cli(tool, **kwargs):
    """korean-law-mcp CLI 호출 래퍼"""
    cmd = [KOREAN_LAW_CLI, tool]
    for k, v in kwargs.items():
        cmd.extend([f"--{k}", str(v)])

    env = os.environ.copy()
    env["LAW_OC"] = LAW_OC

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True,
            timeout=30, env=env
        )
        if result.returncode == 0 and result.stdout.strip():
            # CLI가 JSON 출력하는 경우 파싱 시도
            try:
                return json.loads(result.stdout)
            except json.JSONDecodeError:
                return {"text": result.stdout.strip()}
        else:
            return {"error": result.stderr.strip() or "CLI 실행 실패"}
    except subprocess.TimeoutExpired:
        return {"error": "CLI 타임아웃 (30초)"}
    except FileNotFoundError:
        return {"error": "korean-law-mcp CLI 미설치"}

# ========== 보안: 입력 정화 ==========
def sanitize(text):
    """XSS 방지 — HTML 이스케이프"""
    if not text:
        return ""
    return html.escape(str(text).strip())

# ========== ROUTES ==========

@app.route("/api/health", methods=["GET"])
def health():
    return jsonify({
        "status": "ok",
        "service": "법률도우미 API v2",
        "version": "2.0.0",
        "tools": 64,
        "time": datetime.now().isoformat()
    })


# === 법령 검색 (기본) ===
@app.route("/api/search", methods=["GET"])
def search_law():
    """법제처 API 프록시 (법령 검색)"""
    query = sanitize(request.args.get("q", ""))
    if not query:
        return jsonify({"error": "검색어를 입력하세요"}), 400

    # 검색 로그
    try:
        conn = get_db()
        conn.execute("INSERT INTO search_log (query, category) VALUES (?, 'law')", (query,))
        conn.commit()
        conn.close()
    except Exception:
        pass

    try:
        root = law_api_xml("law", query=query, display=20)
        laws = []
        for el in root.findall(".//law"):
            laws.append({
                "title": xml_find_text(el, "법령명한글"),
                "type": xml_find_text(el, "법령구분명", "법률"),
                "id": xml_find_text(el, "법령일련번호"),
                "mst": xml_find_text(el, "법령일련번호"),
                "date": xml_find_text(el, "공포일자"),
                "status": xml_find_text(el, "현행연혁코드"),
                "link": f"https://www.law.go.kr/법령/{xml_find_text(el, '법령명한글')}"
            })
        return jsonify(laws)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# === 판례 검색 ===
@app.route("/api/precedents", methods=["GET"])
def search_precedents():
    """판례 검색 — korean-law-mcp CLI 활용"""
    query = sanitize(request.args.get("q", ""))
    if not query:
        return jsonify({"error": "검색어를 입력하세요"}), 400

    try:
        conn = get_db()
        conn.execute("INSERT INTO search_log (query, category) VALUES (?, 'precedent')", (query,))
        conn.commit()
        conn.close()
    except Exception:
        pass

    # 법제처 판례 XML 직접 검색
    try:
        precs = _search_prec_xml(query)
        # 결과 없으면 키워드 분리 재검색
        if not precs and " " in query:
            for kw in query.split()[:2]:
                if len(kw) >= 2:
                    precs = _search_prec_xml(kw)
                    if precs:
                        break
        return jsonify(precs)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def _search_prec_xml(query, display=15):
    """법제처 판례 XML 검색 → 리스트 반환"""
    root = law_api_xml("prec", query=query, display=display)
    precs = []
    for el in root.findall(".//prec"):
        precs.append({
            "title": xml_find_text(el, "사건명"),
            "court": xml_find_text(el, "법원명"),
            "date": xml_find_text(el, "선고일자"),
            "caseNo": xml_find_text(el, "사건번호"),
            "type": xml_find_text(el, "사건종류명"),
            "id": xml_find_text(el, "판례일련번호"),
            "link": f"https://www.law.go.kr/판례/{xml_find_text(el, '사건번호')}"
        })
    return precs


# === 법령 원문 조회 ===
@app.route("/api/law/text", methods=["GET"])
def get_law_text():
    """법령 원문 조회 — MST(법령일련번호) 기반"""
    mst = request.args.get("mst", "")
    jo = request.args.get("jo", "")  # 특정 조문 (선택)

    if not mst:
        return jsonify({"error": "mst(법령일련번호) 필요"}), 400

    # 1차: korean-law-mcp CLI
    params = {"mst": mst}
    if jo:
        params["jo"] = jo
    result = law_cli("get_law_text", **params)
    if "error" not in result:
        return jsonify(result)

    # 2차: 법제처 XML 직접 API
    try:
        root = law_api_xml("law", mst=mst)
        # 조문 추출
        articles = []
        for art in root.findall(".//조문단위"):
            content = xml_find_text(art, "조문내용")
            if content:
                articles.append({
                    "조문키": xml_find_text(art, "조문키"),
                    "조문번호": xml_find_text(art, "조문번호"),
                    "조문내용": content,
                })
        law_name = xml_find_text(root, ".//법령명_한글") or xml_find_text(root, ".//법령명한글")
        return jsonify({"법령명": law_name, "조문": articles})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# === 법률 용어 사전 ===
@app.route("/api/terms", methods=["GET"])
def search_terms():
    """법률 용어 사전 검색"""
    query = sanitize(request.args.get("q", ""))
    if not query:
        return jsonify({"error": "검색어를 입력하세요"}), 400

    # 1차: korean-law-mcp CLI
    result = law_cli("search_legal_terms", query=query)
    if "error" not in result:
        return jsonify(result)

    # 2차: 내장 법률 용어 사전
    return jsonify(_builtin_term_search(query))


# === 통합 검색 ===
@app.route("/api/search/all", methods=["GET"])
def search_all():
    """법령+판례+행정규칙 통합 검색"""
    query = sanitize(request.args.get("q", ""))
    if not query:
        return jsonify({"error": "검색어를 입력하세요"}), 400

    result = law_cli("search_all", query=query)
    if "error" not in result:
        return jsonify(result)

    return jsonify({"error": "통합검색 실패", "fallback": "개별 검색 API를 이용하세요"}), 500


# === 일상용어 → 법률용어 ===
@app.route("/api/terms/daily", methods=["GET"])
def daily_to_legal():
    """일상용어에서 법률용어 찾기"""
    query = sanitize(request.args.get("q", ""))
    if not query:
        return jsonify({"error": "검색어를 입력하세요"}), 400

    result = law_cli("get_daily_to_legal", query=query)
    if "error" not in result:
        return jsonify(result)

    return jsonify(_builtin_daily_terms(query))


# === 구독 ===
@app.route("/api/subscribe", methods=["POST"])
def subscribe():
    data = request.get_json()
    email = sanitize(data.get("email", "")).lower()
    if not email or "@" not in email:
        return jsonify({"error": "올바른 이메일을 입력하세요"}), 400

    conn = get_db()
    try:
        conn.execute("INSERT OR IGNORE INTO subscribers (email) VALUES (?)", (email,))
        conn.commit()
        count = conn.execute("SELECT COUNT(*) FROM subscribers WHERE active=1").fetchone()[0]
        return jsonify({"status": "ok", "message": f"{email} 구독 완료", "total": count})
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()


@app.route("/api/unsubscribe", methods=["POST"])
def unsubscribe():
    """구독 해제"""
    data = request.get_json()
    email = sanitize(data.get("email", "")).lower()
    if not email:
        return jsonify({"error": "이메일 필요"}), 400

    conn = get_db()
    conn.execute("UPDATE subscribers SET active=0 WHERE email=?", (email,))
    conn.commit()
    conn.close()
    return jsonify({"status": "ok", "message": f"{email} 구독 해제"})


@app.route("/api/subscribers/count", methods=["GET"])
def subscriber_count():
    conn = get_db()
    count = conn.execute("SELECT COUNT(*) FROM subscribers WHERE active=1").fetchone()[0]
    conn.close()
    return jsonify({"count": count})


# === Q&A ===
@app.route("/api/qa", methods=["POST"])
def ask_question():
    data = request.get_json()
    question = sanitize(data.get("question", ""))
    name = sanitize(data.get("name", "")) or "익명"

    if not question:
        return jsonify({"error": "질문을 입력하세요"}), 400

    # 간단 rate limit (같은 IP 10초 내 중복 방지)
    ip_hash = hashlib.sha256(request.remote_addr.encode()).hexdigest()[:12]

    conn = get_db()
    recent = conn.execute(
        "SELECT created_at FROM qa_posts WHERE ip_hash=? ORDER BY id DESC LIMIT 1",
        (ip_hash,)
    ).fetchone()
    if recent:
        last_time = datetime.fromisoformat(recent["created_at"])
        if (datetime.now() - last_time).total_seconds() < 10:
            conn.close()
            return jsonify({"error": "잠시 후 다시 질문해주세요 (10초 제한)"}), 429

    answer = get_ai_answer(question)

    conn.execute(
        "INSERT INTO qa_posts (name, question, answer, ip_hash) VALUES (?,?,?,?)",
        (name, question, answer, ip_hash)
    )
    conn.commit()
    conn.close()

    return jsonify({"status": "ok", "answer": answer})


@app.route("/api/qa/list", methods=["GET"])
def list_qa():
    limit = min(int(request.args.get("limit", 20)), 50)
    conn = get_db()
    rows = conn.execute(
        "SELECT name, question, answer, created_at FROM qa_posts ORDER BY id DESC LIMIT ?",
        (limit,)
    ).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


# === 검색 통계 ===
@app.route("/api/stats", methods=["GET"])
def search_stats():
    """인기 검색어 + 카테고리별 검색 수"""
    conn = get_db()
    popular = conn.execute(
        "SELECT query, COUNT(*) as cnt FROM search_log GROUP BY query ORDER BY cnt DESC LIMIT 10"
    ).fetchall()
    by_cat = conn.execute(
        "SELECT category, COUNT(*) as cnt FROM search_log GROUP BY category"
    ).fetchall()
    total_qa = conn.execute("SELECT COUNT(*) FROM qa_posts").fetchone()[0]
    conn.close()
    return jsonify({
        "popular_searches": [{"query": r["query"], "count": r["cnt"]} for r in popular],
        "by_category": {r["category"]: r["cnt"] for r in by_cat},
        "total_qa": total_qa
    })


# ========== AI ANSWER (법제처 API 연동) ==========

def extract_legal_keywords(question: str) -> list:
    """질문에서 법률 검색 키워드 추출"""
    # 주제별 키워드 매핑
    keyword_map = {
        "오피스텔|주거|상가|임대|임차|전세|월세|보증금|계약|집주인|세입자": ["주택임대차보호법", "상가건물임대차보호법", "민법 임대차"],
        "해고|퇴직|임금|근로|노동|연장|야근|산재|실업": ["근로기준법", "노동"],
        "이혼|양육|위자료|친권|재산분할|혼인": ["민법 혼인", "가사소송법"],
        "사기|횡령|폭행|협박|명예훼손|고소|형사": ["형법", "형사소송법"],
        "세금|양도|상속|증여|소득세|부가세|종합소득": ["소득세법", "상속세및증여세법"],
        "파산|회생|채무|면책|빚|채권": ["채무자회생법", "민사집행법"],
        "특허|발명|상표|저작권|디자인": ["특허법", "저작권법"],
        "교통|음주|면허|사고|벌금|과태료": ["도로교통법", "교통사고처리특례법"],
        "아파트|관리비|층간소음|재건축|분양": ["공동주택관리법", "주택법"],
        "개인정보|프라이버시|정보보호": ["개인정보보호법"],
        "소비자|환불|청약철회|하자": ["소비자기본법", "전자상거래법"],
    }

    keywords = []
    q = question.lower()
    for pattern, kws in keyword_map.items():
        if re.search(pattern, q):
            keywords.extend(kws)

    # 매칭 안 되면 질문에서 명사 추출 (간단 휴리스틱)
    if not keywords:
        # 2글자 이상 단어를 키워드로
        words = re.findall(r'[가-힣]{2,}', question)
        keywords = words[:3] if words else [question[:10]]

    return keywords[:5]


def search_laws_for_qa(keywords: list) -> str:
    """법제처 XML API로 관련 법령 검색 → 컨텍스트 텍스트 생성"""
    all_laws = []
    all_precs = []

    for kw in keywords[:3]:
        # 법령 검색 (XML)
        try:
            root = law_api_xml("law", query=kw, display=5)
            for el in root.findall(".//law"):
                name = xml_find_text(el, "법령명한글")
                if name and name not in [x["name"] for x in all_laws]:
                    all_laws.append({
                        "name": name,
                        "type": xml_find_text(el, "법령구분명"),
                        "mst": xml_find_text(el, "법령일련번호"),
                        "date": xml_find_text(el, "공포일자"),
                    })
        except Exception:
            pass

        # 판례 검색 (XML)
        try:
            root = law_api_xml("prec", query=kw, display=3)
            for el in root.findall(".//prec"):
                case_name = xml_find_text(el, "사건명")
                if case_name:
                    all_precs.append({
                        "name": case_name,
                        "court": xml_find_text(el, "법원명"),
                        "date": xml_find_text(el, "선고일자"),
                        "caseNo": xml_find_text(el, "사건번호"),
                    })
        except Exception:
            pass

    # 법령 원문 조회 (XML)
    law_text_snippet = ""
    if all_laws:
        mst = all_laws[0].get("mst", "")
        if mst:
            try:
                root = law_api_xml("law", mst=mst)
                snippets = []
                for art in root.findall(".//조문단위"):
                    content = xml_find_text(art, "조문내용")
                    if content and len(content) > 10:
                        snippets.append(content.strip())
                    if len(snippets) >= 10:
                        break
                if snippets:
                    law_text_snippet = f"\n\n【{all_laws[0]['name']} 주요 조문】\n" + "\n".join(snippets)
            except Exception:
                pass

    # 컨텍스트 조합
    context_parts = []
    if all_laws:
        law_list = ", ".join([f"{l['name']}({l['type']})" for l in all_laws[:8]])
        context_parts.append(f"【관련 법령】 {law_list}")
    if all_precs:
        prec_list = ", ".join([f"{p['name']}({p['court']}, {p['caseNo']})" for p in all_precs[:5]])
        context_parts.append(f"【관련 판례】 {prec_list}")
    if law_text_snippet:
        context_parts.append(law_text_snippet)

    return "\n".join(context_parts) if context_parts else "", all_laws


def get_ai_answer(question: str) -> str:
    """법제처 API 검색 → Gemini AI 답변 (실제 법령 데이터 기반)"""

    # 1단계: 키워드 추출 & 법제처 검색
    keywords = extract_legal_keywords(question)
    law_context, found_laws = search_laws_for_qa(keywords)

    if not GEMINI_KEY:
        return get_rule_based_answer(question)

    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={GEMINI_KEY}"

        system_prompt = """당신은 대한민국 법률 전문 AI 상담사입니다.
아래의 법제처(www.law.go.kr) 실제 법령 데이터를 기반으로 정확하고 구체적인 답변을 제공하세요.

■ 답변 규칙:
1. 반드시 관련 법률명과 조항 번호를 구체적으로 인용하세요 (예: 주택임대차보호법 제4조 제1항)
2. 법조문의 핵심 내용을 쉬운 말로 풀어서 설명하세요
3. 실용적 조언을 포함하세요 (신고 절차, 구제 기관, 비용, 기한 등)
4. 관련 판례가 있으면 언급하세요
5. 답변은 구조적으로 작성하세요:
   【결론】 질문에 대한 명확한 답변 (가능/불가능, 조건 등)
   【근거 법령】 구체적 법조문 인용 및 설명
   【실무 조언】 실제 행동 가이드
6. 1200자 이내로 답변하세요
7. 한국어 존댓말 사용
8. 마지막에 반드시 다음을 추가: "⚖️ 본 답변은 법제처 법령정보를 기반으로 한 AI 참고용이며, 구체적 사안은 전문가 상담을 권합니다."
9. 모호하거나 확실하지 않은 부분은 솔직히 "이 부분은 구체적 상황에 따라 다를 수 있습니다"라고 밝히세요"""

        user_prompt = f"""질문: {question}"""

        if law_context:
            user_prompt += f"\n\n=== 법제처 검색 결과 (실제 법령 데이터) ===\n{law_context}\n=== 검색 결과 끝 ==="
        else:
            user_prompt += "\n\n(법제처 검색 결과가 없습니다. 일반 법률 지식으로 답변하되, 반드시 관련 법령명을 언급하세요.)"

        payload = {
            "contents": [
                {"role": "user", "parts": [{"text": user_prompt}]}
            ],
            "systemInstruction": {"parts": [{"text": system_prompt}]},
            "generationConfig": {"temperature": 0.2, "maxOutputTokens": 2500}
        }
        resp = requests.post(url, json=payload, timeout=45)
        resp.raise_for_status()
        data = resp.json()
        answer = data["candidates"][0]["content"]["parts"][0]["text"].strip()

        # 출처 법령 링크 추가
        if found_laws:
            source_links = []
            for l in found_laws[:3]:
                name = l.get("name", "")
                if name:
                    source_links.append(f"📜 {name}: https://www.law.go.kr/법령/{name}")
            if source_links:
                answer += "\n\n📚 참조 법령 링크:\n" + "\n".join(source_links)

        return answer
    except Exception as e:
        print(f"Gemini error: {e}")
        return get_rule_based_answer(question)


def get_rule_based_answer(question: str) -> str:
    q = question.lower()
    answers = {
        "전세|보증금|임대|임차|월세": "주택임대차보호법에 따르면, 임차인은 임대차 종료 시 보증금 반환을 청구할 수 있습니다. 보증금 미반환 시: 1) 내용증명 발송, 2) 임차권등기명령 신청, 3) 지급명령 청구를 순서대로 진행하세요. 대한법률구조공단(132) 무료 상담을 권합니다.",
        "음주|운전|면허|교통|사고": "도로교통법 제44조에 따라 혈중알코올농도 0.03% 이상은 음주운전입니다. 1회 적발 시 면허정지/취소, 벌금 또는 징역형이 부과됩니다. 변호사 선임을 권합니다.",
        "세금|양도|소득세|상속세|증여|종합소득": "세금 관련 문제는 국세청(126), 세무서 방문 상담, 또는 세무사 상담을 권합니다. 1세대 1주택 양도소득세 비과세(12억 이하, 2년 보유) 등 다양한 감면제도가 있습니다.",
        "파산|회생|채무|면책|빚": "개인회생은 정기소득이 있고 무담보채무 10억원 이하인 경우 신청 가능합니다. 3~5년 변제 후 잔여 채무가 면책됩니다. 법률구조공단(132)에서 무료 상담 가능합니다.",
        "특허|출원|발명|상표|디자인": "특허출원은 특허로(patent.go.kr)에서 가능합니다. 개인 발명가는 수수료 70% 감면 혜택이 있습니다. 특허청 1544-8080 상담을 권합니다.",
        "이혼|양육|위자료|재산분할|친권": "이혼은 협의이혼(가정법원)과 재판이혼이 있습니다. 위자료, 재산분할, 양육권은 법원이 결정합니다. 법률구조공단에서 무료 상담 가능합니다.",
        "해고|임금|퇴직금|산재|근로": "부당해고는 노동위원회에 구제신청(해고일로부터 3개월 이내) 가능합니다. 퇴직금은 1년 이상 근무 시 의무 지급입니다. 고용노동부 1350 상담을 권합니다.",
        "사기|횡령|배임|고소|형사": "형사 고소는 가까운 경찰서 또는 검찰청에 접수합니다. 공소시효는 범죄에 따라 다르며, 사기죄는 10년입니다. 피해 증거를 잘 보존하세요.",
    }

    for pattern, answer in answers.items():
        if re.search(pattern, q):
            return answer + "\n\n⚖️ 본 답변은 AI 참고용이며, 구체적 사안은 전문가 상담을 권합니다."

    return "구체적인 법률 문제는 대한법률구조공단(132번), 법률홈닥터, 또는 변호사에게 상담하시길 권합니다. 법제처(www.law.go.kr)에서 관련 법령을 검색하실 수 있습니다.\n\n⚖️ 본 답변은 AI 참고용이며, 법률 자문이 아닙니다."


# ========== 내장 사전 (폴백) ==========
def _builtin_term_search(query):
    terms = [
        {"term": "소멸시효", "desc": "일정 기간 권리를 행사하지 않으면 권리가 소멸하는 제도. 민법 제162조~제184조.", "related": ["제척기간", "취득시효"]},
        {"term": "선의취득", "desc": "무권리자로부터 동산을 선의로 취득한 경우 소유권을 인정. 민법 제249조.", "related": ["점유", "무권대리"]},
        {"term": "부당이득", "desc": "법률상 원인 없이 타인의 재산으로 이익을 얻은 것. 민법 제741조.", "related": ["불법행위", "반환청구"]},
        {"term": "채무불이행", "desc": "채무자가 정당한 이유 없이 채무 내용에 따른 이행을 하지 않는 것. 이행지체, 이행불능, 불완전이행.", "related": ["손해배상", "계약해제"]},
        {"term": "대위변제", "desc": "채무자 대신 제3자가 채무를 변제하고 구상권을 취득하는 것.", "related": ["구상권", "연대보증"]},
        {"term": "불법행위", "desc": "고의 또는 과실로 타인에게 손해를 가하는 위법한 행위. 민법 제750조.", "related": ["손해배상", "과실"]},
        {"term": "공소시효", "desc": "일정 기간이 지나면 형사 공소를 제기할 수 없게 되는 제도. 형사소송법 제249조.", "related": ["소멸시효", "공소권"]},
        {"term": "가처분", "desc": "소송 목적물의 현상 변경을 방지하기 위한 임시 보전처분. 민사집행법 제300조.", "related": ["가압류", "보전처분"]},
        {"term": "가압류", "desc": "금전채권의 집행을 보전하기 위해 채무자 재산을 동결하는 처분. 민사집행법 제276조.", "related": ["가처분", "강제집행"]},
        {"term": "항변권", "desc": "상대방의 청구에 대해 이행을 거절할 수 있는 권리. 동시이행의 항변권 등.", "related": ["동시이행", "상계"]},
        {"term": "구상권", "desc": "타인의 채무를 대신 변제한 자가 그 타인에게 상환을 청구할 수 있는 권리.", "related": ["대위변제", "연대보증"]},
        {"term": "임차권", "desc": "임대차계약에 의하여 목적물을 사용·수익할 수 있는 권리.", "related": ["임대차", "대항력"]},
        {"term": "대항력", "desc": "이미 성립한 법률관계를 제3자에게 주장할 수 있는 효력. 주택임대차보호법 제3조.", "related": ["임차권", "확정일자"]},
    ]
    results = []
    for v in terms:
        if query in v["term"] or query in v["desc"]:
            results.append(v)
    return results if results else [{"term": query, "desc": "해당 용어를 찾을 수 없습니다. 법제처 법령용어사전(law.go.kr)을 참고하세요.", "related": []}]


def _builtin_daily_terms(query):
    mapping = {
        "월세": "임대료 (임대차보호법)",
        "전세금": "임차보증금 (주택임대차보호법)",
        "집주인": "임대인",
        "세입자": "임차인",
        "빚": "채무",
        "빚쟁이": "채무자",
        "계약서": "계약증서",
        "고소장": "형사고소장",
        "합의": "민사조정/화해",
        "벌금": "벌금형 (형법)",
        "체납": "체납처분 (국세징수법)",
    }
    results = []
    for daily, legal in mapping.items():
        if query in daily:
            results.append({"daily": daily, "legal": legal})
    return results if results else [{"daily": query, "legal": "매칭되는 법률 용어를 찾지 못했습니다."}]


# ========== MAIN ==========
if __name__ == "__main__":
    port = int(os.getenv("PORT", 5100))
    print(f"🏛️  법률 도우미 API v2 서버 시작 (port {port})")
    print(f"   korean-law-mcp: {KOREAN_LAW_CLI}")
    print(f"   법제처 API 키: {'설정됨' if LAW_OC != 'test' else '⚠️ test (IP등록 필요)'}")
    print(f"   Gemini AI: {'활성' if GEMINI_KEY else '비활성'}")
    app.run(host="0.0.0.0", port=port, debug=False)
