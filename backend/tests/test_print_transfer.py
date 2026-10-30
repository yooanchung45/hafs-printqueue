"""The queue advances only after the printer confirms startup."""
import asyncio
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import db as database
from db import Base
from models import Job, JobStatus, Printer, PrinterStatus, User
from printer_gateway import gateway
from routes import admin


@pytest.mark.parametrize("confirmed", [False, True])
def test_transfer_only_advances_queue_after_confirmation(monkeypatch, tmp_path, confirmed):
    path = tmp_path / "new.3mf"
    path.write_bytes(b"test")
    session = SimpleNamespace(snapshot=lambda: SimpleNamespace(connected=True), start_print=Mock(return_value=object()))

    def wait(_pending):
        if not confirmed:
            raise TimeoutError("start not confirmed")

    session.wait_for_print_start = wait
    monkeypatch.setattr(gateway, "get", lambda _: session)
    monkeypatch.setattr(admin, "_upload_with_progress", lambda *_: None)
    notify = Mock()
    monkeypatch.setattr(admin, "notify_print_started", notify)
    monkeypatch.setattr(admin, "_transfers", {"test": {"printer_id": 1}})
    monkeypatch.setattr(admin, "_active_printers", {1})

    async def run():
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            maker = async_sessionmaker(engine, expire_on_commit=False)
            monkeypatch.setattr(admin, "async_session_maker", maker)
            async with maker() as db:
                db.add(User(id=1, name="Student", email="test@example.com"))
                db.add(Printer(id=1, name="A1", status=PrinterStatus.IDLE, serial="test"))
                db.add(Job(id=1, user_id=1, printer_id=1, filename="new.3mf", file_path=str(path), status=JobStatus.QUEUED, queue_position=1))
                await db.commit()
            await admin._run_print_transfer("test", 1, 0)
            async with maker() as db:
                job, printer = await db.get(Job, 1), await db.get(Printer, 1)
                assert job.status == (JobStatus.PRINTING if confirmed else JobStatus.QUEUED)
                assert (job.print_confirmed_at is not None) == confirmed
                assert job.queue_position == (None if confirmed else 1)
                assert printer.current_job_id == (1 if confirmed else None)
                assert admin._transfer_snapshot("test")["phase"] == ("done" if confirmed else "error")
                assert notify.call_count == int(confirmed)
                assert 1 not in admin._active_printers
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_existing_database_adds_confirmation_column_once(monkeypatch):
    async def run():
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        monkeypatch.setattr(database, "engine", engine)
        try:
            async with engine.begin() as conn:
                await conn.execute(text("CREATE TABLE jobs (id INTEGER PRIMARY KEY, failure_acknowledged BOOLEAN NOT NULL DEFAULT 0)"))
            await database.init_db()
            await database.init_db()
            async with engine.begin() as conn:
                columns = await conn.run_sync(lambda sync: inspect(sync).get_columns("jobs"))
                assert sum(column["name"] == "print_confirmed_at" for column in columns) == 1
        finally:
            await engine.dispose()

    asyncio.run(run())
