"""Best-effort Slack notifications via an Incoming Webhook.

Notification delivery is intentionally fire-and-forget: Slack being slow or
unavailable must never make a print queue operation fail.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Iterable
from zoneinfo import ZoneInfo

import httpx

from config import settings


logger = logging.getLogger("notifications")
_tasks: set[asyncio.Task] = set()
_SEOUL = ZoneInfo("Asia/Seoul")


def _escape(value: object) -> str:
    """Prevent user-supplied names/titles from becoming Slack mrkdwn links."""
    return str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


async def _post(payload: dict) -> None:
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.post(settings.SLACK_WEBHOOK_URL, json=payload)
            response.raise_for_status()
    except Exception as exc:
        logger.warning("Slack notification failed: %s", exc)


def _send(title: str, fields: Iterable[tuple[str, object]], path: str = "") -> None:
    if not settings.SLACK_NOTIFICATIONS_ENABLED or not settings.SLACK_WEBHOOK_URL:
        return

    lines = [f"*{title}*"]
    lines.extend(
        f"*{_escape(label)}:* {_escape(value)}"
        for label, value in fields
        if value not in (None, "")
    )
    lines.append(f"*시각:* {datetime.now(_SEOUL).strftime('%Y-%m-%d %H:%M:%S')}")
    if path and settings.APP_BASE_URL:
        lines.append(f"<{settings.APP_BASE_URL.rstrip('/')}{path}|사이트에서 확인>")
    message = "\n".join(lines)
    payload = {
        "text": title,
        "blocks": [{"type": "section", "text": {"type": "mrkdwn", "text": message}}],
    }

    try:
        task = asyncio.get_running_loop().create_task(_post(payload))
    except RuntimeError:
        logger.warning("Slack notification skipped: no running event loop")
        return
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


def notify_new_jobs(user_name: str, filenames: list[str], printer_name: str) -> None:
    names = ", ".join(filenames)
    _send("새 출력 신청", [("신청자", user_name), ("파일", names), ("프린터", printer_name)])


def notify_print_started(filename: str, printer_name: str, user_name: str | None = None) -> None:
    _send("출력 시작", [("프린터", printer_name), ("작업", filename), ("신청자", user_name)])


def notify_print_completed(filename: str, printer_name: str, user_name: str | None = None) -> None:
    _send("출력 완료", [("프린터", printer_name), ("작업", filename), ("신청자", user_name)])


def notify_print_failed(filename: str, printer_name: str) -> None:
    _send("출력 실패", [("프린터", printer_name), ("작업", filename)])


def notify_printer_error(printer_name: str, state: str) -> None:
    _send("프린터 오류", [("프린터", printer_name), ("상태", state)])


def notify_new_question(author_name: str, title: str, post_id: int) -> None:
    _send("새 질문 게시글", [("작성자", author_name), ("제목", title)], f"/board/{post_id}")
