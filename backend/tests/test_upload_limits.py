"""Upload size boundaries are checked before database work or file persistence."""
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from auth import require_user
from db import get_db
from routes import jobs


@pytest.fixture
def upload_client(monkeypatch, tmp_path):
    # Small limits exercise the real multipart routes without large test files.
    monkeypatch.setattr(jobs, "MAX_FILE_SIZE", 100)
    monkeypatch.setattr(jobs, "MAX_BATCH_BYTES", 100)
    monkeypatch.setattr(jobs.settings, "UPLOAD_DIR", str(tmp_path))

    async def printers(_db):
        return []

    monkeypatch.setattr(jobs, "_printers_payload", printers)
    class UploadDb:
        def add(self, job):
            job.id = 1

        async def flush(self):
            pass

        async def commit(self):
            pass

    async def printer(_db, _printer_id):
        return SimpleNamespace(id=1, name="Test printer")

    async def slice_noop(*_args, **_kwargs):
        pass

    monkeypatch.setattr(jobs, "resolve_printer", printer)
    monkeypatch.setattr(jobs, "_slice_job_bg", slice_noop)
    monkeypatch.setattr(jobs, "notify_new_jobs", lambda *_args: None)
    monkeypatch.setattr(jobs, "job_dict", lambda job, **_kwargs: {"id": job.id})
    app = FastAPI()
    app.include_router(jobs.router)
    app.dependency_overrides[require_user] = lambda: SimpleNamespace(id=1, name="Test user")
    app.dependency_overrides[get_db] = UploadDb
    with TestClient(app) as client:
        yield client, tmp_path


@pytest.mark.parametrize("path,suffix", [
    ("/api/upload", ".gcode.3mf"),
    ("/api/upload/stl-confirm", ".stl"),
])
@pytest.mark.parametrize("sizes", [[101], [50, 51]])
def test_oversized_upload_is_rejected_before_saving(upload_client, path, suffix, sizes):
    client, directory = upload_client
    response = client.post(path, data={"scales": "1", "rotations_x": "0", "rotations_y": "0", "rotations_z": "0"}, files=[
        ("files", (f"model-{index}{suffix}", b"x" * size, "application/octet-stream"))
        for index, size in enumerate(sizes)
    ])
    assert response.status_code == 413
    assert "100MB" in response.json()["detail"]
    assert list(directory.iterdir()) == []


def test_stl_batch_at_limit_is_saved_completely(upload_client):
    client, directory = upload_client
    response = client.post("/api/upload/stl-confirm", data={"scales": ["1", "1"], "rotations_x": ["0", "0"], "rotations_y": ["0", "0"], "rotations_z": ["0", "0"]}, files=[
        ("files", ("a.stl", b"a" * 40, "application/octet-stream")),
        ("files", ("b.stl", b"b" * 60, "application/octet-stream")),
    ])
    assert response.status_code == 200
    assert len(response.json()["created"]) == 1
    assert {path.read_bytes() for path in directory.iterdir()} == {b"a" * 40, b"b" * 60}


def test_upload_options_advertise_both_limits(upload_client):
    client, _directory = upload_client
    limits = client.get("/api/upload").json()["limits"]
    assert limits["max_file_bytes"] == 100
    assert limits["max_batch_bytes"] == 100
