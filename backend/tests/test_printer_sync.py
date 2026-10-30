"""Regression tests for stale MQTT state around print startup."""

from datetime import timedelta
from types import SimpleNamespace
import asyncio
from unittest.mock import Mock

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from db import Base
from models import Job, JobStatus, Printer, PrinterStatus, User
import printer_sync
from printer_sync import _print_finished, _utcnow
from routes.admin import _mark_print_started


def _status(state, percentage=None):
    return SimpleNamespace(online=True, state=state, percentage=percentage)


def test_starting_a_print_resets_previous_progress():
    job = Job(id=7, status=JobStatus.QUEUED, queue_position=1)
    printer = Printer(
        id=3,
        name="A1",
        status=PrinterStatus.IDLE,
        progress=100,
    )

    _mark_print_started(job, printer)

    assert job.status == JobStatus.PRINTING
    assert job.started_at is not None
    assert job.print_confirmed_at == job.started_at
    assert job.queue_position is None
    assert printer.status == PrinterStatus.PRINTING
    assert printer.current_job_id == job.id
    assert printer.progress == 0


def test_unconfirmed_finish_never_completes_job():
    for previous in (PrinterStatus.PRINTING, PrinterStatus.OFFLINE):
        assert not _print_finished(_status("FINISH", 100), previous, 0, has_current_job=True)
        assert not _print_finished(_status("IDLE", 100), previous, 100, has_current_job=True)


def test_confirmed_finish_completes_even_short_print():
    assert _print_finished(_status("FINISH", 100), PrinterStatus.PRINTING, 0, has_current_job=True, confirmed_at=_utcnow())


def test_confirmed_completed_idle_survives_disconnect():
    assert _print_finished(_status("IDLE", 100), PrinterStatus.OFFLINE, None, has_current_job=True, confirmed_at=_utcnow() - timedelta(minutes=2))


def test_running_report_does_not_complete_job():
    assert not _print_finished(_status("RUNNING", 99), PrinterStatus.PRINTING, 99, has_current_job=True, confirmed_at=_utcnow())


def test_sync_requires_persisted_start_confirmation_and_notifies_once(monkeypatch):
    status = SimpleNamespace(online=True, state="FINISH", percentage=100, slots=[], nozzle_temp=25, bed_temp=25)
    client = SimpleNamespace(is_mock=False, get_status=lambda: status)
    monkeypatch.setattr(printer_sync, "PrinterClient", lambda **_: client)
    notify = Mock()
    monkeypatch.setattr(printer_sync, "notify_print_completed", notify)

    async def run():
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            maker = async_sessionmaker(engine, expire_on_commit=False)
            async with maker() as db:
                db.add(User(id=1, name="Student", email="test@example.com"))
                printer = Printer(id=1, name="A1", status=PrinterStatus.PRINTING, current_job_id=1, progress=0)
                job = Job(id=1, user_id=1, printer_id=1, filename="new.3mf", file_path="new.3mf", status=JobStatus.PRINTING, started_at=_utcnow() - timedelta(minutes=2))
                db.add_all([printer, job])
                await db.commit()
                await printer_sync.sync_printer(db, printer)
                assert job.status == JobStatus.PRINTING
                assert printer.progress == 0
                notify.assert_not_called()
                status.state, status.percentage = "RUNNING", 1
                await printer_sync.sync_printer(db, printer)
                assert job.print_confirmed_at is not None
            # Confirmation survives a new DB session (and process restart).
            async with maker() as db:
                printer = await db.get(Printer, 1)
                job = await db.get(Job, 1)
                status.state, status.percentage = "FINISH", 100
                await printer_sync.sync_printer(db, printer)
                await printer_sync.sync_printer(db, printer)
                assert job.status == JobStatus.AWAITING_CLEAR
                notify.assert_called_once()
        finally:
            await engine.dispose()

    asyncio.run(run())
