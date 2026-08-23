import smtplib
import ssl
from email.message import EmailMessage
from html import escape
from dotenv import load_dotenv
import os

load_dotenv()

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587
SMTP_USER = os.getenv("SMTP_USER")
SMTP_PASS = os.getenv("SMTP_PASS")


def send_email(to: str, subject: str, html: str):
    context = ssl.create_default_context()
    msg = EmailMessage()
    from_str = f"HAFS PrintQueue <${SMTP_USER}>"
    msg["Subject"] = subject
    msg["From"] = from_str
    msg["To"] = to
    msg.set_content(html, subtype="html")

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as smtp:
        smtp.starttls(context=context)
        smtp.login(user=SMTP_USER, password=SMTP_PASS)
        smtp.send_message(msg)


def send_print_done_email(to: str, user_name: str, job_filename: str):
    subject = f"[HAFS PrintQueue] 출력 완료 - {job_filename}"
    html = f"""<!DOCTYPE html>
<html lang="ko">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>출력 완료</title>
</head>
<body style="margin:0;padding:0;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI','Noto Sans KR',system-ui,sans-serif;color:#1a1a1a;font-size:15px;line-height:1.6;">
  <table width="100%" cellpadding="0" cellspacing="0">
    <tr>
      <td style="padding:40px 24px;max-width:600px;margin:0 auto;display:block;">

        <!-- Logo -->
        <p style="margin:0 0 32px;font-size:18px;font-weight:700;color:#1a1a1a;">HAFS PrintQueue</p>

        <!-- Content -->
        <h1 style="margin:0 0 8px;font-size:22px;font-weight:700;">출력이 완료되었습니다</h1>
        <p style="margin:0 0 24px;color:#6b7280;">
          {user_name}님의 출력물이 준비됐어요. <br>
          출력물을 <strong>D홀 3층 물리실</strong>에서 수령해 주세요.
        </p>

        <p style="margin:0 0 6px;"><strong>파일명</strong></p>
        <p style="margin:0 0 24px;color:#6b7280;word-break:break-all;">{job_filename}</p>

        <!-- Separator -->
        <hr style="border:none;border-top:1px solid #e5e7eb;margin:0 0 24px;">

        <!-- Footer -->
        <p style="margin:0;font-size:13px;color:#6b7280;">HAFS PrintQueue · 용인한국외국어대학교부설고등학교 메이커 시스템</p>

      </td>
    </tr>
  </table>
</body>
</html>"""
    send_email(to, subject, html)


def send_approved_email(to: str, user_name: str, job_filename: str):
    subject = f"[HAFS PrintQueue] 출력 신청 승인 - {job_filename}"
    html = f"""<!DOCTYPE html>
<html lang="ko">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>출력 신청 승인</title>
</head>
<body style="margin:0;padding:0;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI','Noto Sans KR',system-ui,sans-serif;color:#1a1a1a;font-size:15px;line-height:1.6;">
  <table width="100%" cellpadding="0" cellspacing="0">
    <tr>
      <td style="padding:40px 24px;max-width:600px;margin:0 auto;display:block;">

        <p style="margin:0 0 32px;font-size:18px;font-weight:700;color:#1a1a1a;">HAFS PrintQueue</p>

        <h1 style="margin:0 0 8px;font-size:22px;font-weight:700;">출력 신청이 승인되었습니다</h1>
        <p style="margin:0 0 24px;color:#6b7280;">
          {user_name}님의 신청이 승인되어 출력 대기열에 추가되었습니다. <br>
          출력이 완료되면 다시 이메일로 알려드릴게요.
        </p>

        <p style="margin:0 0 6px;"><strong>파일명</strong></p>
        <p style="margin:0 0 24px;color:#6b7280;word-break:break-all;">{job_filename}</p>

        <hr style="border:none;border-top:1px solid #e5e7eb;margin:0 0 24px;">
        <p style="margin:0;font-size:13px;color:#6b7280;">HAFS PrintQueue · 용인한국외국어대학교부설고등학교 메이커 시스템</p>

      </td>
    </tr>
  </table>
</body>
</html>"""
    send_email(to, subject, html)


def send_rejected_email(to: str, user_name: str, job_filename: str, reason: str = ""):
    subject = f"[HAFS PrintQueue] 출력 신청 거부 - {job_filename}"
    reason_block = f"""
        <p style="margin:0 0 6px;"><strong>사유</strong></p>
        <p style="margin:0 0 24px;color:#6b7280;white-space:pre-wrap;">{escape(reason)}</p>
""" if reason else ""
    html = f"""<!DOCTYPE html>
<html lang="ko">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>출력 신청 거부</title>
</head>
<body style="margin:0;padding:0;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI','Noto Sans KR',system-ui,sans-serif;color:#1a1a1a;font-size:15px;line-height:1.6;">
  <table width="100%" cellpadding="0" cellspacing="0">
    <tr>
      <td style="padding:40px 24px;max-width:600px;margin:0 auto;display:block;">

        <p style="margin:0 0 32px;font-size:18px;font-weight:700;color:#1a1a1a;">HAFS PrintQueue</p>

        <h1 style="margin:0 0 8px;font-size:22px;font-weight:700;">출력 신청이 거부되었습니다</h1>
        <p style="margin:0 0 24px;color:#6b7280;">
          {user_name}님의 신청이 관리자에 의해 거부되었습니다.
        </p>

        <p style="margin:0 0 6px;"><strong>파일명</strong></p>
        <p style="margin:0 0 24px;color:#6b7280;word-break:break-all;">{job_filename}</p>
        {reason_block}
        <hr style="border:none;border-top:1px solid #e5e7eb;margin:0 0 24px;">
        <p style="margin:0;font-size:13px;color:#6b7280;">HAFS PrintQueue · 용인한국외국어대학교부설고등학교 메이커 시스템</p>

      </td>
    </tr>
  </table>
</body>
</html>"""
    send_email(to, subject, html)
