"""관리자 라우트.

NOTE: 프린터 상태(printer.status)는 admin 액션에서 절대 안 건드림.
- Mock 환경: 항상 OFFLINE (ip+access_code 없으니까)
- 학교 연결 후: 단일 paho MQTT 게이트웨이가 자동 갱신
"""
import asyncio
import ftplib
import logging
import os
import re
import ssl
import threading
import time
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

logger = logging.getLogger("admin")

from fastapi import APIRouter, Depends, Form, HTTPException
from fastapi.responses import FileResponse, JSONResponse, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from auth import require_admin
from db import async_session_maker, get_db
from printer_client import PrinterClient
from api_serializers import job_dict, printer_admin_dict, printer_dict, user_dict
from models import FilamentSlot, Job, JobStatus, Printer, User, PrinterStatus
from email_service import send_approved_email, send_rejected_email, send_print_done_email
from notifications import notify_print_failed, notify_print_started
from upload_cleanup import discard_job_files


_transfers: dict[str, dict] = {}
_transfer_lock = threading.RLock()
_active_printers: set[int] = set()
_FTP_CONNECT_TIMEOUT = 15
_FTP_TRANSFER_TIMEOUT = 120
_FTP_BLOCK_SIZE = 64 * 1024


def _set_transfer(transfer_id: str, **values):
    with _transfer_lock:
        if transfer_id in _transfers:
            _transfers[transfer_id].update(values)


def _transfer_snapshot(transfer_id: str):
    with _transfer_lock:
        state = _transfers.get(transfer_id)
        return dict(state) if state else None


def _utcnow():
    # TZ=Asia/Seoul makes datetime.now() return KST; always write UTC to the DB.
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _mark_print_started(job, printer):
    """Record a new print without leaking the previous print's progress."""
    job.status = JobStatus.PRINTING
    job.started_at = _utcnow()
    job.queue_position = None
    printer.status = PrinterStatus.PRINTING
    printer.current_job_id = job.id
    printer.progress = 0


async def _ensure_loaded_filament_slot(db: AsyncSession, printer_id: int, slot_index: int):
    slot = (await db.execute(
        select(FilamentSlot)
        .where(FilamentSlot.printer_id == printer_id)
        .where(FilamentSlot.slot_index == slot_index)
        .where(FilamentSlot.is_empty.is_(False))
    )).scalar_one_or_none()
    if slot is None:
        raise HTTPException(422, "선택한 필라멘트가 현재 슬롯에 없습니다. 상태를 동기화해 주세요.")


def _bambulabs_start_print(ip, access_code, serial, name, file_path, remote_name, ams_slot):
    """Legacy FTPS/print bridge; suspends the singleton MQTT session while active."""
    import time
    import bambulabs_api as _bl
    from printer_gateway import gateway

    gateway.remove(serial)
    pr = _bl.Printer(ip, access_code, serial)

    try:
        pr.mqtt_start()
        time.sleep(6)
        slot = int(ams_slot)

        # Patch AMS slot references (S0A → S{slot}A, T0 → T{slot}) when slot != 0.
        # For .gcode: simple string replace in a temp file.
        # For .3mf: repack the archive via patch_ams_slot().
        upload_path = file_path
        tmp_path = None
        if slot != 0:
            if file_path.endswith('.gcode'):
                import tempfile
                with open(file_path, 'r', encoding='utf-8', errors='replace') as gf:
                    gcode = gf.read()
                gcode = gcode.replace('M620 S0A', f'M620 S{slot}A')
                gcode = gcode.replace('M621 S0A', f'M621 S{slot}A')
                gcode = re.sub(r'(?m)^(\s*)T0(\s|$)', rf'\1T{slot}\2', gcode)
                tmp = tempfile.NamedTemporaryFile(
                    mode='w', suffix='.gcode', delete=False, encoding='utf-8'
                )
                tmp.write(gcode)
                tmp.close()
                tmp_path = upload_path = tmp.name
            elif file_path.endswith('.3mf'):
                from make_3mf import patch_ams_slot
                tmp_path = upload_path = patch_ams_slot(file_path, slot)

        try:
            with open(upload_path, "rb") as f:
                pr.upload_file(f, remote_name)
        finally:
            if tmp_path:
                os.unlink(tmp_path)
        time.sleep(2)

        ok = pr.start_print(remote_name, 1, use_ams=True, ams_mapping=[slot])
        if not ok:
            return "print_rejected"

        time.sleep(6)
        st = str(pr.get_state() or "").upper()
        if st in ("FAILED", "PAUSE"):
            return "print_paused"

        return "ok"
    finally:
        try:
            pr.mqtt_stop()
        except Exception:
            pass
        gateway.configure(ip, access_code, serial, name)


def _upload_with_progress(ip, access_code, file_path, remote_name, progress):
    """Upload over implicit FTPS, retrying broken connections from byte zero."""
    from bambulabs_api.ftp_client import ImplicitFTP_TLS

    total = max(os.path.getsize(file_path), 1)
    last_error = None
    for attempt in range(1, 4):
        ftp = ImplicitFTP_TLS(timeout=_FTP_CONNECT_TIMEOUT)
        sent = 0
        try:
            progress(sent, total, attempt)
            ftp.connect(host=ip, port=990, timeout=_FTP_CONNECT_TIMEOUT)
            ftp.login("bblp", access_code)
            ftp.prot_p()
            ftp.timeout = _FTP_TRANSFER_TIMEOUT
            if ftp.sock is not None:
                ftp.sock.settimeout(_FTP_TRANSFER_TIMEOUT)
            with open(file_path, "rb") as source:
                def on_chunk(chunk):
                    nonlocal sent
                    sent += len(chunk)
                    progress(sent, total, attempt)
                ftp.storbinary(f"STOR {remote_name}", source, blocksize=_FTP_BLOCK_SIZE, callback=on_chunk)
            return
        except (OSError, EOFError, ftplib.Error, ssl.SSLError) as exc:
            last_error = exc
            if attempt == 3:
                break
            time.sleep(0.5 * attempt)
        finally:
            try:
                ftp.close()
            except Exception:
                pass
    raise ConnectionError(f"파일 전송 중 프린터 연결이 끊겼습니다: {last_error}")


async def _run_print_transfer(transfer_id: str, job_id: int, requested_slot: int):
    """Run a printer transfer independently of the browser request."""
    initial_state = _transfer_snapshot(transfer_id) or {}
    printer_id = initial_state.get("printer_id")
    tmp_path = None
    try:
        async with async_session_maker() as db:
            job = await _get_job_or_404(db, job_id)
            printer = (await db.execute(
                select(Printer).where(Printer.id == job.printer_id)
            )).scalar_one()
            printer_id = printer.id

            from printer_gateway import gateway

            session = gateway.get(printer.serial)
            if session is None:
                session = gateway.configure(
                    printer.ip, printer.access_code, printer.serial, printer.name
                )
            slot = requested_slot

            upload_path = job.file_path
            if slot != 0:
                if upload_path.endswith(".gcode"):
                    import tempfile
                    with open(upload_path, "r", encoding="utf-8", errors="replace") as source:
                        gcode = source.read().replace("M620 S0A", f"M620 S{slot}A").replace(
                            "M621 S0A", f"M621 S{slot}A"
                        )
                        gcode = re.sub(r"(?m)^(\s*)T0(\s|$)", rf"\1T{slot}\2", gcode)
                    tmp = tempfile.NamedTemporaryFile(
                        mode="w", suffix=".gcode", delete=False, encoding="utf-8"
                    )
                    tmp.write(gcode)
                    tmp.close()
                    tmp_path = upload_path = tmp.name
                elif upload_path.endswith(".3mf"):
                    from make_3mf import patch_ams_slot
                    tmp_path = upload_path = patch_ams_slot(upload_path, slot)

            remote_name = os.path.basename(job.file_path)
            _set_transfer(transfer_id, phase="uploading", message="프린터로 파일 전송 중", progress=2)

            def report(sent, total, attempt):
                percent = min(88, 2 + round(sent / total * 86))
                suffix = f" (재연결 {attempt - 1}/2)" if attempt > 1 else ""
                _set_transfer(
                    transfer_id, progress=percent,
                    message=f"프린터로 파일 전송 중{suffix}",
                    bytes_sent=sent, bytes_total=total,
                )

            loop = asyncio.get_running_loop()
            await loop.run_in_executor(
                None, _upload_with_progress,
                printer.ip, printer.access_code, upload_path, remote_name, report,
            )

            _set_transfer(transfer_id, phase="starting", message="출력 시작 명령 전송 중", progress=92)
            session = gateway.get(printer.serial)
            if session is None or not session.snapshot().connected:
                raise ConnectionError("파일 전송 후 프린터 연결이 끊겼습니다. 파일은 전송되었지만 출력은 시작하지 않았습니다.")
            session.start_print(remote_name, slot)
            # QoS 0 publish has no broker acknowledgement. Catch an immediate
            # disconnect before recording the job as printing.
            await asyncio.sleep(0.5)
            if not session.snapshot().connected:
                raise ConnectionError("출력 시작 명령을 보내는 동안 프린터 연결이 끊겼습니다. 프린터 화면을 확인해 주세요.")

            job.ams_slot = slot
            _mark_print_started(job, printer)
            queued = (await db.execute(
                select(Job).where(Job.printer_id == printer.id)
                .where(Job.status == JobStatus.QUEUED).order_by(Job.queue_position)
            )).scalars().all()
            for position, queued_job in enumerate(queued, start=1):
                queued_job.queue_position = position
            await db.commit()

            owner = (await db.execute(select(User).where(User.id == job.user_id))).scalar_one_or_none()
            notify_print_started(job.filename, printer.name, owner.name if owner else None)
            _set_transfer(transfer_id, phase="done", message="전송 완료 · 출력을 시작했습니다", progress=100)
    except Exception as exc:
        logger.exception("백그라운드 출력 전송 실패: job=%s", job_id)
        _set_transfer(transfer_id, phase="error", message=str(exc), progress=None)
    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
        if printer_id is not None:
            with _transfer_lock:
                _active_printers.discard(printer_id)


router = APIRouter(prefix="/api/admin", tags=["admin"])


@router.get("")
async def admin_dashboard(
    user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Job)
        .where(Job.status == JobStatus.PENDING_APPROVAL)
        .order_by(Job.created_at)
    )
    pending_jobs = result.scalars().all()

    result = await db.execute(select(Printer).order_by(Printer.id))
    printers = result.scalars().all()

    printer_jobs = {}
    for p in printers:
        result = await db.execute(
            select(Job)
            .where(Job.printer_id == p.id)
            .where(Job.status.in_([JobStatus.QUEUED, JobStatus.PRINTING, JobStatus.AWAITING_CLEAR]))
            .order_by(Job.queue_position)
        )
        printer_jobs[p.id] = result.scalars().all()

    result = await db.execute(
        select(Job)
        .where(Job.status == JobStatus.FAILED)
        .where(Job.failure_acknowledged.is_(False))
        .order_by(Job.created_at.desc())
    )
    failed_jobs = result.scalars().all()
    failed_by_printer = {}
    for failed_job in failed_jobs:
        failed_by_printer.setdefault(failed_job.printer_id, []).append(failed_job)

    all_jobs = list(pending_jobs) + list(failed_jobs)
    for jobs in printer_jobs.values():
        all_jobs.extend(jobs)
    user_ids = {j.user_id for j in all_jobs}
    users = {}
    if user_ids:
        result = await db.execute(select(User).where(User.id.in_(user_ids)))
        users = {u.id: u for u in result.scalars().all()}

    slots = (await db.execute(
        select(FilamentSlot).order_by(FilamentSlot.printer_id, FilamentSlot.slot_index)
    )).scalars().all()
    slots_by_printer = {}
    for slot in slots:
        slots_by_printer.setdefault(slot.printer_id, []).append(slot)

    def serialize(job):
        return job_dict(job, owner=users.get(job.user_id))

    return {
        "user": user_dict(user),
        "pending_jobs": [serialize(job) for job in pending_jobs],
        "printers": [
            {
                **printer_admin_dict(printer, slots_by_printer.get(printer.id, [])),
                "jobs": [serialize(job) for job in printer_jobs[printer.id]],
                "failed_jobs": [
                    serialize(job) for job in failed_by_printer.get(printer.id, [])
                ],
            }
            for printer in printers
        ],
    }


async def _get_job_or_404(db: AsyncSession, job_id: int) -> Job:
    result = await db.execute(select(Job).where(Job.id == job_id))
    job = result.scalar_one_or_none()
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


async def _next_queue_position(db: AsyncSession, printer_id: int) -> int:
    result = await db.execute(
        select(Job)
        .where(Job.printer_id == printer_id)
        .where(Job.status == JobStatus.QUEUED)
        .order_by(Job.queue_position.desc())
    )
    last = result.scalars().first()
    return (last.queue_position or 0) + 1 if last else 1


async def _retry_failed_job(
    db: AsyncSession,
    job: Job,
    printer_id: int,
    *,
    at_front: bool,
) -> None:
    if at_front:
        queued = (await db.execute(
            select(Job)
            .where(Job.printer_id == printer_id)
            .where(Job.status == JobStatus.QUEUED)
            .order_by(Job.queue_position, Job.id)
        )).scalars().all()
        for position, queued_job in enumerate(queued, start=2):
            queued_job.queue_position = position
        job.queue_position = 1
    else:
        job.queue_position = await _next_queue_position(db, printer_id)

    job.printer_id = printer_id
    job.status = JobStatus.QUEUED
    job.started_at = None
    job.completed_at = None
    job.failure_acknowledged = False


async def _ensure_queue_head(db: AsyncSession, job: Job) -> None:
    """Only the first queued job may start on a printer."""
    first = (await db.execute(
        select(Job.id)
        .where(Job.printer_id == job.printer_id)
        .where(Job.status == JobStatus.QUEUED)
        .order_by(Job.queue_position, Job.id)
        .limit(1)
    )).scalar_one_or_none()
    if first != job.id:
        raise HTTPException(409, "대기열의 첫 번째 작업만 시작할 수 있습니다")


@router.post("/jobs/{job_id}/approve")
async def approve_job(
    job_id: int,
    user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    job = await _get_job_or_404(db, job_id)
    if job.status != JobStatus.PENDING_APPROVAL:
        raise HTTPException(409, "승인 대기 중인 작업이 아닙니다")

    job.status = JobStatus.QUEUED
    job.approved_at = _utcnow()
    job.queue_position = await _next_queue_position(db, job.printer_id)
    await db.commit()

    job_user_result = await db.execute(select(User).where(User.id == job.user_id))
    job_user = job_user_result.scalar_one_or_none()
    if job_user:
        try:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, send_approved_email, job_user.email, job_user.name, job.filename)
        except Exception as e:
            logger.warning("승인 이메일 발송 실패 %s: %s", job_user.email, e)

    return {"ok": True, "job": job_dict(job, owner=job_user)}


@router.post("/jobs/{job_id}/reject")
async def reject_job(
    job_id: int,
    reason: str = Form(""),
    user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    job = await _get_job_or_404(db, job_id)
    if job.status != JobStatus.PENDING_APPROVAL:
        raise HTTPException(409, "승인 대기 중인 작업이 아닙니다")

    job_user_result = await db.execute(select(User).where(User.id == job.user_id))
    job_user = job_user_result.scalar_one_or_none()

    reason = reason.strip()
    job.status = JobStatus.REJECTED
    job.admin_notes = reason or None
    await db.commit()
    discard_job_files(job.file_path)  # rejected — keep the row, drop the bytes

    if job_user:
        try:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(
                None, send_rejected_email,
                job_user.email, job_user.name, job.filename, reason,
            )
        except Exception as e:
            logger.warning("거부 이메일 발송 실패 %s: %s", job_user.email, e)

    return {"ok": True, "job": job_dict(job, owner=job_user)}


@router.post("/jobs/{job_id}/reassign")
async def reassign_job(
    job_id: int,
    printer_id: int = Form(...),
    user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    job = await _get_job_or_404(db, job_id)
    if job.status not in (JobStatus.PENDING_APPROVAL, JobStatus.QUEUED):
        raise HTTPException(409, "현재 상태에서는 프린터를 변경할 수 없습니다")

    result = await db.execute(select(Printer).where(Printer.id == printer_id))
    new_printer = result.scalar_one_or_none()
    if new_printer is None or job.printer_id == printer_id:
        raise HTTPException(422, "변경할 프린터를 확인해 주세요")

    old_printer_id = job.printer_id

    if job.status == JobStatus.QUEUED:
        job.queue_position = None
        await db.flush()
        old_q = await db.execute(
            select(Job)
            .where(Job.printer_id == old_printer_id)
            .where(Job.status == JobStatus.QUEUED)
            .order_by(Job.queue_position)
        )
        for i, j in enumerate(old_q.scalars().all(), start=1):
            j.queue_position = i
        job.queue_position = await _next_queue_position(db, printer_id)

    job.printer_id = printer_id
    await db.commit()
    return {"ok": True, "job": job_dict(job, printer=new_printer)}


@router.post("/jobs/{job_id}/start-transfer")
async def start_job_transfer(
    job_id: int,
    ams_slot: int = Form(..., ge=0),
    user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Queue a transfer and return immediately so the UI can show progress."""
    job = await _get_job_or_404(db, job_id)
    if job.status != JobStatus.QUEUED:
        return JSONResponse({"detail": "이미 시작되었거나 대기 중인 작업이 아닙니다."}, status_code=409)
    await _ensure_queue_head(db, job)
    printer = (await db.execute(
        select(Printer).where(Printer.id == job.printer_id)
    )).scalar_one_or_none()
    if printer is None:
        return JSONResponse({"detail": "프린터를 찾을 수 없습니다."}, status_code=404)
    await _ensure_loaded_filament_slot(db, printer.id, ams_slot)
    busy = (await db.execute(
        select(Job).where(Job.printer_id == printer.id)
        .where(Job.status.in_([JobStatus.PRINTING, JobStatus.AWAITING_CLEAR]))
    )).scalars().first()
    if busy is not None:
        return JSONResponse({"detail": "해당 프린터는 이미 출력 중입니다."}, status_code=409)

    if not (printer.ip and printer.access_code and printer.serial):
        return JSONResponse({"detail": "프린터 연결 정보가 없습니다."}, status_code=409)

    with _transfer_lock:
        if printer.id in _active_printers:
            return JSONResponse({"detail": "이 프린터로 이미 파일을 전송 중입니다."}, status_code=409)
        transfer_id = uuid.uuid4().hex
        _active_printers.add(printer.id)
        _transfers[transfer_id] = {
            "id": transfer_id,
            "job_id": job.id,
            "printer_id": printer.id,
            "phase": "preparing",
            "message": "파일 전송 준비 중",
            "progress": 0,
            "bytes_sent": 0,
            "bytes_total": job.file_size or 0,
        }
    asyncio.create_task(_run_print_transfer(transfer_id, job.id, ams_slot))
    return JSONResponse({"transfer_id": transfer_id}, status_code=202)


@router.post("/jobs/{job_id}/move")
async def move_queued_job(
    job_id: int,
    direction: str = Form(...),
    user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Move a queued job one position within its printer queue."""
    job = await _get_job_or_404(db, job_id)
    if job.status != JobStatus.QUEUED or direction not in ("up", "down"):
        raise HTTPException(409, "대기 중인 작업만 순서를 변경할 수 있습니다")

    queued = (await db.execute(
        select(Job)
        .where(Job.printer_id == job.printer_id)
        .where(Job.status == JobStatus.QUEUED)
        .order_by(Job.queue_position, Job.id)
    )).scalars().all()
    try:
        index = next(i for i, queued_job in enumerate(queued) if queued_job.id == job.id)
    except StopIteration:
        raise HTTPException(409, "대기열에서 작업을 찾지 못했습니다")

    target_index = index - 1 if direction == "up" else index + 1
    if 0 <= target_index < len(queued):
        queued[index], queued[target_index] = queued[target_index], queued[index]
        for position, queued_job in enumerate(queued, start=1):
            queued_job.queue_position = position
        await db.commit()
    return {"ok": True}


@router.post("/jobs/{job_id}/delete-queued")
async def delete_queued_job(
    job_id: int,
    user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Permanently remove a queued job and its stored files."""
    job = await _get_job_or_404(db, job_id)
    if job.status != JobStatus.QUEUED:
        raise HTTPException(409, "대기 중인 작업만 삭제할 수 있습니다")

    printer_id = job.printer_id
    file_path = job.file_path
    await db.delete(job)
    await db.flush()
    remaining = (await db.execute(
        select(Job)
        .where(Job.printer_id == printer_id)
        .where(Job.status == JobStatus.QUEUED)
        .order_by(Job.queue_position, Job.id)
    )).scalars().all()
    for position, queued_job in enumerate(remaining, start=1):
        queued_job.queue_position = position
    await db.commit()

    if file_path:
        path = Path(file_path)
        try:
            path.unlink(missing_ok=True)
            path.with_suffix(".stl").unlink(missing_ok=True)
        except OSError as exc:
            logger.warning("대기 작업 파일 삭제 실패 %s: %s", file_path, exc)
    return {"ok": True}


@router.get("/transfers/{transfer_id}")
async def print_transfer_status(
    transfer_id: str,
    user: User = Depends(require_admin),
):
    state = _transfer_snapshot(transfer_id)
    if state is None:
        raise HTTPException(status_code=404, detail="전송 상태를 찾을 수 없습니다.")
    return JSONResponse(state)


@router.post("/jobs/{job_id}/start")
async def start_job(
    job_id: int,
    ams_slot: int = Form(..., ge=0),
    user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """선택한 AMS 필라멘트로 큐 작업을 실제 프린터에서 시작한다."""
    job = await _get_job_or_404(db, job_id)
    if job.status != JobStatus.QUEUED:
        raise HTTPException(409, "대기 중인 작업만 시작할 수 있습니다")
    await _ensure_queue_head(db, job)

    result = await db.execute(select(Printer).where(Printer.id == job.printer_id))
    printer = result.scalar_one_or_none()
    if printer is None:
        raise HTTPException(404, "프린터를 찾을 수 없습니다")
    await _ensure_loaded_filament_slot(db, printer.id, ams_slot)

    # 이미 출력 중이면 거부 (A1은 베드 수동 비움 필요)
    busy = await db.execute(
        select(Job).where(Job.printer_id == printer.id)
        .where(Job.status.in_([JobStatus.PRINTING, JobStatus.AWAITING_CLEAR]))
    )
    if busy.scalars().first() is not None:
        raise HTTPException(409, "프린터가 사용 중입니다")

    client = PrinterClient(
        ip=printer.ip, access_code=printer.access_code,
        serial=printer.serial, name=printer.name,
    )

    # Mock(통신정보 없음) → 상태만 전환
    if client.is_mock:
        job.ams_slot = ams_slot
        _mark_print_started(job, printer)
        await db.commit()
        notify_print_started(job.filename, printer.name)
        return {"ok": True, "job": job_dict(job, printer=printer)}

    # 실제 출력: 블로킹 bambulabs 작업을 스레드 풀에서 실행
    remote_name = os.path.basename(job.file_path)
    try:
        loop = asyncio.get_running_loop()
        result_str = await loop.run_in_executor(
            None, _bambulabs_start_print,
            printer.ip, printer.access_code, printer.serial, printer.name,
            job.file_path, remote_name, ams_slot,
        )
    except Exception:
        logger.exception(
            "출력 시작 실패: printer=%s job=%s file=%s",
            printer.name,
            job.id,
            job.file_path,
        )
        raise HTTPException(502, "프린터에서 출력을 시작하지 못했습니다")

    if result_str != "ok":
        raise HTTPException(502, result_str)

    job.ams_slot = ams_slot
    _mark_print_started(job, printer)
    await db.commit()
    _owner_result = await db.execute(select(User).where(User.id == job.user_id))
    _owner = _owner_result.scalar_one_or_none()
    notify_print_started(job.filename, printer.name, _owner.name if _owner else None)

    # Renumber remaining queued jobs for this printer
    q_res = await db.execute(
        select(Job)
        .where(Job.printer_id == printer.id)
        .where(Job.status == JobStatus.QUEUED)
        .order_by(Job.queue_position)
    )
    for i, j in enumerate(q_res.scalars().all(), start=1):
        j.queue_position = i
    await db.commit()

    return {"ok": True, "job": job_dict(job, owner=_owner, printer=printer)}


@router.post("/jobs/{job_id}/complete")
async def complete_job(
    job_id: int,
    user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """관리자가 베드 비움을 확인한 뒤 작업을 최종 완료하고 학생에게 알림."""
    job = await _get_job_or_404(db, job_id)
    if job.status != JobStatus.AWAITING_CLEAR:
        raise HTTPException(409, "베드 정리 대기 중인 작업이 아닙니다")

    job.status = JobStatus.COMPLETED
    job.completed_at = _utcnow()
    # 이 job이 프린터의 현재 작업으로 박혀있으면 비움 (cleared on complete)
    _pres = await db.execute(select(Printer).where(Printer.id == job.printer_id))
    _printer = _pres.scalar_one_or_none()
    if _printer is not None and _printer.current_job_id == job.id:
        _printer.current_job_id = None
    await db.commit()

    # 남은 큐 재정렬
    result = await db.execute(
        select(Job)
        .where(Job.printer_id == job.printer_id)
        .where(Job.status == JobStatus.QUEUED)
        .order_by(Job.queue_position)
    )
    remaining = result.scalars().all()
    for i, j in enumerate(remaining, start=1):
        j.queue_position = i
    await db.commit()

    # The user email means the bed has actually been cleared and the print is
    # ready for pickup, so it is sent only on this explicit admin action.
    job_user_result = await db.execute(select(User).where(User.id == job.user_id))
    job_user = job_user_result.scalar_one_or_none()
    if job_user is not None:
        try:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(
                None, send_print_done_email,
                job_user.email, job_user.name, job.filename,
            )
        except Exception as e:
            logger.warning("완료 이메일 발송 실패 %s: %s", job_user.email, e)

    return {"ok": True, "job": job_dict(job, owner=job_user, printer=_printer)}


@router.post("/jobs/{job_id}/fail")
async def fail_job(
    job_id: int,
    user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """출력 실패. Job 상태만 변경, 프린터 상태는 안 건드림."""
    job = await _get_job_or_404(db, job_id)
    if job.status != JobStatus.PRINTING:
        raise HTTPException(409, "출력 중인 작업이 아닙니다")

    job.status = JobStatus.FAILED
    job.completed_at = _utcnow()
    job.failure_acknowledged = False
    _pres = await db.execute(select(Printer).where(Printer.id == job.printer_id))
    _printer = _pres.scalar_one_or_none()
    if _printer is not None and _printer.current_job_id == job.id:
        _printer.current_job_id = None
    await db.commit()
    notify_print_failed(
        job.filename,
        _printer.name if _printer is not None else f"프린터 #{job.printer_id}",
    )

    return {"ok": True, "job": job_dict(job, printer=_printer)}


@router.post("/jobs/{job_id}/retry")
async def retry_failed_job(
    job_id: int,
    printer_id: int | None = Form(None),
    at_front: bool = Form(False),
    user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """관리자가 확인한 실패 작업을 지정 프린터 대기열로 되돌림."""
    job = await _get_job_or_404(db, job_id)
    if job.status != JobStatus.FAILED:
        raise HTTPException(409, "실패한 작업만 다시 대기시킬 수 있습니다")
    if not job.file_path or not Path(job.file_path).exists():
        raise HTTPException(410, "원본 파일이 없습니다")

    target_printer_id = printer_id if printer_id is not None else job.printer_id
    target_printer = (await db.execute(
        select(Printer).where(Printer.id == target_printer_id)
    )).scalar_one_or_none()
    if target_printer is None:
        raise HTTPException(422, "대상 프린터를 확인해 주세요")

    await _retry_failed_job(
        db,
        job,
        target_printer_id,
        at_front=at_front,
    )
    await db.commit()
    return {"ok": True, "job": job_dict(job, printer=target_printer)}


@router.post("/jobs/{job_id}/dismiss-failure")
async def dismiss_failed_job(
    job_id: int,
    user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """운영 화면에서 실패를 확인 처리하되 기록과 파일은 보존함."""
    job = await _get_job_or_404(db, job_id)
    if job.status != JobStatus.FAILED:
        raise HTTPException(409, "실패한 작업만 확인 처리할 수 있습니다")
    job.failure_acknowledged = True
    await db.commit()
    return {"ok": True, "job": job_dict(job)}


@router.post("/jobs/clear-failed")
async def clear_failed_jobs(
    user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """실패/거부된 작업 전체 삭제 (DB 레코드 + 디스크 파일)."""
    result = await db.execute(
        select(Job).where(Job.status.in_([JobStatus.FAILED, JobStatus.REJECTED]))
    )
    jobs = result.scalars().all()
    for job in jobs:
        if job.file_path:
            p = Path(job.file_path)
            p.unlink(missing_ok=True)
            p.with_suffix(".stl").unlink(missing_ok=True)
        await db.delete(job)
    await db.commit()
    return {"ok": True, "deleted": len(jobs)}


@router.post("/jobs/{job_id}/requeue")
async def requeue_job(
    job_id: int,
    user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """실패/거부/취소된 작업을 승인 대기로 되돌림 (파일이 남아있으면)."""
    job = await _get_job_or_404(db, job_id)
    if job.status not in (JobStatus.FAILED, JobStatus.REJECTED, JobStatus.CANCELED):
        raise HTTPException(409, "이 작업은 다시 대기시킬 수 없습니다")
    if not job.file_path or not Path(job.file_path).exists():
        raise HTTPException(410, "원본 파일이 없습니다")
    job.status = JobStatus.PENDING_APPROVAL
    job.completed_at = None
    job.queue_position = None
    await db.commit()
    return {"ok": True, "job": job_dict(job)}


@router.get("/jobs/{job_id}/stl")
async def admin_stl_preview(
    job_id: int,
    user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """원본 STL을 Three.js 미리보기용으로 서빙."""
    job = await _get_job_or_404(db, job_id)
    stl_path = Path(job.file_path).with_suffix(".stl")
    if not stl_path.exists():
        raise HTTPException(status_code=404, detail="STL 미리보기를 사용할 수 없습니다 (직접 업로드된 .3mf거나 이미 삭제됨)")
    return FileResponse(str(stl_path), media_type="application/octet-stream")


@router.get("/jobs/{job_id}/3mf-thumb")
async def admin_3mf_thumb(
    job_id: int,
    user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Bambu Studio .gcode.3mf 안의 plate_1.png 썸네일을 반환."""
    job = await _get_job_or_404(db, job_id)
    if not job.file_path or not Path(job.file_path).exists():
        raise HTTPException(status_code=404, detail="파일을 찾을 수 없습니다")
    try:
        with zipfile.ZipFile(job.file_path) as z:
            for candidate in ("Metadata/plate_1.png", "Metadata/top_1.png", "Metadata/pick_1.png"):
                if candidate in z.namelist():
                    return Response(content=z.read(candidate), media_type="image/png")
    except Exception:
        pass
    raise HTTPException(status_code=404, detail="썸네일을 찾을 수 없습니다")


@router.post("/jobs/{job_id}/cancel")
async def cancel_job(
    job_id: int,
    user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """출력 중인 작업을 프린터에서 즉시 중단하고 취소로 표시."""
    job = await _get_job_or_404(db, job_id)
    if job.status != JobStatus.PRINTING:
        raise HTTPException(409, "출력 중인 작업이 아닙니다")

    result = await db.execute(select(Printer).where(Printer.id == job.printer_id))
    printer = result.scalar_one_or_none()

    # Send stop command to printer
    if printer and printer.ip and printer.access_code and printer.serial:
        client = PrinterClient(
            ip=printer.ip,
            access_code=printer.access_code,
            serial=printer.serial,
            name=printer.name,
        )
        loop = asyncio.get_running_loop()
        stopped = await loop.run_in_executor(None, client.stop)
        if not stopped:
            raise HTTPException(502, "프린터에 출력 중단 명령을 보내지 못했습니다")

        # Reset printer status
        printer.status = PrinterStatus.IDLE
        printer.current_job_id = None
        printer.progress = None

    # An intentional administrator stop is a cancellation, not a print failure.
    job.status = JobStatus.CANCELED
    job.completed_at = _utcnow()
    job.failure_acknowledged = True
    await db.commit()
    discard_job_files(job.file_path)  # print stopped — file already sent to the printer

    return {"ok": True, "job": job_dict(job, printer=printer)}


@router.post("/printers/{printer_id}/stop")
async def stop_printer(
    printer_id: int,
    user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Stop a printer directly and cancel its linked active job, if present."""
    printer = (await db.execute(
        select(Printer).where(Printer.id == printer_id)
    )).scalar_one_or_none()
    if printer is None:
        raise HTTPException(404, "프린터를 찾을 수 없습니다")
    if not (printer.ip and printer.access_code and printer.serial):
        raise HTTPException(409, "프린터 연결 정보가 없습니다")

    client = PrinterClient(
        ip=printer.ip,
        access_code=printer.access_code,
        serial=printer.serial,
        name=printer.name,
    )
    loop = asyncio.get_running_loop()
    stopped = await loop.run_in_executor(None, client.stop)
    if not stopped:
        raise HTTPException(502, "프린터에 출력 중단 명령을 보내지 못했습니다")

    job = None
    if printer.current_job_id is not None:
        job = (await db.execute(
            select(Job).where(Job.id == printer.current_job_id)
        )).scalar_one_or_none()
    if job is None:
        job = (await db.execute(
            select(Job)
            .where(Job.printer_id == printer.id)
            .where(Job.status == JobStatus.PRINTING)
            .order_by(Job.started_at.desc(), Job.id.desc())
        )).scalars().first()
    canceled_file = None
    if job is not None and job.status == JobStatus.PRINTING:
        job.status = JobStatus.CANCELED
        job.completed_at = _utcnow()
        job.failure_acknowledged = True
        printer.current_job_id = None
        canceled_file = job.file_path

    printer.status = PrinterStatus.IDLE
    printer.progress = None
    await db.commit()
    discard_job_files(canceled_file)
    return {
        "ok": True,
        "printer": printer_admin_dict(printer),
        "job": job_dict(job, printer=printer) if job is not None else None,
    }


# ============================================================
# 보고서 (PDF + Excel)
# ============================================================

@router.get("/reports")
async def reports_page(
    year: int | None = None,
    month: int | None = None,
    user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Monthly report data for the admin report screen."""
    from reports import gather_report_data, _status_korean

    now = datetime.now()
    year_options = list(range(2024, now.year + 2))
    month_options = list(range(1, 13))
    view_year = year or now.year
    view_month = month or now.month
    if view_year not in year_options or not 1 <= view_month <= 12:
        raise HTTPException(422, "조회 연월이 올바르지 않습니다")
    report = await gather_report_data(db, view_year, view_month)
    return {
        "user": user_dict(user),
        "year_options": year_options,
        "month_options": month_options,
        "view_year": view_year,
        "view_month": view_month,
        "stats": report["stats"],
        "printer_stats": list(report["printer_stats"].values()),
        "top_users": report["top_users"],
        "jobs": [
            {
                **job_dict(
                    job,
                    owner=report["users"].get(job.user_id),
                    printer=report["printers"].get(job.printer_id),
                ),
                "status_label": _status_korean(job.status.value),
            }
            for job in report["jobs"]
        ],
    }

@router.get("/reports/excel")
async def download_excel(
    year: int,
    month: int,
    user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Excel 다운로드."""
    from reports import gather_report_data, generate_excel

    data = await gather_report_data(db, year, month)
    excel_bytes = generate_excel(data)

    filename = f"PrintQueue_{year}-{month:02d}_데이터.xlsx"
    encoded = quote(filename)

    return Response(
        content=excel_bytes,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{encoded}",
        },
    )


@router.post("/printers/add")
async def add_printer(
    user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
    name: str = Form(...),
    serial: str = Form(...),
    ip: str = Form(...),
    access_code: str = Form(...),
):
    """새 프린터 등록. 통신 정보(ip/access_code/serial) 모두 필수."""
    printer = Printer(
        name=name.strip(),
        serial=serial.strip(),
        ip=ip.strip(),
        access_code=access_code.strip(),
    )
    db.add(printer)
    await db.commit()
    await db.refresh(printer)
    return {"ok": True, "printer": printer_admin_dict(printer)}


@router.post("/printers/{printer_id}/edit")
async def edit_printer(
    printer_id: int,
    user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
    name: str = Form(...),
    serial: str = Form(...),
    ip: str = Form(...),
    access_code: str = Form(...),
):
    """기존 프린터 정보 수정."""
    result = await db.execute(select(Printer).where(Printer.id == printer_id))
    printer = result.scalar_one_or_none()
    if printer is None:
        raise HTTPException(status_code=404, detail="프린터를 찾을 수 없습니다")
    printer.name = name.strip()
    printer.serial = serial.strip()
    printer.ip = ip.strip()
    printer.access_code = access_code.strip()
    await db.commit()
    return {"ok": True, "printer": printer_admin_dict(printer)}


@router.post("/printers/{printer_id}/delete")
async def delete_printer(
    printer_id: int,
    user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """프린터 삭제. 연결된 작업이 있으면 거부 (기록 보호)."""
    result = await db.execute(select(Printer).where(Printer.id == printer_id))
    printer = result.scalar_one_or_none()
    if printer is None:
        raise HTTPException(status_code=404, detail="프린터를 찾을 수 없습니다")

    # 연결된 작업 확인
    result = await db.execute(select(Job).where(Job.printer_id == printer_id))
    linked_jobs = result.scalars().all()
    if linked_jobs:
        raise HTTPException(409, "출력 기록이 있는 프린터는 삭제할 수 없습니다")

    await db.delete(printer)
    await db.commit()
    return {"ok": True}


@router.post("/printers/sync")
async def sync_printers(
    user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """모든 프린터 실제 상태/AMS를 읽어 DB 갱신."""
    from printer_sync import sync_all
    await sync_all(db, force_refresh=True)
    return {"ok": True}


@router.post("/printers/{printer_id}/light")
async def set_printer_light(
    printer_id: int,
    on: int = Form(...),   # 1 = on, 0 = off
    user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Printer).where(Printer.id == printer_id))
    printer = result.scalar_one_or_none()
    if printer is None:
        raise HTTPException(404, "프린터를 찾을 수 없습니다")
    client = PrinterClient(ip=printer.ip, access_code=printer.access_code,
                           serial=printer.serial, name=printer.name)
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, client.set_light, bool(on))
    return {"ok": True, "on": bool(on)}


@router.get("/jobs/{job_id}/download")
async def download_job_file(
    job_id: int,
    user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """gcode / STL 파일 다운로드."""
    job = await _get_job_or_404(db, job_id)
    if not job.file_path or not os.path.exists(job.file_path):
        raise HTTPException(status_code=404, detail="파일을 찾을 수 없습니다.")
    return FileResponse(
        path=job.file_path,
        filename=job.filename,
        media_type="application/octet-stream",
    )


@router.get("/printers/status")
async def printers_status_api(
    user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """JSON: 모든 프린터의 현재 상태 (JS 폴링용)."""
    result = await db.execute(select(Printer).order_by(Printer.id))
    return [printer_dict(printer) for printer in result.scalars().all()]
