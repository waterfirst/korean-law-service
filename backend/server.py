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
        resp = requests.get(LAW_API_BASE, params={
            "OC": LAW_OC,
            "target": "law",
            "type": "JSON",
            "query": query,
            "display": 20
        }, timeout=10)

        if resp.status_code == 200:
            data = resp.json()
            laws = []
            if "LawSearch" in data and "law" in data["LawSearch"]:
                raw = data["LawSearch"]["law"]
                if not isinstance(raw, list):
                    raw = [raw]
                for l in raw:
                    laws.append({
                        "title": l.get("법령명한글", l.get("법령명", "")),
                        "type": l.get("법령구분", "법률"),
                        "id": l.get("법령일련번호", ""),
                        "mst": l.get("법령일련번호", ""),
                        "date": l.get("공포일자", ""),
                        "status": l.get("시행여부", ""),
                        "link": f"https://www.law.go.kr/법령/{l.get('법령명한글', '')}"
                    })
            return jsonify(laws)
        else:
            return jsonify({"error": f"법제처 API 오류: {resp.status_code}"}), 502
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

    # 1차: korean-law-mcp CLI
    result = law_cli("search_precedents", query=query)
    if "error" not in result:
        return jsonify(result)

    # 2차: 법제처 판례 직접 검색
    try:
        resp = requests.get(LAW_API_BASE, params={
            "OC": LAW_OC,
            "target": "prec",
            "type": "JSON",
            "query": query,
            "display": 15
        }, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            precs = []
            raw = data.get("PrecSearch", {}).get("prec", [])
            if isinstance(raw, dict):
                raw = [raw]
            for p in raw:
                precs.append({
                    "title": p.get("사건명", ""),
                    "court": p.get("법원명", ""),
                    "date": p.get("선고일자", ""),
                    "caseNo": p.get("사건번호", ""),
                    "id": p.get("판례일련번호", ""),
                    "link": f"https://www.law.go.kr/판례/{p.get('사건번호', '')}"
                })
            return jsonify(precs)
        return jsonify([])
    except Exception as e:
        return jsonify({"error": str(e)}), 500


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

    # 2차: 법제처 직접 API
    try:
        resp = requests.get("https://www.law.go.kr/DRF/lawService.do", params={
            "OC": LAW_OC,
            "target": "law",
            "type": "JSON",
            "MST": mst
        }, timeout=15)
        if resp.status_code == 200:
            return jsonify(resp.json())
        return jsonify({"error": f"법제처 API 오류: {resp.status_code}"}), 502
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


# ========== AI ANSWER ==========
def get_ai_answer(question: str) -> str:
    if not GEMINI_KEY:
        return get_rule_based_answer(question)

    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={GEMINI_KEY}"
        payload = {
            "contents": [{"parts": [{"text": f"""당신은 한국 법률 전문 AI 상담사입니다.

규칙:
1. 관련 법령을 구체적으로 인용 (법률명, 조항 번호)
2. 실용적 조언 포함 (상담 기관, 절차)
3. 500자 이내 답변
4. 마지막에 "⚖️ 본 답변은 AI 참고용이며, 구체적 사안은 전문가 상담을 권합니다." 추가
5. 한국어, 존댓말

질문: {question}"""}]}],
            "generationConfig": {"temperature": 0.3, "maxOutputTokens": 1000}
        }
        resp = requests.post(url, json=payload, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        return data["candidates"][0]["content"]["parts"][0]["text"].strip()
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
