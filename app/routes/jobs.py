"""작업(Job) 관련 라우트.

- GET  /upload:              업로드 선택 페이지 (STL vs 슬라이싱된 파일)
- POST /upload:              슬라이싱된 파일 (.3mf/.gcode) 직접 제출
- POST /upload/stl-preview:  STL 파일들 임시 저장 → 미리보기 페이지로
- GET  /upload/stl-serve/{id}: Three.js가 STL 파일을 fetch하는 엔드포인트
- POST /upload/stl-confirm:  미리보기 확인 후 Job 생성
- GET  /jobs:                본인 작업 목록
- POST /jobs/{job_id}/cancel: 본인 작업 취소 (승인 대기·큐 대기 상태만)
"""
import re
import uuid
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, Response, HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List

from auth import require_user
from config import settings
from db import get_db
from models import Job, JobStatus, Printer, User


import filters as _filters

router = APIRouter()
templates = Jinja2Templates(directory="templates")
_filters.register(templates)

ALLOWED_SLICED_SUFFIX = ".gcode.3mf"
ALLOWED_STL = {".stl"}
MAX_FILE_SIZE = 200 * 1024 * 1024  # 200MB


# ── Smart printer picker ──────────────────────────────────────────────────────

async def pick_best_printer(db: AsyncSession) -> Printer:
    """Return the printer with the fewest active jobs, preferring online ones."""
    result = await db.execute(select(Printer).order_by(Printer.id))
    printers = result.scalars().all()
    if not printers:
        raise HTTPException(500, "등록된 프린터가 없습니다")

    load_counts = {}
    for p in printers:
        count_result = await db.execute(
            select(func.count(Job.id))
            .where(Job.printer_id == p.id)
            .where(Job.status.in_([JobStatus.PROCESSING, JobStatus.PENDING_APPROVAL, JobStatus.QUEUED, JobStatus.PRINTING]))
        )
        load_counts[p.id] = count_result.scalar_one()

    healthy = [p for p in printers if p.status.value not in ("offline", "error")]
    candidates = healthy if healthy else printers
    return min(candidates, key=lambda p: load_counts[p.id])


# ── GET /upload ───────────────────────────────────────────────────────────────

async def _queue_counts(db: AsyncSession, printers) -> dict:
    """Return {printer_id: active_job_count} in one query."""
    if not printers:
        return {}
    res = await db.execute(
        select(Job.printer_id, func.count(Job.id))
        .where(Job.status.in_([JobStatus.PROCESSING, JobStatus.PENDING_APPROVAL, JobStatus.QUEUED, JobStatus.PRINTING]))
        .group_by(Job.printer_id)
    )
    raw = dict(res.all())
    return {p.id: raw.get(p.id, 0) for p in printers}


@router.get("/upload", response_class=HTMLResponse)
async def upload_page(
    request: Request,
    user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Printer).order_by(Printer.id))
    printers = result.scalars().all()
    return templates.TemplateResponse("upload.html", {
        "request": request, "user": user, "printers": printers,
        "queue_counts": await _queue_counts(db, printers),
        "error": request.query_params.get("error"),
    })


# ── POST /upload  (sliced file — existing flow) ───────────────────────────────

@router.post("/upload")
async def upload_submit(
    request: Request,
    printer_id: str = Form(...),
    user_notes: str = Form(""),
    files: List[UploadFile] = File(...),
    user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    files = [f for f in files if f.filename]
    if not files:
        return RedirectResponse(url="/upload?error=no_files", status_code=302)

    for f in files:
        if not f.filename.lower().endswith(ALLOWED_SLICED_SUFFIX):
            return RedirectResponse(url="/upload?error=invalid_extension", status_code=302)

    if printer_id == "auto":
        printer = await pick_best_printer(db)
    else:
        try:
            pid = int(printer_id)
        except ValueError:
            return RedirectResponse(url="/upload?error=invalid_printer", status_code=302)
        result = await db.execute(select(Printer).where(Printer.id == pid))
        printer = result.scalar_one_or_none()
        if printer is None:
            return RedirectResponse(url="/upload?error=invalid_printer", status_code=302)

    notes = user_notes.strip() or None
    upload_dir = Path(settings.UPLOAD_DIR)
    upload_dir.mkdir(parents=True, exist_ok=True)

    for file in files:
        ext = Path(file.filename).suffix.lower()
        file_path = upload_dir / f"{uuid.uuid4().hex}{ext}"
        total_size = 0
        with open(file_path, "wb") as fh:
            while chunk := await file.read(1024 * 1024):
                total_size += len(chunk)
                if total_size > MAX_FILE_SIZE:
                    file_path.unlink(missing_ok=True)
                    return RedirectResponse(url="/upload?error=file_too_large", status_code=302)
                fh.write(chunk)
        db.add(Job(
            user_id=user.id, printer_id=printer.id,
            filename=file.filename, file_path=str(file_path),
            file_size=total_size, status=JobStatus.PENDING_APPROVAL,
            user_notes=notes,
        ))

    await db.commit()
    return RedirectResponse(url="/jobs?submitted=1", status_code=303)


# ── POST /upload/stl-preview ──────────────────────────────────────────────────

@router.post("/upload/stl-preview")
async def stl_preview(
    request: Request,
    files: List[UploadFile] = File(...),
    user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    """Receive STL files, save them temporarily, redirect to preview page."""
    if not files or all(not f.filename for f in files):
        return RedirectResponse(url="/upload?error=no_files", status_code=302)

    upload_dir = Path(settings.UPLOAD_DIR)
    upload_dir.mkdir(parents=True, exist_ok=True)

    saved = []
    for f in files:
        if not f.filename:
            continue
        ext = Path(f.filename).suffix.lower()
        if ext not in ALLOWED_STL:
            continue

        temp_id = uuid.uuid4().hex + ".stl"
        file_path = upload_dir / temp_id
        total_size = 0
        with open(file_path, "wb") as out:
            while chunk := await f.read(1024 * 1024):
                total_size += len(chunk)
                if total_size > MAX_FILE_SIZE:
                    file_path.unlink(missing_ok=True)
                    break
                out.write(chunk)

        if file_path.exists():
            saved.append({"temp_id": temp_id, "original_name": f.filename})

    if not saved:
        return RedirectResponse(url="/upload?error=no_stl_files", status_code=302)

    result = await db.execute(select(Printer).order_by(Printer.id))
    printers = result.scalars().all()

    return templates.TemplateResponse("stl_preview.html", {
        "request": request,
        "user": user,
        "files": saved,
        "printers": printers,
        "queue_counts": await _queue_counts(db, printers),
    })


# ── GET /upload/stl-serve/{temp_id} ──────────────────────────────────────────

@router.get("/upload/stl-serve/{temp_id}")
async def stl_serve(
    temp_id: str,
    user: User = Depends(require_user),
):
    """Serve the STL file to Three.js in the browser."""
    # Security: only allow UUID hex + .stl
    if not re.match(r'^[a-f0-9]{32}\.stl$', temp_id):
        raise HTTPException(400, "Invalid file id")

    file_path = Path(settings.UPLOAD_DIR) / temp_id
    if not file_path.exists():
        raise HTTPException(404, "File not found")

    return FileResponse(str(file_path), media_type="application/octet-stream")


# ── Background slicer ─────────────────────────────────────────────────────────

async def _slice_job_bg(job_id: int, stl_path: str, original_name: str):
    """Runs after response is sent: slices STL → gcode, updates job record."""
    from slicer import slice_stl, SlicingError
    from db import async_session_maker

    async with async_session_maker() as db:
        result = await db.execute(select(Job).where(Job.id == job_id))
        job = result.scalar_one_or_none()
        if job is None:
            return
        try:
            final_path, estimated_minutes = await slice_stl(stl_path)
            job.file_path = final_path
            job.filename = Path(original_name).stem + Path(final_path).suffix
            job.file_size = Path(final_path).stat().st_size
            job.estimated_minutes = estimated_minutes
        except SlicingError as e:
            job.admin_notes = f"[슬라이싱 실패] {e.message}"
        except Exception as e:
            job.admin_notes = f"[슬라이싱 오류] {type(e).__name__}: {e}"
        finally:
            job.status = JobStatus.PENDING_APPROVAL
            await db.commit()


# ── POST /upload/stl-confirm ──────────────────────────────────────────────────

@router.post("/upload/stl-confirm")
async def stl_confirm(
    background_tasks: BackgroundTasks,
    file_ids: List[str] = Form(...),
    filenames: List[str] = Form(...),
    printer_id: str = Form(...),
    user_notes: str = Form(""),
    scales: List[float] = Form(...),
    rotations_x: List[float] = Form(...),
    rotations_y: List[float] = Form(...),
    rotations_z: List[float] = Form(...),
    user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    """Apply transforms, create job as PROCESSING, slice in background."""
    from stl_transform import apply_transform
    import asyncio

    if printer_id == "auto":
        printer = await pick_best_printer(db)
    else:
        try:
            pid = int(printer_id)
        except ValueError:
            return RedirectResponse(url="/upload?error=invalid_printer", status_code=302)
        result = await db.execute(select(Printer).where(Printer.id == pid))
        printer = result.scalar_one_or_none()
        if printer is None:
            return RedirectResponse(url="/upload?error=invalid_printer", status_code=302)

    notes = user_notes.strip() or None
    loop = asyncio.get_event_loop()
    pending = []  # (job_id, stl_path, original_name) to slice after commit

    for i, (temp_id, original_name) in enumerate(zip(file_ids, filenames)):
        if not re.match(r'^[a-f0-9]{32}\.stl$', temp_id):
            continue
        file_path = Path(settings.UPLOAD_DIR) / temp_id
        if not file_path.exists():
            continue

        scale = scales[i]      if i < len(scales)      else 1.0
        rot_x = rotations_x[i] if i < len(rotations_x) else 0.0
        rot_y = rotations_y[i] if i < len(rotations_y) else 0.0
        rot_z = rotations_z[i] if i < len(rotations_z) else 0.0

        stl_path = await loop.run_in_executor(
            None, apply_transform, str(file_path), scale, rot_x, rot_y, rot_z
        )
        if stl_path != str(file_path):
            file_path.unlink(missing_ok=True)

        job = Job(
            user_id=user.id,
            printer_id=printer.id,
            filename=original_name,
            file_path=stl_path,
            file_size=Path(stl_path).stat().st_size,
            status=JobStatus.PROCESSING,
            user_notes=notes,
        )
        db.add(job)
        await db.flush()  # populate job.id before commit
        pending.append((job.id, stl_path, original_name))

    await db.commit()

    for job_id, stl_path, original_name in pending:
        background_tasks.add_task(_slice_job_bg, job_id, stl_path, original_name)

    return RedirectResponse(url="/jobs?submitted=1", status_code=303)


# ── GET /printers ─────────────────────────────────────────────────────────────

@router.get("/printers")
async def printers_status():
    return RedirectResponse(url="/", status_code=301)


# ── GET /jobs ─────────────────────────────────────────────────────────────────

@router.get("/jobs", response_class=HTMLResponse)
async def my_jobs(
    request: Request,
    user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Job).where(Job.user_id == user.id).order_by(Job.created_at.desc())
    )
    jobs = result.scalars().all()

    printer_ids = {j.printer_id for j in jobs}
    printers = {}
    if printer_ids:
        result = await db.execute(select(Printer).where(Printer.id.in_(printer_ids)))
        printers = {p.id: p for p in result.scalars().all()}

    return templates.TemplateResponse("my_jobs.html", {
        "request": request, "user": user, "jobs": jobs, "printers": printers,
        "submitted": request.query_params.get("submitted") == "1",
    })


# ── POST /jobs/{job_id}/cancel ────────────────────────────────────────────────

@router.post("/jobs/{job_id}/cancel")
async def cancel_job(
    job_id: int,
    user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Job).where(Job.id == job_id))
    job = result.scalar_one_or_none()
    if job is None:
        raise HTTPException(status_code=404)
    if job.user_id != user.id:
        raise HTTPException(status_code=403)
    if job.status not in (JobStatus.PENDING_APPROVAL, JobStatus.QUEUED):
        return RedirectResponse(url="/jobs", status_code=303)

    was_queued = job.status == JobStatus.QUEUED
    printer_id = job.printer_id
    job.status = JobStatus.CANCELED
    job.queue_position = None
    await db.commit()

    if was_queued:
        result = await db.execute(
            select(Job)
            .where(Job.printer_id == printer_id)
            .where(Job.status == JobStatus.QUEUED)
            .order_by(Job.queue_position)
        )
        for i, j in enumerate(result.scalars().all(), start=1):
            j.queue_position = i
        await db.commit()

    return RedirectResponse(url="/jobs", status_code=303)

@router.get("/jobs/{job_id}/stl-preview")
async def job_stl_file(
    job_id: int,
    _: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    """Serve the original STL for Three.js preview (owner only)."""
    result = await db.execute(select(Job).where(Job.id == job_id))
    job = result.scalar_one_or_none()
    if job is None:
        raise HTTPException(status_code=404)
    stl_path = Path(job.file_path).with_suffix(".stl")
    if not stl_path.exists():
        raise HTTPException(status_code=404, detail="STL not available")

    return FileResponse(str(stl_path), media_type="application/octet-stream")


@router.get("/jobs/{job_id}/thumb")
async def job_thumb(
    job_id: int,
    _: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    """Extract the PNG thumbnail from a Bambu Studio .gcode.3mf (owner only)."""
    import zipfile

    result = await db.execute(select(Job).where(Job.id == job_id))
    job = result.scalar_one_or_none()
    if job is None:
        raise HTTPException(status_code=404)
    if not job.file_path or not Path(job.file_path).exists():
        raise HTTPException(status_code=404)

    try:
        with zipfile.ZipFile(job.file_path) as z:
            for candidate in ("Metadata/plate_1.png", "Metadata/top_1.png"):
                if candidate in z.namelist():
                    return Response(
                        content=z.read(candidate),
                        media_type="image/png"
                    )
    except Exception:
        pass

    raise HTTPException(status_code=404, detail="No thumbnail found")