"""FastAPI 진입점."""
import random
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

import auth
import filters as _filters
from config import settings, validate
from db import init_db
from routes import jobs as jobs_routes
from routes import admin as admin_routes
from routes import board as board_routes


@asynccontextmanager
async def lifespan(app: FastAPI):
    """앱 시작/종료 시 실행."""
    validate()
    await init_db()

    from db import seed_printers, seed_filament_slots
    await seed_printers()
    await seed_filament_slots()

    from db import async_session_maker
    from printer_live import start_background_tasks
    await start_background_tasks(async_session_maker)

    yield


app = FastAPI(title="HAFS PrintQueue", lifespan=lifespan)

app.add_middleware(
    SessionMiddleware,
    secret_key=settings.SESSION_SECRET,
    session_cookie="printqueue_session",
    max_age=60 * 60 * 24 * 14,
    same_site="lax",
    https_only=False,
)

app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")
_filters.register(templates)


# 인증 라우트
app.add_api_route("/auth/login", auth.login, methods=["GET"])
app.add_api_route("/auth/callback", auth.callback, methods=["GET"])
app.add_api_route("/auth/logout", auth.logout, methods=["GET", "POST"])

# 작업/관리자/게시판 라우트
app.include_router(jobs_routes.router)
app.include_router(admin_routes.router)
app.include_router(board_routes.router)


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
    "잘 지냈나 {name}이",
]


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    from auth import get_current_user
    from db import get_db
    from models import Printer, FilamentSlot
    from sqlalchemy import select

    async for db in get_db():
        user = await get_current_user(request, db)

        if user is None:
            return templates.TemplateResponse(
                "login.html",
                {"request": request, "error": request.query_params.get("error")},
            )

        from models import Job, JobStatus

        result = await db.execute(select(Printer).order_by(Printer.id))
        printers = result.scalars().all()

        result = await db.execute(
            select(FilamentSlot).order_by(FilamentSlot.printer_id, FilamentSlot.slot_index)
        )
        slots_by_printer = {}
        for slot in result.scalars().all():
            slots_by_printer.setdefault(slot.printer_id, []).append(slot)

        printer_ids = [p.id for p in printers]
        result = await db.execute(
            select(Job)
            .where(Job.printer_id.in_(printer_ids))
            .where(Job.status.in_([JobStatus.QUEUED, JobStatus.PRINTING]))
            .order_by(Job.printer_id, Job.queue_position)
        )
        printer_jobs = {p.id: [] for p in printers}
        for job in result.scalars().all():
            printer_jobs[job.printer_id].append(job)

        if "이서우" in user.name:
            greeting = "sw💘"
        else: 
            greeting = random.choice(_GREETINGS).format(name=user.name)
        return templates.TemplateResponse(
            "dashboard.html",
            {
                "request": request,
                "user": user,
                "printers": printers,
                "slots_by_printer": slots_by_printer,
                "printer_jobs": printer_jobs,
                "user_id": user.id,
                "greeting": greeting,
            },
        )


@app.get("/guide", response_class=HTMLResponse)
async def guide(request: Request):
    from auth import get_current_user
    from db import get_db

    async for db in get_db():
        user = await get_current_user(request, db)
        if user is None:
            return templates.TemplateResponse(
                "login.html",
                {"request": request, "error": request.query_params.get("error")},
            )
        return templates.TemplateResponse(
            "slicing_guide.html",
            {"request": request, "user": user},
        )


@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    return RedirectResponse(url="/static/favicon.ico")


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/printers/status")
async def printers_status(request: Request):
    """JSON printer status for dashboard live-update (any logged-in user)."""
    from auth import get_current_user
    from db import get_db
    from models import Printer
    from sqlalchemy import select
    from fastapi.responses import JSONResponse

    async for db in get_db():
        user = await get_current_user(request, db)
        if user is None:
            return JSONResponse(status_code=401, content={})
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
