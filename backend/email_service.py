"""Student email notifications styled to match the PrintQueue web app."""

import os
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import make_msgid
from html import escape
from urllib.parse import urlsplit
from uuid import uuid4

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587
SMTP_USER = os.getenv("SMTP_USER")
SMTP_PASS = os.getenv("SMTP_PASS")
APP_BASE_URL = os.getenv("APP_BASE_URL", "")
OAUTH_REDIRECT_URI = os.getenv(
    "OAUTH_REDIRECT_URI", "http://localhost:3000/api/auth/callback"
)

# Email clients do not reliably support CSS custom properties or OKLCH, so the
# web app's tokens are represented here by compatible named palette values.
_COLORS = {
    "ink": "#172033",
    "ink_2": "#48566a",
    "muted": "#526174",
    "rule": "#dbe2ea",
    "accent": "#2563eb",
    "accent_strong": "#1d4ed8",
    "accent_ink": "#ffffff",
    "danger": "#b8322e",
}
_FONT_STACK = "Arial, 'Apple SD Gothic Neo', 'Noto Sans KR', sans-serif"


def _jobs_url() -> str:
    base_url = APP_BASE_URL.rstrip("/")
    if not base_url:
        redirect = urlsplit(OAUTH_REDIRECT_URI)
        base_url = f"{redirect.scheme}://{redirect.netloc}" if redirect.netloc else ""
    return f"{base_url}/jobs" if base_url else "/jobs"


def _render_email(
    *,
    page_title: str,
    heading: str,
    message: str,
    job_filename: str,
    detail_html: str = "",
) -> str:
    colors = _COLORS
    delivery_ref = uuid4().hex
    safe_message = escape(message)
    safe_filename = escape(job_filename)
    jobs_url = escape(f"{_jobs_url()}?ref={delivery_ref}", quote=True)

    return f"""<!-- Hallmark · component: student status email · genre: modern-minimal · theme: HAFS cobalt
     pre-emit critique: P5 H5 E5 S5 R5 V4 -->
<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="color-scheme" content="light">
  <title>{escape(page_title)}</title>
  <style>
    .email-action:hover {{ background-color: {colors['accent_strong']} !important; }}
    .email-action:focus, .email-action:focus-visible {{ outline: 3px solid {colors['ink']} !important; outline-offset: 3px !important; }}
    .email-action:active {{ background-color: {colors['accent_strong']} !important; }}
    @media only screen and (max-width: 620px) {{
      .email-shell {{ padding: 20px 12px !important; }}
      .email-card {{ border-radius: 12px !important; }}
      .email-header, .email-content {{ padding-left: 20px !important; padding-right: 20px !important; }}
      .email-action {{ display: block !important; text-align: center !important; }}
    }}
  </style>
</head>
<body style="margin:0;padding:0;color:{colors['ink']};font-family:{_FONT_STACK};font-size:16px;line-height:1.6;">
  <div style="display:none;max-height:0;overflow:hidden;opacity:0;color:transparent;">{safe_message}</div>
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="width:100%;">
    <tr>
      <td class="email-shell" align="center" style="padding:48px 20px;">
        <table role="presentation" class="email-card" width="100%" cellpadding="0" cellspacing="0" border="0" style="width:100%;max-width:560px;border:1px solid {colors['rule']};border-radius:14px;">
          <tr>
            <td class="email-header" style="padding:24px 28px;border-bottom:1px solid {colors['rule']};">
              <span style="font-size:17px;font-weight:700;letter-spacing:-0.3px;color:{colors['ink']};">HAFS PrintQueue</span>
            </td>
          </tr>
          <tr>
            <td class="email-content" style="padding:32px 28px;">
              <h1 style="margin:0 0 12px;color:{colors['ink']};font-family:{_FONT_STACK};font-size:26px;line-height:1.25;letter-spacing:-0.6px;font-style:normal;">{escape(heading)}</h1>
              <p style="margin:0 0 28px;color:{colors['ink_2']};">{safe_message}</p>

              <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="width:100%;margin:0 0 16px;">
                <tr>
                  <td style="padding:16px;color:{colors['muted']};font-size:13px;font-weight:700;vertical-align:top;white-space:nowrap;">파일</td>
                  <td style="padding:16px;color:{colors['ink']};font-size:14px;font-weight:600;text-align:right;word-break:break-all;">{safe_filename}</td>
                </tr>
              </table>
              {detail_html}

              <table role="presentation" cellpadding="0" cellspacing="0" border="0" style="margin-top:28px;">
                <tr>
                  <td style="border-radius:10px;background:{colors['accent']};">
                    <a class="email-action" href="{jobs_url}" style="display:inline-block;padding:12px 20px;border-radius:10px;background:{colors['accent']};color:{colors['accent_ink']};font-size:14px;font-weight:700;line-height:1.3;text-decoration:none;white-space:nowrap;">내 작업 확인</a>
                  </td>
                </tr>
              </table>
              <span style="display:none;max-height:0;overflow:hidden;opacity:0;color:transparent;font-size:1px;line-height:1px;">message-ref:{delivery_ref}</span>
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>"""


def send_email(to: str, subject: str, html: str, text: str):
    context = ssl.create_default_context()
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = f"HAFS PrintQueue <{SMTP_USER}>"
    msg["To"] = to
    msg["Message-ID"] = make_msgid()
    msg["X-Entity-Ref-ID"] = uuid4().hex
    msg.set_content(text)
    msg.add_alternative(html, subtype="html")

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as smtp:
        smtp.starttls(context=context)
        smtp.login(user=SMTP_USER, password=SMTP_PASS)
        smtp.send_message(msg)


def send_print_done_email(to: str, user_name: str, job_filename: str, pickup_instructions: str = ""):
    subject = f"[HAFS PrintQueue] 출력 완료 - {job_filename}"
    message = f"{user_name}님의 출력물이 준비되었습니다."
    detail_html = ""
    if pickup_instructions:
        safe_instructions = escape(pickup_instructions).replace("\n", "<br>")
        detail_html = f"""
              <div style="padding:16px;word-break:break-word;">
                <span style="display:block;margin-bottom:4px;color:{_COLORS['muted']};font-size:12px;font-weight:700;">관리자 안내 및 수령 장소</span>
                <span style="display:block;color:{_COLORS['ink_2']};font-size:14px;line-height:1.55;">{safe_instructions}</span>
              </div>"""
    html = _render_email(
        page_title="출력 완료",
        heading="출력물이 준비되었습니다",
        message=message,
        job_filename=job_filename,
        detail_html=detail_html,
    )
    instructions_text = f"\n\n관리자 안내 및 수령 장소:\n{pickup_instructions}" if pickup_instructions else ""
    text = f"{message}\n\n파일: {job_filename}{instructions_text}\n내 작업 확인: {_jobs_url()}"
    send_email(to, subject, html, text)


def send_approved_email(to: str, user_name: str, job_filename: str):
    subject = f"[HAFS PrintQueue] 출력 신청 승인 - {job_filename}"
    message = f"{user_name}님의 신청이 승인되어 출력 대기열에 추가되었어요. 출력이 완료되면 다시 알려드릴게요."
    html = _render_email(
        page_title="출력 신청 승인",
        heading="출력 신청이 승인되었습니다",
        message=message,
        job_filename=job_filename,
    )
    text = f"{user_name}님의 출력 신청이 승인되어 대기열에 추가되었어요.\n\n파일: {job_filename}\n내 작업 확인: {_jobs_url()}"
    send_email(to, subject, html, text)


def send_rejected_email(to: str, user_name: str, job_filename: str, reason: str = ""):
    subject = f"[HAFS PrintQueue] 출력 신청 거부 - {job_filename}"
    message = f"{user_name}님의 출력 신청이 거부되었습니다. 아래 내용을 확인해 주세요."
    detail_html = ""
    if reason:
        safe_reason = escape(reason).replace("\n", "<br>")
        detail_html = f"""
              <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="width:100%;margin:0;">
                <tr>
                  <td style="padding:16px;">
                    <span style="display:block;margin-bottom:4px;color:{_COLORS['danger']};font-size:12px;font-weight:700;">거부 사유</span>
                    <span style="display:block;color:{_COLORS['ink_2']};font-size:14px;line-height:1.55;">{safe_reason}</span>
                  </td>
                </tr>
              </table>"""
    html = _render_email(
        page_title="출력 신청 거부",
        heading="출력 신청이 거부되었습니다",
        message=message,
        job_filename=job_filename,
        detail_html=detail_html,
    )
    reason_text = f"\n거부 사유: {reason}" if reason else ""
    text = f"{user_name}님의 출력 신청이 거부되었습니다.\n\n파일: {job_filename}{reason_text}\n내 작업 확인: {_jobs_url()}"
    send_email(to, subject, html, text)
