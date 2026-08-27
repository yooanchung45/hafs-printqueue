"""MQTT 게이트웨이의 최신 상태/AMS snapshot을 DB에 반영."""
import asyncio
import logging
from datetime import datetime, timezone
from sqlalchemy import select, delete
from models import Job, JobStatus, Printer, FilamentSlot, PrinterStatus, User
from printer_client import PrinterClient
from notifications import notify_printer_error, notify_print_completed

logger = logging.getLogger("printer_sync")

# The MQTT cache can still contain the previous print's FINISH/IDLE report
# immediately after a new start command.  Do not let that stale terminal state
# complete the new job before the printer has had time to report its new state.
STARTUP_TERMINAL_GRACE_SECONDS = 45

_STATE_MAP = {
    "IDLE": PrinterStatus.IDLE, "FINISH": PrinterStatus.IDLE,
    "RUNNING": PrinterStatus.PRINTING, "PREPARE": PrinterStatus.PRINTING,
    "PAUSE": PrinterStatus.PAUSED, "FAILED": PrinterStatus.ERROR,
}


def _utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _is_startup_terminal_report(status, started_at, now=None) -> bool:
    if not status.online or started_at is None:
        return False
    if status.state not in ("IDLE", "FINISH", "FAILED"):
        return False
    now = now or _utcnow()
    age = (now - started_at).total_seconds()
    return 0 <= age < STARTUP_TERMINAL_GRACE_SECONDS


def _printer_status(state: str, has_current_job: bool) -> PrinterStatus:
    """Map Bambu state while distinguishing an intentional stop from failure.

    Bambu reports both a user-requested stop and a real print failure as
    ``FAILED``.  The cancel route clears ``current_job_id`` before the next
    sync, which lets us treat that terminal report as idle without hiding a
    failure for a job that is still considered active.
    """
    if state == "FAILED" and not has_current_job:
        return PrinterStatus.IDLE
    return _STATE_MAP.get(state, PrinterStatus.IDLE)


def _print_finished(status, previous_status: PrinterStatus, previous_progress: int | None, has_current_job: bool, started_at=None) -> bool:
    """Recognize a completed print even after a brief MQTT disconnect."""
    if not status.online or not has_current_job:
        return False
    if _is_startup_terminal_report(status, started_at):
        return False
    if status.state == "FINISH":
        return True
    return (
        status.state == "IDLE"
        and previous_status in (PrinterStatus.PRINTING, PrinterStatus.PAUSED, PrinterStatus.OFFLINE)
        and max(previous_progress or 0, status.percentage or 0) >= 99
    )


async def sync_printer(db, printer):
    """프린터 한 대 동기화. 실패해도 예외 안 던짐."""
    client = PrinterClient(
        ip=printer.ip, access_code=printer.access_code,
        serial=printer.serial, name=printer.name,
    )
    if client.is_mock:
        return
    previous_status = printer.status
    previous_progress = printer.progress
    current_job = None
    if printer.current_job_id is not None:
        current_job = (await db.execute(
            select(Job).where(Job.id == printer.current_job_id)
        )).scalar_one_or_none()
    try:
        status = client.get_status()
    except Exception as e: 
        logger.warning("sync 실패 %s: %s", printer.name, e)
        printer.status = PrinterStatus.OFFLINE
        printer.progress = None
        await db.commit()
        return

    # 프린터 상태 갱신
    startup_terminal = _is_startup_terminal_report(
        status, current_job.started_at if current_job is not None else None
    )
    if startup_terminal:
        # Preserve the newly-recorded printing state and zero progress while
        # the gateway is still exposing the previous print's terminal cache.
        printer.status = PrinterStatus.PRINTING
        printer.progress = min(previous_progress or 0, 98)
        printer.nozzle_temp = status.nozzle_temp
        printer.bed_temp = status.bed_temp
    elif status.online:
        printer.status = _printer_status(
            status.state, has_current_job=printer.current_job_id is not None
        )
        printer.progress = status.percentage
        printer.nozzle_temp = status.nozzle_temp
        printer.bed_temp = status.bed_temp
    else:
        logger.warning("sync 실패 %s: %s", printer.name, status.error or "unknown error")
        printer.status = PrinterStatus.OFFLINE
        printer.progress = None

    if printer.status == PrinterStatus.ERROR and previous_status != PrinterStatus.ERROR:
        notify_printer_error(printer.name, status.state)

    # The printer finishes independently of the web app. Reconcile the active
    # DB job exactly once when Bambu reports FINISH (or a completed IDLE state).
    finished = _print_finished(
        status,
        previous_status,
        previous_progress,
        has_current_job=printer.current_job_id is not None,
        started_at=current_job.started_at if current_job is not None else None,
    )
    completed_job = None
    completed_owner = None
    completion_recorded = False
    if finished:
        completed_job = current_job
        if completed_job is not None and completed_job.status == JobStatus.PRINTING:
            completed_job.status = JobStatus.AWAITING_CLEAR
            completion_recorded = True
            completed_owner = (await db.execute(
                select(User).where(User.id == completed_job.user_id)
            )).scalar_one_or_none()
        # Keep current_job_id until an admin confirms that the bed is clear.
        printer.progress = None

    # AMS 슬롯 갱신 (기존 삭제 후 재삽입)
    if status.slots:
        await db.execute(delete(FilamentSlot).where(FilamentSlot.printer_id == printer.id))
        for s in status.slots:
            db.add(FilamentSlot(
                printer_id=printer.id, slot_index=s.slot_index,
                material_type=s.material_type, color_hex=s.color_hex,
                color_name=s.color_name, remaining_percent=s.remaining_percent,
                is_empty=1 if s.is_empty else 0,
            ))
    await db.commit()

    if completion_recorded:
        notify_print_completed(
            completed_job.filename,
            printer.name,
            completed_owner.name if completed_owner else None,
        )


async def sync_all(db, force_refresh=False):
    from printer_gateway import gateway

    result = await db.execute(select(Printer).order_by(Printer.id))
    printers = result.scalars().all()
    gateway.prune(p.serial for p in printers if p.serial)
    if force_refresh:
        for printer in printers:
            session = gateway.configure(
                printer.ip, printer.access_code, printer.serial, printer.name
            )
            if session:
                try:
                    session.request_full_status()
                except Exception as exc:
                    logger.warning("강제 상태 요청 실패 %s: %s", printer.name, exc)
        await asyncio.sleep(1)
    for printer in printers:
        await sync_printer(db, printer)
