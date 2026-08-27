"""FastAPI entry point for the HAFS PrintQueue API server."""
import random
import re
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import Depends, FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.middleware.sessions import SessionMiddleware

import auth
from api_serializers import job_dict, printer_dict, user_dict
from auth import get_current_user, require_user
from config import settings, validate
from db import get_db, init_db
from models import FilamentSlot, Job, JobStatus, Printer, User
from routes import admin as admin_routes
from routes import board as board_routes
from routes import camera as camera_routes
from routes import jobs as jobs_routes


@asynccontextmanager
async def lifespan(app: FastAPI):
    validate()
    await init_db()

    from db import async_session_maker, seed_filament_slots, seed_printers
    from printer_live import start_background_tasks

    await seed_printers()
    await seed_filament_slots()
    await start_background_tasks(async_session_maker)

    try:
        yield
    finally:
        from camera_stream import camera_hub
        from printer_gateway import gateway

        await camera_hub.close()
        gateway.close()


app = FastAPI(title="HAFS PrintQueue API", lifespan=lifespan)

app.add_middleware(
    SessionMiddleware,
    secret_key=settings.SESSION_SECRET,
    session_cookie="printqueue_session",
    max_age=60 * 60 * 24 * 14,
    same_site="lax",
    https_only=False,
)

# OAuth remains owned by FastAPI; all other browser routes are owned by Next.js.
app.add_api_route("/api/auth/login", auth.login, methods=["GET"])
app.add_api_route("/api/auth/callback", auth.callback, methods=["GET"])
app.add_api_route("/api/auth/logout", auth.logout, methods=["GET", "POST"])

app.include_router(jobs_routes.router)
app.include_router(admin_routes.router)
app.include_router(board_routes.router)
app.include_router(camera_routes.router)


_GREETINGS = [
    "안녕하세요, {name}님! 👋",
    "어서오세요, {name}님! 🏃‍➡️",
    "반갑습니다, {name}님! 👽",
    "오늘도 좋은 하루 되세요, {name}님! 🌟",
    "{name}님, 출력하러 오셨나요? 🎉",
    "환영합니다, {name}님! 🚀",
    "좋은 시간이에요, {name}님! ⚡",
    "{name}님, 오늘 뭘 만들어볼까요? 🛠️",
    "안녕, {name}! 오늘도 파이팅! 💪",
    "{name}님이 돌아왔어요! 🎊",
    "반가워요, {name}님! 오늘의 프린트를 시작해볼까요? 🖨️",
    "{name}님, 멋진 걸 만들어봐요! 🌈",
]


def _greeting(user: User) -> str:
    real_name = re.sub(r"^\d+", "", user.name)
    if "이서우" in real_name:
        return "sw💘"
    if "이재현" in real_name:
        return "이재현 🥀 ah ey"
    if "이재승" in real_name:
        return "흰둥아 앉아"
    if "최성진" in real_name:
        return "공부해"
    return random.choice(_GREETINGS).format(name=real_name)


@app.get("/api/session")
async def session(user: Optional[User] = Depends(get_current_user)):
    """Return a non-error session envelope so the Next login screen can render."""
    return {"authenticated": user is not None, "user": user_dict(user) if user else None}


@app.get("/api/dashboard")
async def dashboard(
    user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    printers = (await db.execute(select(Printer).order_by(Printer.id))).scalars().all()
    slots = (
        await db.execute(
            select(FilamentSlot).order_by(FilamentSlot.printer_id, FilamentSlot.slot_index)
        )
    ).scalars().all()
    slots_by_printer: dict[int, list[FilamentSlot]] = {}
    for slot in slots:
        slots_by_printer.setdefault(slot.printer_id, []).append(slot)

    printer_jobs = {printer.id: [] for printer in printers}
    if printers:
        jobs = (
            await db.execute(
                select(Job)
                .where(Job.printer_id.in_([printer.id for printer in printers]))
                .where(
                    Job.status.in_(
                        [JobStatus.QUEUED, JobStatus.PRINTING, JobStatus.AWAITING_CLEAR]
                    )
                )
                .order_by(Job.printer_id, Job.queue_position)
            )
        ).scalars().all()
        for job in jobs:
            printer_jobs[job.printer_id].append(job_dict(job))

    return {
        "user": user_dict(user),
        "greeting": _greeting(user),
        "printers": [
            {
                **printer_dict(printer, slots_by_printer.get(printer.id, [])),
                "jobs": printer_jobs[printer.id],
            }
            for printer in printers
        ],
    }


@app.get("/api/health")
async def health():
    return {"status": "ok"}


@app.get("/api/printers/status")
async def printers_status(
    _: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    printers = (await db.execute(select(Printer).order_by(Printer.id))).scalars().all()
    return [printer_dict(printer) for printer in printers]
