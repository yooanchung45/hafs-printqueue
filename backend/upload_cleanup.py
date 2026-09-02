"""Upload directory housekeeping.

Historically the app never deleted STL / 3MF files on its own: a rejected or
canceled job kept its bytes on disk forever, and so did every completed print.
On the Raspberry Pi's SD card that eventually fills the partition and new
uploads start failing with a bare 500.

Files for jobs that will never print again (rejected, or canceled before they
started) are deleted the moment the handler runs. The periodic sweep (also once
at startup, to reclaim the backlog) then removes: completed prints, retryable
jobs past their grace window, and orphan files with no job row.
"""
import asyncio
import logging
import re
import time
from datetime import timezone
from pathlib import Path

from sqlalchemy import select

from config import settings
from models import Job, JobStatus

logger = logging.getLogger("upload_cleanup")

# Swept unconditionally — the file will never be needed again.
DISCARD_NOW = (JobStatus.REJECTED, JobStatus.COMPLETED)
# Retryable: a FAILED job can be re-queued, and a print an admin stopped mid-run
# can be reset on the printer and reprinted from the same file. Keep the file
# this long so that stays possible, then let the sweep reclaim it.
RETRYABLE_GRACE = 3 * 24 * 60 * 60

SWEEP_INTERVAL = 6 * 60 * 60          # seconds between sweeps
ORPHAN_MIN_AGE = 24 * 60 * 60        # don't touch a job-less file younger than this
_UUID_NAME = re.compile(r"^[0-9a-f]{32}\.")


def _age_seconds(job) -> float:
    ref = job.completed_at or job.started_at or job.created_at
    if ref is None:
        return float("inf")
    if ref.tzinfo is None:
        ref = ref.replace(tzinfo=timezone.utc)
    return time.time() - ref.timestamp()


def _should_discard(job) -> bool:
    if job.status in DISCARD_NOW:
        return True
    if job.status == JobStatus.FAILED:
        return _age_seconds(job) > RETRYABLE_GRACE
    if job.status == JobStatus.CANCELED:
        # Stopped mid-print -> keep for the grace window so it can be reprinted.
        # Canceled before it ever started printing -> dead weight, drop it now.
        if job.started_at is None:
            return True
        return _age_seconds(job) > RETRYABLE_GRACE
    return False


def _siblings(file_path: str) -> set[Path]:
    """The artifact plus the .stl / .gcode that slicing leaves beside it."""
    path = Path(file_path)
    return {path, path.with_suffix(".stl"), path.with_suffix(".gcode")}


def discard_job_files(file_path: str | None) -> int:
    """Delete a job's artifact and its slicing siblings. Safe to call with a
    missing path or missing files. Returns bytes freed."""
    if not file_path:
        return 0
    freed = 0
    for candidate in _siblings(file_path):
        try:
            freed += candidate.stat().st_size
        except OSError:
            continue
        try:
            candidate.unlink(missing_ok=True)
        except OSError as exc:
            logger.warning("파일 삭제 실패 %s: %s", candidate, exc)
            freed -= candidate.stat().st_size if candidate.exists() else 0
    return freed


async def _sweep_once(db_maker) -> None:
    upload_dir = Path(settings.UPLOAD_DIR)
    if not upload_dir.is_dir():
        return

    keep: set[str] = set()
    freed = 0
    async with db_maker() as db:
        jobs = (await db.execute(select(Job))).scalars().all()
    for job in jobs:
        if not job.file_path:
            continue
        if _should_discard(job):
            freed += discard_job_files(job.file_path)
        else:
            keep.update(str(p) for p in _siblings(job.file_path))

    cutoff = time.time() - ORPHAN_MIN_AGE
    for entry in upload_dir.iterdir():
        if not entry.is_file() or str(entry) in keep or not _UUID_NAME.match(entry.name):
            continue
        try:
            if entry.stat().st_mtime > cutoff:
                continue
            freed += entry.stat().st_size
            entry.unlink(missing_ok=True)
        except OSError as exc:
            logger.warning("고아 파일 삭제 실패 %s: %s", entry, exc)

    if freed:
        logger.info("업로드 정리: %.1f MB 확보", freed / 1024 / 1024)


async def cleanup_loop(db_maker) -> None:
    """Sweep once now (clears the backlog) then every SWEEP_INTERVAL."""
    while True:
        try:
            await _sweep_once(db_maker)
        except Exception as exc:
            logger.warning("업로드 정리 오류: %s", exc)
        await asyncio.sleep(SWEEP_INTERVAL)
