"""관리자 라우트.

NOTE: 프린터 상태(printer.status)는 admin 액션에서 절대 안 건드림.
- Mock 환경: 항상 OFFLINE (ip+access_code 없으니까)
- 학교 연결 후: bambulabs_api가 주기적 MQTT ping으로 자동 갱신
"""
import asyncio
import logging
import os
from datetime import datetime, timezone

logger = logging.getLogger("admin")

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from auth import require_admin
from db import get_db
from printer_client import PrinterClient
from models import Job, JobStatus, Printer, User, PrinterStatus
from email_service import send_approved_email, send_rejected_email, send_print_done_email


def _utcnow():
    # TZ=Asia/Seoul makes datetime.now() return KST; always write UTC to the DB.
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _bambulabs_start_print(ip, access_code, serial, file_path, remote_name, ams_slot):
    """Sync bambulabs operations. Returns 'ok' or an error key string."""
    import time
    import bambulabs_api as _bl
    from printer_client import _first_loaded_slot

    pr = _bl.Printer(ip, access_code, serial)
    try:
        pr.mqtt_start()
        time.sleep(6)

        if ams_slot is not None and str(ams_slot).strip() != "":
            slot = int(ams_slot)
        else:
            slot = _first_loaded_slot(pr.mqtt_dump())

        if slot is None:
            return "no_filament"

        # Patch AMS slot references (S0A → S{slot}A) when slot != 0.
        # For .gcode: simple string replace in a temp file.
        # For .3mf: repack the archive via patch_ams_slot().
        upload_path = file_path
        tmp_path = None
        if slot != 0:
            import os as _os
            if file_path.endswith('.gcode'):
                import tempfile
                with open(file_path, 'r', encoding='utf-8', errors='replace') as gf:
                    gcode = gf.read()
                gcode = gcode.replace('M620 S0A', f'M620 S{slot}A')
                gcode = gcode.replace('M621 S0A', f'M621 S{slot}A')
                tmp = tempfile.NamedTemporaryFile(
                    mode='w', suffix='.gcode', delete=False, encoding='utf-8'
                )
                tmp = tempfile.NamedTemp
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
                _os.unlink(tmp_path)
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


import filters as _filters

router = APIRouter(prefix="/admin")
templates = Jinja2Templates(directory="templates")
_filters.register(templates)


@router.get("", response_class=HTMLResponse)
async def admin_dashboard(
    request: Request,
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
            .where(Job.status.in_([JobStatus.QUEUED, JobStatus.PRINTING]))
            .order_by(Job.queue_position)
        )
        printer_jobs[p.id] = result.scalars().all()

    result = await db.execute(
        select(Job)
        .where(Job.status == JobStatus.CANCELED)
        .order_by(Job.created_at.desc())
    )
    canceled_jobs = result.scalars().all()

    result = await db.execute(
        select(Job)
        .where(Job.status.in_([JobStatus.FAILED, JobStatus.REJECTED]))
        .order_by(Job.created_at.desc())
        .limit(50)
    )
    failed_jobs = result.scalars().all()

    all_jobs = list(pending_jobs) + canceled_jobs + list(failed_jobs)
    for jobs in printer_jobs.values():
        all_jobs.extend(jobs)
    user_ids = {j.user_id for j in all_jobs}
    users = {}
    if user_ids:
        result = await db.execute(select(User).where(User.id.in_(user_ids)))
        users = {u.id: u for u in result.scalars().all()}

    return templates.TemplateResponse(
        "admin.html",
        {
            "request": request,
            "user": user,
            "pending_jobs": pending_jobs,
            "printers": printers,
            "printer_jobs": printer_jobs,
            "canceled_jobs": canceled_jobs,
            "failed_jobs": failed_jobs,
            "users": users,
            "error": request.query_params.get("error"),
        },
    )


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


@router.post("/jobs/{job_id}/approve")
async def approve_job(
    job_id: int,
    user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    job = await _get_job_or_404(db, job_id)
    if job.status != JobStatus.PENDING_APPROVAL:
        return RedirectResponse(url="/admin", status_code=303)

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

    return RedirectResponse(url="/admin", status_code=303)


@router.post("/jobs/{job_id}/reject")
async def reject_job(
    job_id: int,
    reason: str = Form(""),
    user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    job = await _get_job_or_404(db, job_id)
    if job.status != JobStatus.PENDING_APPROVAL:
        return RedirectResponse(url="/admin", status_code=303)

    job_user_result = await db.execute(select(User).where(User.id == job.user_id))
    job_user = job_user_result.scalar_one_or_none()

    reason = reason.strip()
    job.status = JobStatus.REJECTED
    job.admin_notes = reason or None
    await db.commit()

    if job_user:
        try:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(
                None, send_rejected_email,
                job_user.email, job_user.name, job.filename, reason,
            )
        except Exception as e:
            logger.warning("거부 이메일 발송 실패 %s: %s", job_user.email, e)

    return RedirectResponse(url="/admin", status_code=303)


@router.post("/jobs/{job_id}/reassign")
async def reassign_job(
    job_id: int,
    printer_id: int = Form(...),
    user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    job = await _get_job_or_404(db, job_id)
    if job.status not in (JobStatus.PENDING_APPROVAL, JobStatus.QUEUED):
        return RedirectResponse(url="/admin", status_code=303)

    result = await db.execute(select(Printer).where(Printer.id == printer_id))
    new_printer = result.scalar_one_or_none()
    if new_printer is None or job.printer_id == printer_id:
        return RedirectResponse(url="/admin", status_code=303)

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
    return RedirectResponse(url="/admin", status_code=303)


@router.post("/jobs/{job_id}/start")
async def start_job(
    job_id: int,
    ams_slot: str = Form(None),
    user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """큐 작업을 실제 프린터로 출력 시작.
    - AMS에서 필라멘트 든 슬롯을 자동 탐색해 매핑 (슬롯 위치 프린터마다 달라도 대응)
    - 시작 명령 후 상태 재확인: 진짜 진행 중일 때만 PRINTING 기록
    """
    job = await _get_job_or_404(db, job_id)
    if job.status != JobStatus.QUEUED:
        return RedirectResponse(url="/admin?error=job_not_queued", status_code=302)

    result = await db.execute(select(Printer).where(Printer.id == job.printer_id))
    printer = result.scalar_one_or_none()
    if printer is None:
        return RedirectResponse(url="/admin?error=printer_not_found", status_code=302)

    # 이미 출력 중이면 거부 (A1은 베드 수동 비움 필요)
    busy = await db.execute(
        select(Job).where(Job.printer_id == printer.id)
        .where(Job.status == JobStatus.PRINTING)
    )
    if busy.scalars().first() is not None:
        return RedirectResponse(url="/admin?error=printer_busy", status_code=302)

    client = PrinterClient(
        ip=printer.ip, access_code=printer.access_code,
        serial=printer.serial, name=printer.name,
    )

    # Mock(통신정보 없음) → 상태만 전환
    if client.is_mock:
        job.status = JobStatus.PRINTING
        job.started_at = _utcnow()
        printer.current_job_id = job.id
        await db.commit()
        return RedirectResponse(url="/admin?ok=mock_started", status_code=302)

    # 실제 출력: 블로킹 bambulabs 작업을 스레드 풀에서 실행
    remote_name = os.path.basename(job.file_path)
    try:
        loop = asyncio.get_running_loop()
        result_str = await loop.run_in_executor(
            None, _bambulabs_start_print,
            printer.ip, printer.access_code, printer.serial,
            job.file_path, remote_name, ams_slot,
        )
    except Exception:
        return RedirectResponse(url="/admin?error=print_error", status_code=302)

    if result_str != "ok":
        return RedirectResponse(url=f"/admin?error={result_str}", status_code=302)

    job.status = JobStatus.PRINTING
    job.started_at = _utcnow()
    job.queue_position = None
    printer.status = PrinterStatus.PRINTING
    printer.current_job_id = job.id
    await db.commit()

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

    return RedirectResponse(url="/admin?ok=started", status_code=302)


@router.post("/jobs/{job_id}/complete")
async def complete_job(
    job_id: int,
    user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """출력 완료. 큐 재정렬만, 프린터 상태는 안 건드림."""
    job = await _get_job_or_404(db, job_id)
    if job.status != JobStatus.PRINTING:
        return RedirectResponse(url="/admin", status_code=303)

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

    # 완료 이메일 발송
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

    return RedirectResponse(url="/admin", status_code=303)


@router.post("/jobs/{job_id}/fail")
async def fail_job(
    job_id: int,
    user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """출력 실패. Job 상태만 변경, 프린터 상태는 안 건드림."""
    job = await _get_job_or_404(db, job_id)
    if job.status != JobStatus.PRINTING:
        return RedirectResponse(url="/admin", status_code=303)

    job.status = JobStatus.FAILED
    job.completed_at = _utcnow()
    _pres = await db.execute(select(Printer).where(Printer.id == job.printer_id))
    _printer = _pres.scalar_one_or_none()
    if _printer is not None and _printer.current_job_id == job.id:
        _printer.current_job_id = None
    await db.commit()

    return RedirectResponse(url="/admin", status_code=303)


@router.post("/jobs/clear-failed")
async def clear_failed_jobs(
    user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """실패/거부된 작업 전체 삭제 (DB 레코드 + 디스크 파일)."""
    from pathlib import Path as _Path
    result = await db.execute(
        select(Job).where(Job.status.in_([JobStatus.FAILED, JobStatus.REJECTED]))
    )
    jobs = result.scalars().all()
    for job in jobs:
        if job.file_path:
            p = _Path(job.file_path)
            p.unlink(missing_ok=True)
            p.with_suffix(".stl").unlink(missing_ok=True)
        await db.delete(job)
    await db.commit()
    return RedirectResponse(url="/admin", status_code=303)


@router.post("/jobs/{job_id}/requeue")
async def requeue_job(
    job_id: int,
    user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """실패/거부/취소된 작업을 승인 대기로 되돌림 (파일이 남아있으면)."""
    from pathlib import Path as _Path
    job = await _get_job_or_404(db, job_id)
    if job.status not in (JobStatus.FAILED, JobStatus.REJECTED, JobStatus.CANCELED):
        return RedirectResponse(url="/admin", status_code=303)
    if not job.file_path or not _Path(job.file_path).exists():
        return RedirectResponse(url="/admin?error=file_missing", status_code=302)
    job.status = JobStatus.PENDING_APPROVAL
    job.completed_at = None
    job.queue_position = None
    await db.commit()
    return RedirectResponse(url="/admin", status_code=303)


@router.get("/jobs/{job_id}/stl")
async def admin_stl_preview(
    job_id: int,
    user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """원본 STL을 Three.js 미리보기용으로 서빙."""
    from pathlib import Path as _Path
    job = await _get_job_or_404(db, job_id)
    stl_path = _Path(job.file_path).with_suffix(".stl")
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
    import zipfile
    from pathlib import Path as _Path
    job = await _get_job_or_404(db, job_id)
    if not job.file_path or not _Path(job.file_path).exists():
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
    """출력 중인 작업을 프린터에서 즉시 중단하고 실패로 표시."""
    from printer_client import PrinterClient

    job = await _get_job_or_404(db, job_id)
    if job.status != JobStatus.PRINTING:
        return RedirectResponse(url="/admin?error=not_printing", status_code=302)

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
        import asyncio
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, client.stop)

        # Reset printer status
        printer.status = PrinterStatus.IDLE
        printer.current_job_id = None
        printer.progress = None

    # Mark job as failed
    job.status = JobStatus.FAILED
    job.completed_at = _utcnow()
    await db.commit()

    return RedirectResponse(url="/admin?ok=cancelled", status_code=302)


# ============================================================
# 보고서 (PDF + Excel)
# ============================================================

@router.get("/reports", response_class=HTMLResponse)
async def reports_page(
    request: Request,
    user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """보고서 페이지. ?year=&month= 있으면 그 달 표 같이 표시."""
    from datetime import datetime
    from reports import gather_report_data, _status_korean

    now = datetime.now()
    year_options = list(range(2024, now.year + 2))
    month_options = list(range(1, 13))

    try:
        view_year = int(request.query_params.get("view_year", ""))
        view_month = int(request.query_params.get("view_month", ""))
    except (ValueError, TypeError):
        view_year = None
        view_month = None

    # 쿼리 없으면 현재 월 자동
    if not view_year or not view_month:
        view_year = now.year
        view_month = now.month
    report = await gather_report_data(db, view_year, view_month)

    return templates.TemplateResponse(
        "admin_reports.html",
        {
            "request": request,
            "user": user,
            "year_options": year_options,
            "month_options": month_options,
            "default_year": now.year,
            "view_year": view_year,
            "view_month": view_month,
            "report": report,
            "status_kr": _status_korean,
            "default_month": now.month,
        },
    )

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
    from urllib.parse import quote
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
    return RedirectResponse(url="/admin", status_code=302)


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
    return RedirectResponse(url="/admin", status_code=302)


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
        return RedirectResponse(
            url="/admin?error=printer_has_jobs", status_code=302
        )

    await db.delete(printer)
    await db.commit()
    return RedirectResponse(url="/admin", status_code=302)


@router.post("/printers/sync")
async def sync_printers(
    user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """모든 프린터 실제 상태/AMS를 읽어 DB 갱신."""
    from printer_sync import sync_all
    await sync_all(db)
    return RedirectResponse(url="/admin", status_code=302)


@router.post("/printers/snapshot")
async def snapshot_printers(
    user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """모든 프린터 카메라 스냅샷 캡처 (느림 - 프린터당 ~10초)."""
    from printer_sync import capture_all_snapshots
    await capture_all_snapshots(db)
    return RedirectResponse(url="/admin", status_code=302)


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
        return RedirectResponse(url="/admin", status_code=302)
    from printer_client import PrinterClient
    client = PrinterClient(ip=printer.ip, access_code=printer.access_code,
                           serial=printer.serial, name=printer.name)
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, client.set_light, bool(on))
    return RedirectResponse(url="/admin", status_code=302)


@router.get("/jobs/{job_id}/download")
async def download_job_file(
    job_id: int,
    user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """gcode / STL 파일 다운로드."""
    import os
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
    from fastapi.responses import JSONResponse
    result = await db.execute(select(Printer).order_by(Printer.id))
    return JSONResponse([
        {
            "id": p.id,
            "status": p.status.value,
            "progress": p.progress,
            "nozzle_temp": p.nozzle_temp,
            "bed_temp": p.bed_temp,
        }
        for p in result.scalars().all()
    ])
