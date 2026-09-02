"""JSON-safe serializers shared by the FastAPI route modules."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _print_progress(job) -> int | None:
    """Elapsed-vs-estimate progress (0–99) for a printing job. Time-based: the
    slicer's minute estimate is the steadiest signal and it counts up on its
    own between polls. 100 only lands when the status actually flips."""
    if job.status.value != "printing" or job.started_at is None or not job.estimated_minutes:
        return None
    started = job.started_at
    if started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)
    elapsed_min = (datetime.now(timezone.utc) - started).total_seconds() / 60.0
    return max(0, min(99, round(elapsed_min / job.estimated_minutes * 100)))


def user_dict(user) -> dict[str, Any]:
    return {
        "id": user.id,
        "email": user.email,
        "name": user.name,
        "role": user.role.value,
        "created_at": _iso(user.created_at),
    }


def printer_dict(printer, slots=None) -> dict[str, Any]:
    payload = {
        "id": printer.id,
        "name": printer.name,
        "serial": printer.serial,
        "ip": printer.ip,
        "has_access_code": bool(printer.access_code),
        "status": printer.status.value,
        "current_job_id": printer.current_job_id,
        "progress": printer.progress,
        "nozzle_temp": printer.nozzle_temp,
        "bed_temp": printer.bed_temp,
        "created_at": _iso(printer.created_at),
    }
    if slots is not None:
        payload["slots"] = [slot_dict(slot) for slot in slots]
    return payload


def printer_admin_dict(printer, slots=None) -> dict[str, Any]:
    payload = printer_dict(printer, slots)
    payload["access_code"] = printer.access_code
    return payload


def slot_dict(slot) -> dict[str, Any]:
    # Stored names can be stale until the next MQTT sync.  Prefer the current
    # classifier so an already-saved hex value is always labelled correctly.
    from printer_client import _color_name

    return {
        "id": slot.id,
        "printer_id": slot.printer_id,
        "slot_index": slot.slot_index,
        "material_type": slot.material_type,
        "color_hex": slot.color_hex,
        "color_name": _color_name(slot.color_hex) or slot.color_name,
        "remaining_percent": slot.remaining_percent,
        "is_empty": bool(slot.is_empty),
        "updated_at": _iso(slot.updated_at),
    }


def job_dict(job, *, owner=None, printer=None) -> dict[str, Any]:
    payload = {
        "id": job.id,
        "user_id": job.user_id,
        "printer_id": job.printer_id,
        "filename": job.filename,
        "file_size": job.file_size,
        "status": job.status.value,
        "queue_position": job.queue_position,
        "ams_slot": job.ams_slot,
        "estimated_minutes": job.estimated_minutes,
        "progress": _print_progress(job),
        "created_at": _iso(job.created_at),
        "approved_at": _iso(job.approved_at),
        "started_at": _iso(job.started_at),
        "completed_at": _iso(job.completed_at),
        "user_notes": job.user_notes,
        "admin_notes": job.admin_notes,
        "failure_acknowledged": bool(job.failure_acknowledged),
        "preview_kind": "image" if job.filename.lower().endswith(".gcode.3mf") else "stl",
    }
    if owner is not None:
        payload["owner"] = user_dict(owner)
    if printer is not None:
        payload["printer"] = {"id": printer.id, "name": printer.name}
    return payload


def post_dict(post, *, author=None, comment_count=None) -> dict[str, Any]:
    payload = {
        "id": post.id,
        "title": post.title,
        "body": post.body,
        "category": post.category.value,
        "author_id": post.author_id,
        "pinned": bool(post.pinned),
        "created_at": _iso(post.created_at),
        "updated_at": _iso(post.updated_at),
    }
    if author is not None:
        payload["author"] = user_dict(author)
    if comment_count is not None:
        payload["comment_count"] = comment_count
    return payload


def comment_dict(comment, *, author=None, depth=0) -> dict[str, Any]:
    payload = {
        "id": comment.id,
        "post_id": comment.post_id,
        "parent_id": comment.parent_id,
        "author_id": comment.author_id,
        "body": comment.body,
        "created_at": _iso(comment.created_at),
        "depth": depth,
    }
    if author is not None:
        payload["author"] = user_dict(author)
    return payload


def attachment_dict(attachment) -> dict[str, Any]:
    return {
        "id": attachment.id,
        "post_id": attachment.post_id,
        "original_name": attachment.original_name,
        "mime_type": attachment.mime_type,
        "url": f"/api/board/attachments/{attachment.id}",
    }
