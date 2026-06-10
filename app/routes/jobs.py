"""작업(Job) 관련 라우트.

- GET  /upload:              업로드 선택 페이지 (STL vs 슬라이싱된 파일)
- POST /upload:              슬라이싱된 파일 (.3mf/.gcode) 직접 제출
- POST /upload/stl-preview:  STL 파일들 임시 저장 → 미리보기 페이지로
- GET  /upload/stl-serve/{id}: Three.js가 STL 파일을 fetch하는 엔드포인트
- POST /upload/stl-confirm:  미리보기 확인 후 Job 생성
- GET  /jobs:                본인 작업 목록
- POST /jobs/{job_id}/cancel: 본인 작업 취소 (승인 대기·큐 대기 상태만)
"""
import os
import re
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List

from auth import require_user
from config import settings
from db import get_db
from models import Job, JobStatus, Printer, User


router = APIRouter()
templates = Jinja2Templates(directory="templates")

ALLOWED_EXTENSIONS = {".3mf", ".gcode"}
ALLOWED_STL = {".stl"}
MAX_FILE_SIZE = 200 * 1024 * 1024  # 200MB


# ── GET /upload ───────────────────────────────────────────────────────────────

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
        "error": request.query_params.get("error"),
    })


# ── POST /upload  (sliced file — existing flow) ───────────────────────────────

@router.post("/upload")
async def upload_submit(
    request: Request,
    printer_id: int = Form(...),
    user_notes: str = Form(""),
    file: UploadFile = File(...),
    user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    if not file.filename:
        return RedirectResponse(url="/upload?error=no_filename", status_code=302)

    ext = Path(file.filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        return RedirectResponse(url="/upload?error=invalid_extension", status_code=302)

    result = await db.execute(select(Printer).where(Printer.id == printer_id))
    if result.scalar_one_or_none() is None:
        return RedirectResponse(url="/upload?error=invalid_printer", status_code=302)

    safe_name = f"{uuid.uuid4().hex}{ext}"
    upload_dir = Path(settings.UPLOAD_DIR)
    upload_dir.mkdir(parents=True, exist_ok=True)
    file_path = upload_dir / safe_name

    total_size = 0
    with open(file_path, "wb") as f:
        while chunk := await file.read(1024 * 1024):
            total_size += len(chunk)
            if total_size > MAX_FILE_SIZE:
                file_path.unlink(missing_ok=True)
                return RedirectResponse(url="/upload?error=file_too_large", status_code=302)
            f.write(chunk)

    job = Job(
        user_id=user.id, printer_id=printer_id,
        filename=file.filename, file_path=str(file_path),
        file_size=total_size, status=JobStatus.PENDING_APPROVAL,
        user_notes=user_notes.strip() or None,
    )
    db.add(job)
    await db.commit()
    return RedirectResponse(url="/jobs", status_code=303)


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


# ── POST /upload/stl-confirm ──────────────────────────────────────────────────

@router.post("/upload/stl-confirm")
async def stl_confirm(
    request: Request,
    file_ids: List[str] = Form(...),
    filenames: List[str] = Form(...),
    printer_id: int = Form(...),
    user_notes: str = Form(""),
    # Transform params — one value per file (same order as file_ids)
    scales: List[float] = Form(...),
    rotations_x: List[float] = Form(...),
    rotations_y: List[float] = Form(...),
    rotations_z: List[float] = Form(...),
    user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    """Create one Job per STL file, baking in transforms then slicing to gcode."""
    from stl_transform import apply_transform
    from slicer import slice_stl, SlicingError
    import asyncio

    result = await db.execute(select(Printer).where(Printer.id == printer_id))
    if result.scalar_one_or_none() is None:
        return RedirectResponse(url="/upload?error=invalid_printer", status_code=302)

    notes = user_notes.strip() or None
    loop = asyncio.get_event_loop()
    slicing_errors = []

    for i, (temp_id, original_name) in enumerate(zip(file_ids, filenames)):
        if not re.match(r'^[a-f0-9]{32}\.stl$', temp_id):
            continue

        file_path = Path(settings.UPLOAD_DIR) / temp_id
        if not file_path.exists():
            continue

        # 1. Apply transform
        scale = scales[i]      if i < len(scales)      else 1.0
        rot_x = rotations_x[i] if i < len(rotations_x) else 0.0
        rot_y = rotations_y[i] if i < len(rotations_y) else 0.0
        rot_z = rotations_z[i] if i < len(rotations_z) else 0.0

        transformed_path = await loop.run_in_executor(
            None, apply_transform, str(file_path), scale, rot_x, rot_y, rot_z
        )
        if transformed_path != str(file_path):
            file_path.unlink(missing_ok=True)

        # 2. Slice STL to gcode
        try:
            gcode_path = await slice_stl(transformed_path)
            Path(transformed_path).unlink(missing_ok=True)
            final_path = gcode_path
            final_name = Path(original_name).stem + ".gcode"
        except SlicingError as e:
            slicing_errors.append(f"{original_name}: {e.message}")
            final_path = transformed_path
            final_name = original_name

        job = Job(
            user_id=user.id,
            printer_id=printer_id,
            filename=final_name,
            file_path=final_path,
            file_size=Path(final_path).stat().st_size,
            status=JobStatus.PENDING_APPROVAL,
            user_notes=notes,
        )
        db.add(job)

    await db.commit()

    if slicing_errors:
        import urllib.parse
        msg = urllib.parse.quote(" | ".join(slicing_errors)[:200])
        return RedirectResponse(url=f"/jobs?slicing_error={msg}", status_code=303)

    return RedirectResponse(url="/jobs", status_code=303)


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
