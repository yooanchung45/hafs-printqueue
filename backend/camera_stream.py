"""On-demand Bambu A1/P1 camera ingest with one upstream per printer.

The printer sends length-prefixed JPEG frames over TLS/6000.  Every browser
subscriber shares the same upstream connection and receives only the newest
frame, so a slow client never builds latency or adds load to the printer.
"""
from __future__ import annotations

import asyncio
import logging
import random
import ssl
import struct
import time
from dataclasses import dataclass
from typing import AsyncIterator


logger = logging.getLogger("camera_stream")

CAMERA_PORT = 6000
MAX_FRAME_BYTES = 5 * 1024 * 1024
# How long to keep the upstream connection open with zero viewers before
# tearing it down. This is a full-page app, so every navigation away from
# the dashboard drops the subscriber and every navigation back creates a new
# one — a short grace period meant reconnecting almost always paid the full
# cost of a fresh TLS handshake + auth + wait for the first frame. Five
# minutes comfortably covers tabbing away, reading a job, checking another
# page and coming back; the cost is one idle TLS connection per recently
# viewed printer on the LAN, which is cheap.
IDLE_GRACE_SECONDS = 300.0
READ_TIMEOUT_SECONDS = 15.0


def build_auth_packet(access_code: str) -> bytes:
    """Build Bambu's 80-byte LAN live-view authentication packet."""
    user = b"bblp".ljust(32, b"\0")
    token = access_code.encode("utf-8")[:32].ljust(32, b"\0")
    return struct.pack("<IIII", 0x40, 0x3000, 0, 0) + user + token


@dataclass(frozen=True)
class CameraConfig:
    host: str
    access_code: str
    name: str


class CameraSession:
    def __init__(self, printer_id: int, config: CameraConfig):
        self.printer_id = printer_id
        self.config = config
        self.subscribers = 0
        self.frame: bytes | None = None
        self.frame_number = 0
        self.last_error: str | None = None
        self._condition = asyncio.Condition()
        self._task: asyncio.Task | None = None
        self._stop_handle: asyncio.TimerHandle | None = None

    async def subscribe(self) -> AsyncIterator[bytes]:
        self.subscribers += 1
        if self._stop_handle:
            self._stop_handle.cancel()
            self._stop_handle = None
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(
                self._run(), name=f"camera-{self.printer_id}"
            )

        # Start at 0, not self.frame_number: if the upstream is already live
        # (e.g. the previous subscriber left less than IDLE_GRACE_SECONDS
        # ago) there's already a current frame sitting in self.frame, and a
        # new subscriber should get it immediately instead of waiting for
        # the printer to push the next one.
        seen = 0
        try:
            while True:
                async with self._condition:
                    await self._condition.wait_for(
                        lambda: self.frame_number != seen
                        or self._task is None
                        or self._task.done()
                    )
                    if self.frame_number == seen:
                        return
                    seen = self.frame_number
                    frame = self.frame
                if frame:
                    yield frame
        finally:
            self.subscribers -= 1
            if self.subscribers == 0:
                loop = asyncio.get_running_loop()
                self._stop_handle = loop.call_later(
                    IDLE_GRACE_SECONDS, self._stop_if_idle
                )

    async def snapshot(self, timeout: float = 12.0) -> bytes:
        """Return one current JPEG frame.

        Instant when the upstream is already warm (returns the buffered
        frame); otherwise spins the upstream up, waits for the first frame,
        and — because it leaves through ``subscribe``'s ``finally`` — keeps
        it warm afterwards so the live stream request that follows connects
        to an already-running session.
        """
        if self.frame is not None:
            return self.frame

        async def _first() -> bytes:
            async for frame in self.subscribe():
                return frame
            raise ConnectionError("카메라 프레임을 받지 못했습니다")

        return await asyncio.wait_for(_first(), timeout)

    def _stop_if_idle(self) -> None:
        self._stop_handle = None
        if self.subscribers == 0 and self._task and not self._task.done():
            self._task.cancel()

    async def _run(self) -> None:
        delay = 1.0
        try:
            while self.subscribers:
                started = time.monotonic()
                try:
                    await self._read_stream()
                    raise ConnectionError("카메라 연결이 종료되었습니다")
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    self.last_error = str(exc)
                    logger.warning(
                        "camera reconnect printer=%s error=%s", self.config.name, exc
                    )
                if not self.subscribers:
                    break
                if time.monotonic() - started > 30:
                    delay = 1.0
                await asyncio.sleep(delay + random.uniform(0, delay * 0.25))
                delay = min(delay * 2, 30.0)
        finally:
            self._task = None
            async with self._condition:
                self._condition.notify_all()

    async def _read_stream(self) -> None:
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(
                self.config.host,
                CAMERA_PORT,
                ssl=context,
                server_hostname=self.config.host,
            ),
            timeout=8.0,
        )
        try:
            writer.write(build_auth_packet(self.config.access_code))
            await writer.drain()
            while True:
                header = await asyncio.wait_for(
                    reader.readexactly(16), timeout=READ_TIMEOUT_SECONDS
                )
                size = struct.unpack_from("<I", header)[0]
                if size < 4 or size > MAX_FRAME_BYTES:
                    raise ValueError(f"invalid camera frame size: {size}")
                frame = await asyncio.wait_for(
                    reader.readexactly(size), timeout=READ_TIMEOUT_SECONDS
                )
                if not (frame.startswith(b"\xff\xd8") and frame.endswith(b"\xff\xd9")):
                    raise ValueError("invalid JPEG frame")
                self.frame = frame
                self.frame_number += 1
                self.last_error = None
                async with self._condition:
                    self._condition.notify_all()
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass

    async def close(self) -> None:
        if self._stop_handle:
            self._stop_handle.cancel()
        if self._task and not self._task.done():
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)


class CameraHub:
    def __init__(self):
        self._sessions: dict[int, CameraSession] = {}

    def get(self, printer_id: int, host: str, access_code: str, name: str) -> CameraSession:
        config = CameraConfig(host, access_code, name)
        session = self._sessions.get(printer_id)
        if session is None or session.config != config:
            if session is not None:
                # Configuration changes are rare; let the old task close without
                # holding up the request and replace it immediately.
                asyncio.create_task(session.close())
            session = CameraSession(printer_id, config)
            self._sessions[printer_id] = session
        return session

    async def close(self) -> None:
        await asyncio.gather(
            *(session.close() for session in self._sessions.values()),
            return_exceptions=True,
        )
        self._sessions.clear()


camera_hub = CameraHub()

