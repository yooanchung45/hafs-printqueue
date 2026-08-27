"""Boundary checks for the API-only FastAPI application."""

import asyncio
import inspect

from fastapi.testclient import TestClient

from main import app
from models import Printer, PrinterStatus
from routes.admin import dismiss_failed_job, retry_failed_job
from routes.jobs import pick_best_printer, stl_confirm, upload_submit


class _Result:
    def __init__(self, *, rows=None, count=None):
        self.rows = rows
        self.count = count

    def scalars(self):
        return self

    def all(self):
        return self.rows

    def scalar_one(self):
        return self.count


class _PrinterLoadDb:
    def __init__(self, printers, counts):
        self.results = [_Result(rows=printers), *[_Result(count=count) for count in counts]]

    async def execute(self, _statement):
        return self.results.pop(0)


def test_application_routes_stay_under_api_prefix():
    framework_paths = {"/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc"}
    application_paths = {
        route.path for route in app.routes if route.path not in framework_paths
    }

    assert application_paths
    assert all(path.startswith("/api") for path in application_paths)


def test_public_envelopes_and_auth_boundary():
    client = TestClient(app)

    assert client.get("/api/health").json() == {"status": "ok"}
    assert client.get("/api/session").json() == {
        "authenticated": False,
        "user": None,
    }
    assert client.get("/api/dashboard").status_code == 401


def test_student_upload_routes_do_not_accept_a_printer_choice():
    assert "printer_id" not in inspect.signature(upload_submit).parameters
    assert "printer_id" not in inspect.signature(stl_confirm).parameters


def test_failed_job_actions_are_explicit_admin_operations():
    retry_parameters = inspect.signature(retry_failed_job).parameters
    assert "printer_id" in retry_parameters
    assert "at_front" in retry_parameters
    assert "job_id" in inspect.signature(dismiss_failed_job).parameters

    application_paths = {route.path for route in app.routes}
    assert "/api/admin/jobs/{job_id}/retry" in application_paths
    assert "/api/admin/jobs/{job_id}/dismiss-failure" in application_paths


def test_printer_settings_exposes_an_emergency_stop_action():
    application_paths = {route.path for route in app.routes}

    assert "/api/admin/printers/{printer_id}/stop" in application_paths


def test_best_printer_uses_the_shortest_healthy_queue():
    printers = [
        Printer(id=1, name="A1", status=PrinterStatus.IDLE),
        Printer(id=2, name="A2", status=PrinterStatus.IDLE),
        Printer(id=3, name="A3", status=PrinterStatus.OFFLINE),
    ]
    db = _PrinterLoadDb(printers, counts=[4, 1, 0])

    selected = asyncio.run(pick_best_printer(db))

    assert selected.id == 2
