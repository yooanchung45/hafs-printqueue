"""Student-facing print job API routes."""
import asyncio
import logging
import re
import uuid
import zipfile
from pathlib import Path
from typing import List

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api_serializers import job_dict, printer_dict
from auth import require_user
from config import settings
from db import get_db
from models import FilamentSlot, Job, JobStatus, Printer, User, UserRole
from notifications import notify_new_jobs


logger = logging.getLogger("jobs")

router = APIRouter(prefix="/api", tags=["jobs"])
ALLOWED_SLICED_SUFFIX = ".gcode.3mf"
ALLOWED_STL = {".stl"}
MAX_FILE_SIZE = 100 * 1024 * 1024


async def pick_best_printer(db: AsyncSession) -> Printer:
    printers = (await db.execute(select(Printer).order_by(Printer.id))).scalars().all()
    if not printers:
        raise HTTPException(500, "등록된 프린터가 없습니다")
    load_counts = {}
    active = [
        JobStatus.PROCESSING,
        JobStatus.PENDING_APPROVAL,
        JobStatus.QUEUED,
        JobStatus.PRINTING,
        JobStatus.AWAITING_CLEAR,
    ]
    for printer in printers:
        count = await db.execute(
            select(func.count(Job.id))
            .where(Job.printer_id == printer.id)
            .where(Job.status.in_(active))
        )
        load_counts[printer.id] = count.scalar_one()
    healthy = [p for p in printers if p.status.value not in ("offline", "error")]
    return min(healthy or printers, key=lambda p: load_counts[p.id])


async def resolve_printer(db: AsyncSession, printer_id: str) -> Printer:
    """빈 값이면 자동 배정, 아니면 학생이 고른 프린터를 그대로 사용."""
    if printer_id and printer_id.strip():
        try:
            printer = (
                await db.execute(select(Printer).where(Printer.id == int(printer_id)))
            ).scalar_one_or_none()
        except ValueError:
            printer = None
        if printer is not None:
            return printer
    return await pick_best_printer(db)


def _parse_ams_slot(ams_slot: str) -> int | None:
    """빈 값이면 선호 없음 — 관리자가 출력 시작할 때 직접 고른다."""
    return int(ams_slot) if ams_slot and ams_slot.strip() else None


async def _printers_payload(db: AsyncSession) -> list[dict]:
    """업로드 페이지의 프린터/필라멘트 선택 UI용 — 대기열 수와 현재 로드된
    슬롯을 함께 내려줘서, 특정 프린터를 고르면 그 프린터의 색상만 보여줄 수
    있게 한다."""
    printers = (await db.execute(select(Printer).order_by(Printer.id))).scalars().all()
    counts = await _queue_counts(db, printers)
    slots_by_printer: dict[int, list] = {}
    if printers:
        rows = (
            await db.execute(
                select(FilamentSlot).where(
                    FilamentSlot.printer_id.in_([p.id for p in printers])
                )
            )
        ).scalars().all()
        for slot in rows:
            slots_by_printer.setdefault(slot.printer_id, []).append(slot)
    return [
        {
            **printer_dict(printer, slots=slots_by_printer.get(printer.id, [])),
            "queue_count": counts[printer.id],
        }
        for printer in printers
    ]


async def _queue_counts(db: AsyncSession, printers) -> dict[int, int]:
    if not printers:
        return {}
    result = await db.execute(
        select(Job.printer_id, func.count(Job.id))
        .where(
            Job.status.in_(
                [
                    JobStatus.PROCESSING,
                    JobStatus.PENDING_APPROVAL,
                    JobStatus.QUEUED,
                    JobStatus.PRINTING,
                    JobStatus.AWAITING_CLEAR,
                ]
            )
        )
        .group_by(Job.printer_id)
    )
    raw = dict(result.all())
    return {printer.id: raw.get(printer.id, 0) for printer in printers}


@router.get("/upload")
async def upload_options(
    _: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    return {
        "printers": await _printers_payload(db),
        "limits": {"max_file_bytes": MAX_FILE_SIZE, "attachment_bytes": 10 * 1024 * 1024},
    }


@router.post("/upload")
async def upload_submit(
    user_notes: str = Form(""),
    printer_id: str = Form(""),
    ams_slot: str = Form(""),
    files: List[UploadFile] = File(...),
    user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    files = [file for file in files if file.filename]
    if not files:
        raise HTTPException(422, "파일을 선택해 주세요")
    if any(not file.filename.lower().endswith(ALLOWED_SLICED_SUFFIX) for file in files):
        raise HTTPException(422, ".gcode.3mf 파일만 업로드할 수 있습니다")

    printer = await resolve_printer(db, printer_id)
    requested_slot = _parse_ams_slot(ams_slot)
    notes = user_notes.strip() or None
    upload_dir = Path(settings.UPLOAD_DIR)
    upload_dir.mkdir(parents=True, exist_ok=True)
    jobs = []
    for file in files:
        extension = Path(file.filename).suffix.lower()
        file_path = upload_dir / f"{uuid.uuid4().hex}{extension}"
        total_size = 0
        with open(file_path, "wb") as destination:
            while chunk := await file.read(1024 * 1024):
                total_size += len(chunk)
                if total_size > MAX_FILE_SIZE:
                    file_path.unlink(missing_ok=True)
                    raise HTTPException(413, "파일은 100MB를 넘을 수 없습니다")
                destination.write(chunk)
        job = Job(
            user_id=user.id,
            printer_id=printer.id,
            filename=file.filename,
            file_path=str(file_path),
            file_size=total_size,
            status=JobStatus.PENDING_APPROVAL,
            user_notes=notes,
            ams_slot=requested_slot,
        )
        db.add(job)
        jobs.append(job)
    await db.commit()
    for job in jobs:
        await db.refresh(job)
    notify_new_jobs(user.name, [file.filename for file in files], printer.name)
    return {"created": [job_dict(job, printer=printer) for job in jobs]}


@router.post("/upload/stl-preview")
async def stl_preview(
    files: List[UploadFile] = File(...),
    _: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    if not files or all(not file.filename for file in files):
        raise HTTPException(422, "파일을 선택해 주세요")
    upload_dir = Path(settings.UPLOAD_DIR)
    upload_dir.mkdir(parents=True, exist_ok=True)
    saved = []
    for file in files:
        if not file.filename or Path(file.filename).suffix.lower() not in ALLOWED_STL:
            continue
        temp_id = uuid.uuid4().hex + ".stl"
        file_path = upload_dir / temp_id
        total_size = 0
        with open(file_path, "wb") as destination:
            while chunk := await file.read(1024 * 1024):
                total_size += len(chunk)
                if total_size > MAX_FILE_SIZE:
                    file_path.unlink(missing_ok=True)
                    raise HTTPException(413, "파일은 100MB를 넘을 수 없습니다")
                destination.write(chunk)
        if file_path.exists():
            saved.append(
                {
                    "temp_id": temp_id,
                    "original_name": file.filename,
                    "url": f"/api/upload/stl-serve/{temp_id}",
                }
            )
    if not saved:
        raise HTTPException(422, "유효한 STL 파일이 없습니다")
    return {
        "files": saved,
        "printers": await _printers_payload(db),
    }


@router.get("/upload/stl-serve/{temp_id}")
async def stl_serve(temp_id: str, _: User = Depends(require_user)):
    if not re.fullmatch(r"[a-f0-9]{32}\.stl", temp_id):
        raise HTTPException(400, "유효하지 않은 파일 ID입니다")
    file_path = Path(settings.UPLOAD_DIR) / temp_id
    if not file_path.exists():
        raise HTTPException(404, "파일을 찾을 수 없습니다")
    return FileResponse(str(file_path), media_type="application/octet-stream")


async def _slice_job_bg(job_id: int, stl_path: str, original_name: str):
    from db import async_session_maker
    from slicer import SlicingError, slice_stl

    async with async_session_maker() as db:
        job = (
            await db.execute(select(Job).where(Job.id == job_id))
        ).scalar_one_or_none()
        if job is None:
            return
        try:
            final_path, estimated_minutes = await slice_stl(stl_path)
            job.file_path = final_path
            job.filename = Path(original_name).stem + Path(final_path).suffix
            job.file_size = Path(final_path).stat().st_size
            job.estimated_minutes = estimated_minutes
        except SlicingError as exc:
            # Keep the original .stl so the admin can still download it and
            # slice by hand; the reason is surfaced on the approval row.
            logger.warning("슬라이싱 실패 job=%s file=%s: %s", job_id, original_name, exc.message)
            job.admin_notes = f"[슬라이싱 실패] {exc.message}"
        except Exception as exc:
            logger.exception("슬라이싱 오류 job=%s file=%s", job_id, original_name)
            job.admin_notes = f"[슬라이싱 오류] {type(exc).__name__}: {exc}"
        finally:
            job.status = JobStatus.PENDING_APPROVAL
            await db.commit()


@router.post("/upload/stl-confirm")
async def stl_confirm(
    background_tasks: BackgroundTasks,
    file_ids: List[str] = Form(...),
    filenames: List[str] = Form(...),
    user_notes: str = Form(""),
    printer_id: str = Form(""),
    ams_slot: str = Form(""),
    scales: List[float] = Form(...),
    rotations_x: List[float] = Form(...),
    rotations_y: List[float] = Form(...),
    rotations_z: List[float] = Form(...),
    user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    from stl_transform import apply_transform

    printer = await resolve_printer(db, printer_id)
    requested_slot = _parse_ams_slot(ams_slot)
    notes = user_notes.strip() or None
    loop = asyncio.get_running_loop()
    pending = []
    jobs = []
    for index, (temp_id, original_name) in enumerate(zip(file_ids, filenames)):
        if not re.fullmatch(r"[a-f0-9]{32}\.stl", temp_id):
            continue
        file_path = Path(settings.UPLOAD_DIR) / temp_id
        if not file_path.exists():
            continue
        scale = scales[index] if index < len(scales) else 1.0
        rotation_x = rotations_x[index] if index < len(rotations_x) else 0.0
        rotation_y = rotations_y[index] if index < len(rotations_y) else 0.0
        rotation_z = rotations_z[index] if index < len(rotations_z) else 0.0
        stl_path = await loop.run_in_executor(
            None,
            apply_transform,
            str(file_path),
            scale,
            rotation_x,
            rotation_y,
            rotation_z,
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
            ams_slot=requested_slot,
        )
        db.add(job)
        await db.flush()
        pending.append((job.id, stl_path, original_name))
        jobs.append(job)
    if not jobs:
        raise HTTPException(422, "처리할 STL 파일이 없습니다")
    await db.commit()
    notify_new_jobs(user.name, [item[2] for item in pending], printer.name)
    for job_id, stl_path, original_name in pending:
        background_tasks.add_task(_slice_job_bg, job_id, stl_path, original_name)
    return {"created": [job_dict(job, printer=printer) for job in jobs]}


@router.get("/jobs")
async def my_jobs(
    user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    jobs = (
        await db.execute(
            select(Job).where(Job.user_id == user.id).order_by(Job.created_at.desc())
        )
    ).scalars().all()
    printer_ids = {job.printer_id for job in jobs}
    printers = {}
    if printer_ids:
        rows = (
            await db.execute(select(Printer).where(Printer.id.in_(printer_ids)))
        ).scalars().all()
        printers = {printer.id: printer for printer in rows}
    return {"jobs": [job_dict(job, printer=printers.get(job.printer_id)) for job in jobs]}


@router.post("/jobs/{job_id}/cancel")
async def cancel_job(
    job_id: int,
    user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    job = (
        await db.execute(select(Job).where(Job.id == job_id))
    ).scalar_one_or_none()
    if job is None:
        raise HTTPException(404, "작업을 찾을 수 없습니다")
    if job.user_id != user.id:
        raise HTTPException(403, "본인의 작업만 취소할 수 있습니다")
    if job.status not in (JobStatus.PENDING_APPROVAL, JobStatus.QUEUED):
        raise HTTPException(409, "현재 상태에서는 취소할 수 없습니다")
    was_queued = job.status == JobStatus.QUEUED
    printer_id = job.printer_id
    job.status = JobStatus.CANCELED
    job.queue_position = None
    await db.commit()
    if was_queued:
        queued = (
            await db.execute(
                select(Job)
                .where(Job.printer_id == printer_id)
                .where(Job.status == JobStatus.QUEUED)
                .order_by(Job.queue_position)
            )
        ).scalars().all()
        for position, queued_job in enumerate(queued, start=1):
            queued_job.queue_position = position
        await db.commit()
    return {"ok": True, "job": job_dict(job)}


async def _authorized_job(db: AsyncSession, job_id: int, user: User) -> Job:
    job = (
        await db.execute(select(Job).where(Job.id == job_id))
    ).scalar_one_or_none()
    if job is None:
        raise HTTPException(404, "작업을 찾을 수 없습니다")
    if job.user_id != user.id and user.role != UserRole.ADMIN:
        raise HTTPException(403, "이 파일을 볼 수 없습니다")
    return job


@router.get("/jobs/{job_id}/stl-preview")
async def job_stl_file(
    job_id: int,
    user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    job = await _authorized_job(db, job_id, user)
    stl_path = Path(job.file_path).with_suffix(".stl")
    if not stl_path.exists():
        raise HTTPException(404, "STL 미리보기를 사용할 수 없습니다")
    return FileResponse(str(stl_path), media_type="application/octet-stream")


@router.get("/jobs/{job_id}/thumb")
async def job_thumb(
    job_id: int,
    user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    job = await _authorized_job(db, job_id, user)
    if not job.file_path or not Path(job.file_path).exists():
        raise HTTPException(404, "파일을 찾을 수 없습니다")
    try:
        with zipfile.ZipFile(job.file_path) as archive:
            for candidate in ("Metadata/plate_1.png", "Metadata/top_1.png"):
                if candidate in archive.namelist():
                    return Response(content=archive.read(candidate), media_type="image/png")
    except Exception:
        pass
    raise HTTPException(404, "썸네일을 찾을 수 없습니다")
