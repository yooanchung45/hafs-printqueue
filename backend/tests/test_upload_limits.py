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
    app = FastAPI()
    app.include_router(jobs.router)
    app.dependency_overrides[require_user] = lambda: SimpleNamespace(id=1)
    app.dependency_overrides[get_db] = lambda: None
    with TestClient(app) as client:
        yield client, tmp_path


@pytest.mark.parametrize("path,suffix", [
    ("/api/upload", ".gcode.3mf"),
    ("/api/upload/stl-preview", ".stl"),
])
@pytest.mark.parametrize("sizes", [[101], [50, 51]])
def test_oversized_upload_is_rejected_before_saving(upload_client, path, suffix, sizes):
    client, directory = upload_client
    response = client.post(path, files=[
        ("files", (f"model-{index}{suffix}", b"x" * size, "application/octet-stream"))
        for index, size in enumerate(sizes)
    ])
    assert response.status_code == 413
    assert "100MB" in response.json()["detail"]
    assert list(directory.iterdir()) == []


def test_stl_batch_at_limit_is_saved_completely(upload_client):
    client, directory = upload_client
    response = client.post("/api/upload/stl-preview", files=[
        ("files", ("a.stl", b"a" * 40, "application/octet-stream")),
        ("files", ("b.stl", b"b" * 60, "application/octet-stream")),
    ])
    assert response.status_code == 200
    files = response.json()["files"]
    assert len(files) == 2
    assert (directory / files[0]["temp_id"]).read_bytes() == b"a" * 40
    assert (directory / files[1]["temp_id"]).read_bytes() == b"b" * 60


def test_upload_options_advertise_both_limits(upload_client):
    client, _directory = upload_client
    limits = client.get("/api/upload").json()["limits"]
    assert limits["max_file_bytes"] == 100
    assert limits["max_batch_bytes"] == 100
