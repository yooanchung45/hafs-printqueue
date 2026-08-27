"""Email HTML contract tests."""

import email_service


def _capture(monkeypatch):
    sent = {}

    def fake_send(to, subject, html, text):
        sent.update(to=to, subject=subject, html=html, text=text)

    monkeypatch.setattr(email_service, "send_email", fake_send)
    return sent


def test_approved_email_matches_site_and_has_no_legacy_footer(monkeypatch):
    sent = _capture(monkeypatch)

    email_service.send_approved_email(
        "student@hafs.hs.kr", "홍길동", "모델 & 최종.3mf"
    )

    assert "HAFS PrintQueue" in sent["html"]
    assert "내 작업 확인" in sent["html"]
    assert "/jobs" in sent["html"]
    assert "용인한국외국어대학교부설고등학교 메이커 시스템" not in sent["html"]
    assert "모델 &amp; 최종.3mf" in sent["html"]


def test_rejected_email_escapes_student_content(monkeypatch):
    sent = _capture(monkeypatch)

    email_service.send_rejected_email(
        "student@hafs.hs.kr",
        "<학생>",
        "bad<script>.3mf",
        "크기 초과\n<script>alert(1)</script>",
    )

    assert "&lt;학생&gt;" in sent["html"]
    assert "bad&lt;script&gt;.3mf" in sent["html"]
    assert "<script>alert(1)</script>" not in sent["html"]
    assert "크기 초과<br>&lt;script&gt;alert(1)&lt;/script&gt;" in sent["html"]


def test_done_email_contains_pickup_location(monkeypatch):
    sent = _capture(monkeypatch)

    email_service.send_print_done_email(
        "student@hafs.hs.kr", "홍길동", "완성품.3mf"
    )

    assert "D홀 3층 물리실" in sent["html"]
    assert "D홀 3층 물리실" in sent["text"]


def test_repeated_email_sections_receive_unique_refs():
    values = {
        "page_title": "출력 승인",
        "heading": "출력 신청이 승인되었습니다",
        "message": "대기열에 추가되었습니다.",
        "job_filename": "모델.3mf",
    }

    first = email_service._render_email(**values)
    second = email_service._render_email(**values)

    assert first != second
    assert "?ref=" in first
    assert "message-ref:" in first
