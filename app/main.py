"""FastAPI 진입점."""
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

import auth
from config import settings, validate
from db import init_db
from routes import jobs as jobs_routes
from routes import admin as admin_routes


@asynccontextmanager
async def lifespan(app: FastAPI):
    """앱 시작/종료 시 실행."""
    validate()
    await init_db()

    from db import seed_printers, seed_filament_slots
    await seed_printers()
    await seed_filament_slots()

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


# 인증 라우트
app.add_api_route("/auth/login", auth.login, methods=["GET"])
app.add_api_route("/auth/callback", auth.callback, methods=["GET"])
app.add_api_route("/auth/logout", auth.logout, methods=["GET", "POST"])

# 작업/관리자 라우트
app.include_router(jobs_routes.router)
app.include_router(admin_routes.router)


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

        result = await db.execute(select(Printer).order_by(Printer.id))
        printers = result.scalars().all()

        result = await db.execute(
            select(FilamentSlot).order_by(FilamentSlot.printer_id, FilamentSlot.slot_index)
        )
        slots_by_printer = {}
        for slot in result.scalars().all():
            slots_by_printer.setdefault(slot.printer_id, []).append(slot)

        return templates.TemplateResponse(
            "dashboard.html",
            {
                "request": request,
                "user": user,
                "printers": printers,
                "slots_by_printer": slots_by_printer,
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


@app.get("/health")
async def health():
    return {"status": "ok"}
