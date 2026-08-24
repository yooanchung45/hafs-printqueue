"""Authenticated browser endpoints for shared printer camera feeds."""
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from auth import require_user
from camera_stream import camera_hub
from db import get_db
from models import Printer, User


router = APIRouter(prefix="/cameras", tags=["cameras"])
BOUNDARY = b"frame"


@router.get("/{printer_id}/stream")
async def camera_stream(
    printer_id: int,
    _: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Printer).where(Printer.id == printer_id))
    printer = result.scalar_one_or_none()
    if printer is None:
        raise HTTPException(404, "프린터를 찾을 수 없습니다")
    if not printer.ip or not printer.access_code:
        raise HTTPException(503, "카메라 연결 정보가 없습니다")

    session = camera_hub.get(
        printer.id, printer.ip, printer.access_code, printer.name
    )

    async def multipart_frames():
        async for jpeg in session.subscribe():
            yield (
                b"--" + BOUNDARY + b"\r\n"
                b"Content-Type: image/jpeg\r\n"
                b"Content-Length: " + str(len(jpeg)).encode("ascii") + b"\r\n\r\n"
                + jpeg + b"\r\n"
            )

    return StreamingResponse(
        multipart_frames(),
        media_type="multipart/x-mixed-replace; boundary=frame",
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )

