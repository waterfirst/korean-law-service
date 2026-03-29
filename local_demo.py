#!/usr/bin/env python3
"""
법률 도우미 로컬 데모 (Gradio)
- pip install gradio requests 만으로 실행 가능
- 노트북(Jupyter/Colab)에서도 실행 가능
- 법제처 Open API + Gemini AI 활용

사용법:
  pip install gradio requests
  python local_demo.py
"""

import requests, re, os

# ========== 설정 ==========
LAW_OC = os.environ.get("LAW_OC", "ScholarBridge")
GEMINI_KEY = os.environ.get("GEMINI_API_KEY", "")
LAW_API = "https://www.law.go.kr/DRF/lawSearch.do"

# ========== 내장 법률 용어 사전 ==========
LEGAL_TERMS = {
    "소멸시효": "일정 기간 권리를 행사하지 않으면 권리가 소멸하는 제도. 민법 제162조~제184조. 관련: 제척기간, 취득시효",
    "선의취득": "무권리자로부터 동산을 선의로 취득한 경우 소유권을 인정. 민법 제249조. 관련: 점유, 무권대리",
    "부당이득": "법률상 원인 없이 타인의 재산으로 이익을 얻은 것. 민법 제741조. 관련: 불법행위, 반환청구",
    "채무불이행": "채무자가 정당한 이유 없이 채무를 이행하지 않는 것. 이행지체·이행불능·불완전이행. 관련: 손해배상, 계약해제",
    "대위변제": "채무자 대신 제3자가 채무를 변제하고 구상권을 취득하는 것. 관련: 구상권, 연대보증",
    "불법행위": "고의 또는 과실로 타인에게 손해를 가하는 위법 행위. 민법 제750조. 관련: 손해배상, 과실",
    "공소시효": "일정 기간이 지나면 형사 공소를 제기할 수 없는 제도. 형사소송법 제249조. 관련: 소멸시효, 공소권",
    "가처분": "소송 목적물의 현상 변경을 방지하기 위한 임시 보전처분. 민사집행법 제300조. 관련: 가압류, 보전처분",
    "가압류": "금전채권 집행 보전을 위해 채무자 재산을 동결하는 처분. 민사집행법 제276조. 관련: 가처분, 강제집행",
    "항변권": "상대방의 청구에 대해 이행을 거절할 수 있는 권리. 동시이행의 항변권 등. 관련: 동시이행, 상계",
    "구상권": "타인의 채무를 대신 변제한 자가 상환을 청구할 수 있는 권리. 관련: 대위변제, 연대보증",
    "임차권": "임대차계약에 의해 목적물을 사용·수익할 수 있는 권리. 관련: 임대차, 대항력",
    "대항력": "이미 성립한 법률관계를 제3자에게 주장할 수 있는 효력. 주택임대차보호법 제3조. 관련: 임차권, 확정일자",
    "내용증명": "우체국을 통해 발신 사실과 내용을 증명하는 문서. 법적 분쟁에서 증거로 활용. 관련: 소송, 통지",
    "확정일자": "문서에 공증 기관이 날짜를 확인해 주는 것. 임대차 우선변제권 요건. 관련: 대항력, 임차권",
}

DAILY_TO_LEGAL = {
    "월세": "임대료 (임대차보호법)", "전세금": "임차보증금 (주택임대차보호법)",
    "집주인": "임대인", "세입자": "임차인", "빚": "채무", "빚쟁이": "채무자",
    "계약서": "계약증서", "고소장": "형사고소장", "합의": "민사조정/화해",
    "벌금": "벌금형 (형법)", "체납": "체납처분 (국세징수법)", "소송": "민사소송/형사소송",
    "변호사비": "소송비용 (변호사보수)", "재판": "공판절차 (형사) / 변론기일 (민사)",
}


# ========== 1. 법령 검색 ==========
def search_law(query):
    if not query.strip():
        return "⚠️ 검색어를 입력하세요."
    try:
        resp = requests.get(LAW_API, params={
            "OC": LAW_OC, "target": "law", "type": "JSON",
            "query": query, "display": 10
        }, timeout=10)
        data = resp.json()

        if "LawSearch" in data and "law" in data["LawSearch"]:
            laws = data["LawSearch"]["law"]
            if not isinstance(laws, list):
                laws = [laws]
            total = data["LawSearch"].get("totalCnt", len(laws))
            lines = [f"## 🔍 '{query}' 검색 결과 ({total}건)\n"]
            for i, l in enumerate(laws, 1):
                name = l.get("법령명한글", l.get("법령명", ""))
                kind = l.get("법령구분명", "법률")
                date = l.get("공포일자", "")
                dept = l.get("소관부처명", "")
                mst = l.get("법령일련번호", "")
                link = f"https://www.law.go.kr/법령/{name}"
                date_fmt = f"{date[:4]}.{date[4:6]}.{date[6:]}" if len(date) == 8 else date
                lines.append(f"**{i}. [{name}]({link})**")
                lines.append(f"   - 구분: {kind} | 소관: {dept} | 공포일: {date_fmt} | MST: {mst}")
                lines.append("")
            return "\n".join(lines)

        if "result" in data:
            return f"⚠️ API 오류: {data['result']}\n{data.get('msg', '')}\n\n💡 OC 코드 승인 대기 중일 수 있습니다."
        return "검색 결과가 없습니다."
    except Exception as e:
        return f"❌ 오류: {e}"


# ========== 2. 판례 검색 ==========
def search_precedent(query):
    if not query.strip():
        return "⚠️ 검색어를 입력하세요."
    try:
        resp = requests.get(LAW_API, params={
            "OC": LAW_OC, "target": "prec", "type": "JSON",
            "query": query, "display": 10
        }, timeout=10)
        data = resp.json()

        if "PrecSearch" in data and "prec" in data["PrecSearch"]:
            precs = data["PrecSearch"]["prec"]
            if not isinstance(precs, list):
                precs = [precs]
            total = data["PrecSearch"].get("totalCnt", len(precs))
            lines = [f"## ⚖️ '{query}' 판례 검색 ({total}건)\n"]
            for i, p in enumerate(precs, 1):
                title = p.get("사건명", "")
                court = p.get("법원명", "")
                date = p.get("선고일자", "")
                case_no = p.get("사건번호", "")
                lines.append(f"**{i}. {title}**")
                lines.append(f"   - {court} | {case_no} | 선고일: {date}")
                lines.append("")
            return "\n".join(lines)

        if "result" in data:
            return f"⚠️ API 오류: {data['result']}\n💡 OC 코드 승인 대기 중일 수 있습니다."
        return "판례 검색 결과가 없습니다."
    except Exception as e:
        return f"❌ 오류: {e}"


# ========== 3. 법률 용어 사전 ==========
def search_terms(query):
    if not query.strip():
        return "⚠️ 검색어를 입력하세요."
    results = []
    for term, desc in LEGAL_TERMS.items():
        if query in term or query in desc:
            results.append(f"### 📘 {term}\n{desc}\n")
    if results:
        return f"## 법률 용어: '{query}'\n\n" + "\n".join(results)
    return f"'{query}'에 대한 용어를 찾지 못했습니다.\n\n💡 법제처 법령용어사전(law.go.kr)을 참고하세요."


# ========== 4. 일상용어 → 법률용어 ==========
def daily_to_legal(query):
    if not query.strip():
        return "⚠️ 검색어를 입력하세요."
    results = []
    for daily, legal in DAILY_TO_LEGAL.items():
        if query in daily:
            results.append(f"- **{daily}** → {legal}")
    if results:
        return f"## 🔄 일상용어 → 법률용어\n\n" + "\n".join(results)

    # 모든 매핑 보여주기
    all_items = "\n".join(f"- **{d}** → {l}" for d, l in DAILY_TO_LEGAL.items())
    return f"'{query}'와 매칭되는 용어가 없습니다.\n\n**전체 매핑 목록:**\n{all_items}"


# ========== 5. AI 법률 상담 ==========
def ai_counsel(question):
    if not question.strip():
        return "⚠️ 질문을 입력하세요."

    # Gemini API 사용
    if GEMINI_KEY:
        try:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={GEMINI_KEY}"
            payload = {
                "contents": [{"parts": [{"text": f"""당신은 한국 법률 전문 AI 상담사입니다.

규칙:
1. 관련 법령을 구체적으로 인용 (법률명, 조항 번호)
2. 실용적 조언 포함 (상담 기관, 절차, 비용)
3. 600자 이내 답변
4. 마지막에 "⚖️ 본 답변은 AI 참고용이며, 구체적 사안은 전문가 상담을 권합니다." 추가
5. 한국어, 존댓말, 마크다운 형식

질문: {question}"""}]}],
                "generationConfig": {"temperature": 0.3, "maxOutputTokens": 1200}
            }
            resp = requests.post(url, json=payload, timeout=30)
            resp.raise_for_status()
            data = resp.json()
            answer = data["candidates"][0]["content"]["parts"][0]["text"].strip()
            return f"## 🤖 AI 법률 상담\n\n**Q: {question}**\n\n{answer}"
        except Exception as e:
            pass  # 폴백

    # 규칙 기반 폴백
    q = question.lower()
    rules = {
        "전세|보증금|임대|임차|월세": "주택임대차보호법에 따르면, 임차인은 임대차 종료 시 보증금 반환을 청구할 수 있습니다.\n\n**절차:**\n1. 내용증명 발송\n2. 임차권등기명령 신청 (법원)\n3. 지급명령 청구 또는 소액사건심판\n\n**무료 상담:** 대한법률구조공단 ☎ 132",
        "음주|운전|면허": "도로교통법 제44조에 따라 혈중알코올농도 0.03% 이상은 음주운전입니다.\n\n- 1회: 면허정지/취소 + 벌금\n- 2회 이상: 2~5년 징역\n- 측정 거부: 1~5년 징역\n\n변호사 선임을 권합니다.",
        "해고|퇴직금|임금|근로": "부당해고는 **노동위원회**에 구제신청 가능 (해고일 3개월 이내).\n퇴직금은 1년 이상 근무 시 의무지급.\n\n**상담:** 고용노동부 ☎ 1350",
        "이혼|양육|위자료": "이혼은 협의이혼(가정법원)과 재판이혼이 있습니다.\n위자료, 재산분할, 양육권은 법원이 결정합니다.\n\n**무료 상담:** 법률구조공단 ☎ 132",
        "세금|양도|소득세": "1세대 1주택(2년 보유, 12억 이하)은 양도소득세 비과세.\n\n**상담:** 국세청 ☎ 126, 세무사 상담 권장",
        "특허|출원|발명": "특허출원은 특허로(patent.go.kr)에서 전자출원 가능.\n개인 발명가는 수수료 70% 감면.\n\n**상담:** 특허청 ☎ 1544-8080",
        "사기|횡령|고소": "형사 고소는 가까운 경찰서 또는 검찰청에 접수.\n사기죄 공소시효 10년.\n\n피해 증거를 잘 보존하세요.",
        "파산|회생|채무": "개인회생: 정기소득 + 무담보채무 10억원 이하.\n3~5년 변제 후 잔여 채무 면책.\n\n**무료 상담:** 법률구조공단 ☎ 132",
    }
    for pattern, answer in rules.items():
        if re.search(pattern, q):
            return f"## 🤖 AI 법률 상담\n\n**Q: {question}**\n\n{answer}\n\n⚖️ 본 답변은 AI 참고용이며, 구체적 사안은 전문가 상담을 권합니다."

    return f"## 🤖 AI 법률 상담\n\n**Q: {question}**\n\n구체적인 법률 문제는 아래에서 상담받으세요:\n- 대한법률구조공단 ☎ 132\n- 법률홈닥터 (무료)\n- 법제처 법령검색 www.law.go.kr\n\n⚖️ 본 답변은 AI 참고용이며, 법률 자문이 아닙니다."


# ========== Gradio UI ==========
def build_app():
    try:
        import gradio as gr
    except ImportError:
        print("❌ gradio 미설치. 아래 명령으로 설치하세요:")
        print("   pip install gradio")
        return None

    with gr.Blocks(
        title="법률 도우미 v2",
        theme=gr.themes.Soft(primary_hue="blue"),
        css=".gradio-container { max-width: 900px !important; margin: auto !important; }"
    ) as app:

        gr.Markdown("# ⚖️ 법률 도우미 v2\n법제처 Open API + AI 기반 법률 정보 서비스")

        with gr.Tab("🔍 법령 검색"):
            gr.Markdown("법제처 데이터베이스에서 법령을 검색합니다.")
            with gr.Row():
                law_input = gr.Textbox(label="검색어", placeholder="예: 근로기준법, 주택임대차보호법, 특허법...", scale=4)
                law_btn = gr.Button("검색", variant="primary", scale=1)
            gr.Examples(["근로기준법", "주택임대차보호법", "소득세법", "특허법", "형법"], inputs=law_input)
            law_output = gr.Markdown()
            law_btn.click(search_law, inputs=law_input, outputs=law_output)
            law_input.submit(search_law, inputs=law_input, outputs=law_output)

        with gr.Tab("⚖️ 판례 검색"):
            gr.Markdown("대법원·헌법재판소 판례를 검색합니다.")
            with gr.Row():
                prec_input = gr.Textbox(label="검색어", placeholder="예: 부당해고, 명예훼손, 전세보증금...", scale=4)
                prec_btn = gr.Button("검색", variant="primary", scale=1)
            gr.Examples(["부당해고", "명예훼손", "사기", "교통사고", "이혼 재산분할"], inputs=prec_input)
            prec_output = gr.Markdown()
            prec_btn.click(search_precedent, inputs=prec_input, outputs=prec_output)
            prec_input.submit(search_precedent, inputs=prec_input, outputs=prec_output)

        with gr.Tab("📘 용어 사전"):
            gr.Markdown("법률 용어를 쉽게 풀어드립니다.")
            with gr.Row():
                term_input = gr.Textbox(label="용어", placeholder="예: 소멸시효, 가압류, 대항력...", scale=4)
                term_btn = gr.Button("검색", variant="primary", scale=1)
            gr.Examples(["소멸시효", "가처분", "대항력", "채무불이행", "구상권"], inputs=term_input)
            term_output = gr.Markdown()
            term_btn.click(search_terms, inputs=term_input, outputs=term_output)
            term_input.submit(search_terms, inputs=term_input, outputs=term_output)

        with gr.Tab("🔄 일상→법률"):
            gr.Markdown("일상 용어를 법률 용어로 변환합니다.")
            with gr.Row():
                daily_input = gr.Textbox(label="일상 용어", placeholder="예: 월세, 빚, 집주인...", scale=4)
                daily_btn = gr.Button("변환", variant="primary", scale=1)
            gr.Examples(["월세", "빚", "집주인", "계약서", "벌금", "합의"], inputs=daily_input)
            daily_output = gr.Markdown()
            daily_btn.click(daily_to_legal, inputs=daily_input, outputs=daily_output)
            daily_input.submit(daily_to_legal, inputs=daily_input, outputs=daily_output)

        with gr.Tab("🤖 AI 상담"):
            gr.Markdown("법률 질문에 AI가 답변합니다. (참고용, 법률 자문 아님)")
            qa_input = gr.Textbox(label="질문", placeholder="예: 전세 만기인데 집주인이 보증금을 안 돌려줍니다.", lines=3)
            qa_btn = gr.Button("💬 질문하기", variant="primary")
            gr.Examples([
                "전세 보증금을 돌려받지 못하고 있습니다",
                "음주운전 1회 적발 시 처벌은?",
                "퇴직금 계산 방법을 알려주세요",
                "개인회생 신청 조건이 궁금합니다",
                "특허 출원 절차와 비용이 알고 싶습니다"
            ], inputs=qa_input)
            qa_output = gr.Markdown()
            qa_btn.click(ai_counsel, inputs=qa_input, outputs=qa_output)
            qa_input.submit(ai_counsel, inputs=qa_input, outputs=qa_output)

        gr.Markdown("""
---
**법률 도우미 v2** | 법제처 Open API (OC: ScholarBridge) + Gemini AI
⚖️ 본 서비스는 참고용이며, 구체적 법률 문제는 전문가 상담을 권합니다.
[법제처](https://www.law.go.kr) · [GitHub](https://github.com/waterfirst/korean-law-service)
""")

    return app


# ========== 실행 ==========
if __name__ == "__main__":
    app = build_app()
    if app:
        print("🏛️  법률 도우미 v2 로컬 데모 시작")
        print(f"   OC: {LAW_OC}")
        print(f"   Gemini AI: {'✅ 활성' if GEMINI_KEY else '❌ 비활성 (규칙기반 폴백)'}")
        print()
        app.launch(share=False, server_name="0.0.0.0", server_port=7860)
