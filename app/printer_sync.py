"""MQTT 게이트웨이의 최신 상태/AMS snapshot을 DB에 반영."""
import asyncio
import logging
from sqlalchemy import select, delete
from models import Printer, FilamentSlot, PrinterStatus
from printer_client import PrinterClient

logger = logging.getLogger("printer_sync")

_STATE_MAP = {
    "IDLE": PrinterStatus.IDLE, "FINISH": PrinterStatus.IDLE,
    "RUNNING": PrinterStatus.PRINTING, "PREPARE": PrinterStatus.PRINTING,
    "PAUSE": PrinterStatus.PAUSED, "FAILED": PrinterStatus.ERROR,
}


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


async def sync_printer(db, printer):
    """프린터 한 대 동기화. 실패해도 예외 안 던짐."""
    client = PrinterClient(
        ip=printer.ip, access_code=printer.access_code,
        serial=printer.serial, name=printer.name,
    )
    if client.is_mock:
        return
    try:
        status = client.get_status()
    except Exception as e: 
        logger.warning("sync 실패 %s: %s", printer.name, e)
        printer.status = PrinterStatus.OFFLINE
        printer.progress = None
        await db.commit()
        return

    # 프린터 상태 갱신
    if status.online:
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
