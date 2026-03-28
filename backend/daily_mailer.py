#!/usr/bin/env python3
"""
법률 도우미 - 매일 아침 법률 상식 메일링
크론잡으로 매일 08:00 KST 실행

사용법:
  python3 daily_mailer.py           # 오늘의 법률 상식 발송
  python3 daily_mailer.py --test    # 테스트 (콘솔 출력만)
  python3 daily_mailer.py --preview # 오늘 발송할 내용 미리보기
"""

import os, sys, json, sqlite3, smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, date
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")
load_dotenv(Path(__file__).parent.parent / ".env")

# ========== CONFIG ==========
DB_PATH = Path(__file__).parent / "data" / "law_service.db"
SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASS = os.getenv("SMTP_PASS", "")  # Gmail App Password
FROM_NAME = "법률 도우미"
FROM_EMAIL = SMTP_USER or "law@example.com"

# ========== DAILY TIPS (same as frontend) ==========
DAILY_TIPS = [
    {
        "title": "전세 보증금, 2년 지나면 반환 청구 가능",
        "content": "주택임대차보호법 제4조에 따르면, 임대차 기간이 2년 미만으로 정해진 경우에도 최소 2년이 보장됩니다. 계약 만료 시 임대인은 보증금을 반환해야 하며, 지연 시 연 5%의 지연이자를 청구할 수 있습니다.",
        "source": "주택임대차보호법 제4조, 제3조의2",
        "category": "부동산"
    },
    {
        "title": "음주운전 처벌 기준이 강화되었습니다",
        "content": "2019년 '윤창호법' 시행으로, 혈중알코올농도 0.03% 이상이면 면허 정지, 0.08% 이상이면 면허 취소입니다. 음주운전으로 사람을 다치게 하면 1년 이상 15년 이하의 징역 또는 1천만원 이상 3천만원 이하의 벌금에 처합니다.",
        "source": "도로교통법 제44조, 특정범죄가중처벌법 제5조의11",
        "category": "교통"
    },
    {
        "title": "양도소득세, 1세대 1주택은 비과세",
        "content": "소득세법 제89조에 따라 1세대 1주택자가 2년 이상 보유한 주택을 양도하면 양도소득세가 비과세됩니다. 다만 양도가액이 12억원을 초과하는 부분은 과세됩니다.",
        "source": "소득세법 제89조 제1항 제3호",
        "category": "세금"
    },
    {
        "title": "개인회생, 최저생계비는 보장됩니다",
        "content": "채무자 회생 및 파산에 관한 법률에 따르면, 개인회생 시 채무자의 가용소득 중 최저생계비에 해당하는 금액은 변제계획에서 제외됩니다. 월 소득의 일정 부분만 3~5년간 변제하면 나머지 채무가 면책됩니다.",
        "source": "채무자 회생 및 파산에 관한 법률 제579조, 제614조",
        "category": "파산/회생"
    },
    {
        "title": "특허 출원 후 1년 6개월이면 공개됩니다",
        "content": "특허법 제64조에 따라 특허출원은 출원일로부터 1년 6개월 후 공개됩니다. 출원공개 후에는 경고권이 발생하여, 무단 실시자에 대해 보상금을 청구할 수 있습니다.",
        "source": "특허법 제64조, 제65조",
        "category": "특허"
    },
    {
        "title": "교통사고 후 12주 진단, 형사합의 대상",
        "content": "교통사고처리특례법에 따라 업무상과실로 교통사고를 일으켜 피해자가 상해를 입은 경우, 종합보험 가입자는 피해자의 명시적 의사에 반하여 공소를 제기할 수 없습니다. 다만 12대 중과실에 해당하면 예외입니다.",
        "source": "교통사고처리특례법 제3조 제2항",
        "category": "교통"
    },
    {
        "title": "퇴직금, 1년 이상 근무하면 반드시 지급",
        "content": "근로자퇴직급여 보장법 제8조에 따라, 주 15시간 이상 근무하고 1년 이상 재직한 근로자는 퇴직금을 받을 수 있습니다. 30일분 이상의 평균임금이며, 퇴직 후 14일 이내에 지급해야 합니다.",
        "source": "근로자퇴직급여 보장법 제8조, 제9조",
        "category": "노동"
    },
    {
        "title": "상속 포기는 3개월 내에 해야 합니다",
        "content": "민법 제1019조에 따라 상속인은 상속개시 있음을 안 날로부터 3개월 내에 상속을 포기할 수 있습니다. 기간을 놓치면 단순승인으로 간주되어 피상속인의 채무까지 모두 상속됩니다.",
        "source": "민법 제1019조, 제1028조",
        "category": "가족/상속"
    },
    {
        "title": "명예훼손, 사실이어도 처벌받을 수 있습니다",
        "content": "형법 제307조에 따라, 공연히 사실을 적시하여 타인의 명예를 훼손한 경우 2년 이하의 징역이나 500만원 이하의 벌금에 처합니다. 다만 공익 목적의 진실한 사실 적시는 위법성이 조각됩니다.",
        "source": "형법 제307조, 제310조",
        "category": "형사"
    },
    {
        "title": "전자상거래 7일 이내 청약철회 가능",
        "content": "전자상거래법 제17조에 따라 소비자는 물품을 받은 날부터 7일 이내에 청약철회(환불)를 할 수 있습니다. 시험 착용 등 통상적인 사용은 괜찮습니다.",
        "source": "전자상거래 등에서의 소비자보호에 관한 법률 제17조",
        "category": "소비자"
    },
    {
        "title": "주차장 사고도 도로교통법이 적용됩니다",
        "content": "대법원 판례에 따르면, 불특정 다수가 이용하는 주차장은 도로교통법상의 '도로'에 해당합니다. 따라서 주차장 내 사고에도 도로교통법이 적용되며, 보험 처리가 가능합니다.",
        "source": "대법원 2007다79555, 도로교통법 제2조",
        "category": "교통"
    },
    {
        "title": "부동산 이중계약서 작성은 형사처벌 대상",
        "content": "부동산 실거래가 신고를 허위로 하면 취득세의 2~5배 과태료가 부과됩니다. 또한 3년 이하 징역 또는 1억원 이하 벌금에 처해질 수 있습니다.",
        "source": "부동산 거래신고 등에 관한 법률 제28조",
        "category": "부동산"
    },
]


def get_today_tip():
    """오늘의 법률 상식 선택 (날짜 기반)"""
    today = date.today()
    day_of_year = today.timetuple().tm_yday
    idx = day_of_year % len(DAILY_TIPS)
    return DAILY_TIPS[idx], idx


def build_email_html(tip):
    """이메일 HTML 템플릿 생성"""
    today_str = datetime.now().strftime("%Y년 %m월 %d일 %A")
    return f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif; background:#f7fafc; padding:20px;">
<div style="max-width:600px; margin:0 auto; background:white; border-radius:16px; overflow:hidden; box-shadow:0 2px 10px rgba(0,0,0,0.1);">

  <!-- Header -->
  <div style="background:linear-gradient(135deg,#1a365d,#2b6cb0); color:white; padding:30px; text-align:center;">
    <h1 style="margin:0; font-size:1.5rem;">&#9878; 오늘의 법률 상식</h1>
    <p style="margin:8px 0 0; opacity:0.9; font-size:0.9rem;">{today_str}</p>
  </div>

  <!-- Content -->
  <div style="padding:30px;">
    <div style="display:inline-block; background:#d69e2e; color:white; padding:4px 14px; border-radius:20px; font-size:0.8rem; font-weight:600; margin-bottom:16px;">
      {tip['category']}
    </div>
    <h2 style="color:#1a365d; margin:0 0 12px; font-size:1.3rem;">{tip['title']}</h2>
    <p style="color:#2d3748; line-height:1.8; font-size:0.95rem;">{tip['content']}</p>
    <p style="color:#718096; font-size:0.85rem; margin-top:16px; padding-top:12px; border-top:1px solid #e2e8f0;">
      &#128218; <strong>근거:</strong> {tip['source']}
    </p>
  </div>

  <!-- Footer -->
  <div style="background:#f7fafc; padding:20px 30px; text-align:center; font-size:0.8rem; color:#718096;">
    <p>이 메일은 <strong>법률 도우미</strong>에서 발송되었습니다.</p>
    <p>본 내용은 참고용이며, 구체적 법률 문제는 전문가에게 상담하세요.</p>
    <p style="margin-top:8px;">
      <a href="https://waterfirst.github.io/korean-law-service/" style="color:#2b6cb0;">웹사이트 방문</a>
      &nbsp;|&nbsp;
      <a href="https://www.law.go.kr" style="color:#2b6cb0;">법제처</a>
    </p>
  </div>
</div>
</body>
</html>"""


def get_subscribers():
    """활성 구독자 목록 조회"""
    if not DB_PATH.exists():
        return []
    conn = sqlite3.connect(str(DB_PATH))
    rows = conn.execute("SELECT email FROM subscribers WHERE active=1").fetchall()
    conn.close()
    return [r[0] for r in rows]


def send_email(to_email, subject, html_body):
    """SMTP를 통한 이메일 발송"""
    if not SMTP_USER or not SMTP_PASS:
        print(f"  [SKIP] SMTP 미설정 → {to_email}")
        return False

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = f"{FROM_NAME} <{FROM_EMAIL}>"
    msg["To"] = to_email
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.starttls()
            server.login(SMTP_USER, SMTP_PASS)
            server.send_message(msg)
        print(f"  [OK] {to_email}")
        return True
    except Exception as e:
        print(f"  [FAIL] {to_email}: {e}")
        return False


def send_telegram_tip(tip):
    """텔레그램으로도 발송 (보조 채널)"""
    try:
        import subprocess
        today_str = datetime.now().strftime("%Y.%m.%d")
        text = f"⚖️ *오늘의 법률 상식* ({today_str})\n\n"
        text += f"📌 *{tip['title']}*\n\n"
        text += f"{tip['content']}\n\n"
        text += f"📖 근거: {tip['source']}\n\n"
        text += f"_본 내용은 참고용이며, 구체적 법률 문제는 전문가에게 상담하세요._"

        # Telegram Bot API
        bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "7927906835:AAFrilD2u3_maMK8uI5OMWVBJ_yA-Cj4U3Y")
        chat_id = os.getenv("TELEGRAM_CHAT_ID", "5767743818")

        import requests
        resp = requests.post(
            f"https://api.telegram.org/bot{bot_token}/sendMessage",
            json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"},
            timeout=10
        )
        if resp.ok:
            print("  [Telegram] 발송 성공")
        else:
            print(f"  [Telegram] 발송 실패: {resp.text[:100]}")
    except Exception as e:
        print(f"  [Telegram] 오류: {e}")


def main():
    test_mode = "--test" in sys.argv
    preview = "--preview" in sys.argv

    print("=" * 50)
    print("⚖️  법률 도우미 - 매일 아침 법률 상식 메일링")
    print("=" * 50)

    tip, idx = get_today_tip()
    today_str = datetime.now().strftime("%Y.%m.%d")
    subject = f"[법률상식] {tip['title']} ({today_str})"

    print(f"\n📌 오늘의 상식 (#{idx+1}):")
    print(f"  제목: {tip['title']}")
    print(f"  분야: {tip['category']}")
    print(f"  내용: {tip['content'][:60]}...")
    print(f"  근거: {tip['source']}")

    if preview:
        html = build_email_html(tip)
        preview_path = Path(__file__).parent / "data" / "preview.html"
        preview_path.write_text(html, encoding="utf-8")
        print(f"\n📧 미리보기 저장: {preview_path}")
        return

    subscribers = get_subscribers()
    print(f"\n👥 구독자: {len(subscribers)}명")

    if test_mode:
        print("\n[TEST MODE] 실제 발송하지 않음")
        # 텔레그램으로만 테스트 발송
        send_telegram_tip(tip)
        return

    # 이메일 발송
    html = build_email_html(tip)
    success = 0
    for email in subscribers:
        if send_email(email, subject, html):
            success += 1

    # 텔레그램도 발송
    send_telegram_tip(tip)

    # 기록
    if DB_PATH.exists():
        conn = sqlite3.connect(str(DB_PATH))
        conn.execute(
            "INSERT OR REPLACE INTO daily_tips_log (date, tip_index, sent_count) VALUES (?, ?, ?)",
            (today_str, idx, success)
        )
        conn.commit()
        conn.close()

    print(f"\n✅ 완료: {success}/{len(subscribers)}명 발송 성공")


if __name__ == "__main__":
    main()
