#!/usr/bin/env python3
"""
법률 도우미 백엔드 API 서버
- 법제처 API 프록시 (CORS 우회)
- Q&A AI 자동 답변 (Gemini Flash)
- 이메일 구독 관리 (SQLite)
"""

import os, json, sqlite3, time, hashlib
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
LAW_OC = os.getenv("LAW_OC", "test")  # 법제처 API key
GEMINI_KEY = os.getenv("GEMINI_API_KEY", "")

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
    """)
    conn.close()

init_db()

# ========== ROUTES ==========

@app.route("/api/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "service": "법률도우미 API", "time": datetime.now().isoformat()})


@app.route("/api/search", methods=["GET"])
def search_law():
    """법제처 API 프록시"""
    query = request.args.get("q", "")
    if not query:
        return jsonify({"error": "검색어를 입력하세요"}), 400

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
                        "date": l.get("공포일자", ""),
                        "status": l.get("시행여부", ""),
                        "link": f"https://www.law.go.kr/법령/{l.get('법령명한글', '')}"
                    })
            return jsonify(laws)
        else:
            return jsonify({"error": f"법제처 API 오류: {resp.status_code}"}), 502
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/subscribe", methods=["POST"])
def subscribe():
    """이메일 구독 등록"""
    data = request.get_json()
    email = data.get("email", "").strip().lower()
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


@app.route("/api/subscribers/count", methods=["GET"])
def subscriber_count():
    conn = get_db()
    count = conn.execute("SELECT COUNT(*) FROM subscribers WHERE active=1").fetchone()[0]
    conn.close()
    return jsonify({"count": count})


@app.route("/api/qa", methods=["POST"])
def ask_question():
    """Q&A: 질문 등록 + AI 자동 답변"""
    data = request.get_json()
    question = data.get("question", "").strip()
    name = data.get("name", "익명").strip() or "익명"

    if not question:
        return jsonify({"error": "질문을 입력하세요"}), 400

    ip_hash = hashlib.md5(request.remote_addr.encode()).hexdigest()[:8]

    # Get AI answer
    answer = get_ai_answer(question)

    conn = get_db()
    conn.execute(
        "INSERT INTO qa_posts (name, question, answer, ip_hash) VALUES (?,?,?,?)",
        (name, question, answer, ip_hash)
    )
    conn.commit()
    conn.close()

    return jsonify({"status": "ok", "answer": answer})


@app.route("/api/qa/list", methods=["GET"])
def list_qa():
    """Q&A 목록 조회"""
    limit = min(int(request.args.get("limit", 20)), 50)
    conn = get_db()
    rows = conn.execute(
        "SELECT name, question, answer, created_at FROM qa_posts ORDER BY id DESC LIMIT ?",
        (limit,)
    ).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


# ========== AI ANSWER ==========
def get_ai_answer(question: str) -> str:
    """Gemini Flash를 이용한 법률 AI 답변"""
    if not GEMINI_KEY:
        return get_rule_based_answer(question)

    try:
        from google import genai
        client = genai.Client(api_key=GEMINI_KEY)
        resp = client.models.generate_content(
            model="gemini-2.0-flash",
            contents=f"""당신은 한국 법률 전문 AI 상담사입니다. 아래 법률 질문에 대해 답변하세요.

규칙:
1. 관련 법령을 구체적으로 인용하세요 (법률명, 조항)
2. 실용적인 조언을 포함하세요 (어디에 상담할 수 있는지 등)
3. 300자 이내로 간결하게 답변하세요
4. 반드시 마지막에 "본 답변은 AI 참고용이며, 구체적 사안은 전문가 상담을 권합니다." 를 추가하세요
5. 한국어로 답변하세요

질문: {question}"""
        )
        return resp.text.strip()
    except Exception as e:
        print(f"Gemini error: {e}")
        return get_rule_based_answer(question)


def get_rule_based_answer(question: str) -> str:
    """규칙 기반 법률 답변 (AI 없이)"""
    q = question.lower()
    answers = {
        "전세|보증금|임대|임차": "주택임대차보호법에 따르면, 임차인은 임대차 종료 시 보증금 반환을 청구할 수 있습니다. 보증금 미반환 시: 1) 내용증명 발송, 2) 임차권등기명령 신청, 3) 지급명령 청구를 순서대로 진행하세요. 대한법률구조공단(132) 무료 상담을 권합니다.",
        "음주|운전|면허|교통": "도로교통법 제44조에 따라 혈중알코올농도 0.03% 이상은 음주운전입니다. 1회 적발 시 면허정지/취소, 벌금 또는 징역형이 부과됩니다. 변호사 선임을 권합니다.",
        "세금|양도|소득세|상속세|증여": "세금 관련 문제는 국세청(126), 세무서 방문 상담, 또는 세무사 상담을 권합니다. 1세대 1주택 양도소득세 비과세(12억 이하, 2년 보유) 등 다양한 감면제도가 있습니다.",
        "파산|회생|채무|면책": "개인회생은 정기소득이 있고 무담보채무 10억원 이하인 경우 신청 가능합니다. 3~5년 변제 후 잔여 채무가 면책됩니다. 법률구조공단(132)에서 무료 상담 가능합니다.",
        "특허|출원|발명|상표": "특허출원은 특허로(patent.go.kr)에서 가능합니다. 개인 발명가는 수수료 70% 감면 혜택이 있습니다. 특허청 1544-8080 상담을 권합니다.",
        "이혼|양육|위자료|재산분할": "이혼은 협의이혼(가정법원)과 재판이혼이 있습니다. 위자료, 재산분할, 양육권은 법원이 결정합니다. 법률구조공단에서 무료 상담 가능합니다.",
        "해고|임금|퇴직금|산재": "부당해고는 노동위원회에 구제신청(해고일로부터 3개월 이내) 가능합니다. 퇴직금은 1년 이상 근무 시 의무 지급입니다. 고용노동부 1350 상담을 권합니다.",
    }

    import re
    for pattern, answer in answers.items():
        if re.search(pattern, q):
            return answer + " *본 답변은 AI 참고용이며, 구체적 사안은 전문가 상담을 권합니다.*"

    return "구체적인 법률 문제는 대한법률구조공단(132번), 법률홈닥터(무료법률상담), 또는 변호사/법무사에게 상담하시길 권합니다. 법제처(www.law.go.kr)에서 관련 법령을 직접 검색하실 수도 있습니다. *본 답변은 AI 참고용이며, 법률 자문이 아닙니다.*"


# ========== MAIN ==========
if __name__ == "__main__":
    port = int(os.getenv("PORT", 5100))
    print(f"🏛️  법률 도우미 API 서버 시작 (port {port})")
    app.run(host="0.0.0.0", port=port, debug=False)
