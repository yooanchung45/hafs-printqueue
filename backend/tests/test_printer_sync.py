"""Regression tests for stale MQTT state around print startup."""

from datetime import timedelta
from types import SimpleNamespace

from models import Job, JobStatus, Printer, PrinterStatus
from printer_sync import _is_startup_terminal_report, _print_finished, _utcnow
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
    assert job.queue_position is None
    assert printer.status == PrinterStatus.PRINTING
    assert printer.current_job_id == job.id
    assert printer.progress == 0


def test_previous_finish_report_is_ignored_just_after_start():
    started_at = _utcnow()
    status = _status("FINISH", 100)

    assert _is_startup_terminal_report(status, started_at)
    assert not _print_finished(
        status,
        PrinterStatus.PRINTING,
        0,
        has_current_job=True,
        started_at=started_at,
    )


def test_finish_report_completes_job_after_startup_grace():
    started_at = _utcnow() - timedelta(minutes=2)
    status = _status("FINISH", 100)

    assert not _is_startup_terminal_report(status, started_at)
    assert _print_finished(
        status,
        PrinterStatus.PRINTING,
        99,
        has_current_job=True,
        started_at=started_at,
    )


def test_running_report_is_never_hidden_by_startup_grace():
    assert not _is_startup_terminal_report(_status("RUNNING", 1), _utcnow())
