"""프린터 실제 상태/AMS를 읽어 DB(FilamentSlot, Printer.status)에 반영."""
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
        return

    # 프린터 상태
    if status.online:
        printer.status = _STATE_MAP.get(status.state, PrinterStatus.IDLE)
        printer.progress = status.percentage
        printer.nozzle_temp = status.nozzle_temp
        printer.bed_temp = status.bed_temp
    else:
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


async def sync_all(db):
    result = await db.execute(select(Printer).order_by(Printer.id))
    for printer in result.scalars().all():
        await sync_printer(db, printer)


def _capture_snapshot(printer):
    """프린터 카메라 한 프레임을 /app/static/cam{id}.jpg로 저장. 실패해도 조용히."""
    import base64, time as _t
    try:
        import bambulabs_api as _bl
    except ImportError:
        return False
    if not (printer.ip and printer.access_code and printer.serial):
        return False
    p = None
    try:
        p = _bl.Printer(printer.ip, printer.access_code, printer.serial)
        p.mqtt_start()
        p.camera_start()
        _t.sleep(6)
        frame = p.get_camera_frame()
        data = None
        if frame:
            if isinstance(frame, (bytes, bytearray)):
                data = bytes(frame)
            else:
                try:
                    data = base64.b64decode(frame)
                except Exception:
                    data = None
        if data:
            with open(f"/app/static/cam{printer.id}.jpg", "wb") as fh:
                fh.write(data)
            logger.info("snapshot 저장: cam%s.jpg (%d bytes)", printer.id, len(data))
            return True
        return False
    except Exception as e:
        logger.warning("snapshot 실패 %s: %s", printer.name, e)
        return False
    finally:
        if p is not None:
            try: p.camera_stop()
            except Exception: pass
            try: p.mqtt_stop()
            except Exception: pass


async def capture_all_snapshots(db):
    """모든 프린터 카메라 스냅샷 캡처."""
    result = await db.execute(select(Printer).order_by(Printer.id))
    loop = asyncio.get_running_loop()
    for printer in result.scalars().all():
        await loop.run_in_executor(None, _capture_snapshot, printer)
