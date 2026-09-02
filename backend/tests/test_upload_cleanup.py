"""Retention rules: which job files the sweep drops vs. keeps."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from models import JobStatus
from upload_cleanup import RETRYABLE_GRACE, _should_discard


NOW = datetime.now(timezone.utc).replace(tzinfo=None)
OLD = NOW - timedelta(seconds=RETRYABLE_GRACE + 3600)


def _job(status, *, started_at=None, completed_at=None, created_at=None):
    return SimpleNamespace(
        status=status,
        started_at=started_at,
        completed_at=completed_at,
        created_at=created_at or NOW,
    )


@pytest.mark.parametrize(
    ("job", "expected"),
    [
        (_job(JobStatus.REJECTED), True),
        (_job(JobStatus.COMPLETED), True),
        (_job(JobStatus.QUEUED), False),
        (_job(JobStatus.PRINTING, started_at=NOW), False),
        (_job(JobStatus.PENDING_APPROVAL), False),
        # FAILED — retryable, kept until the grace window passes.
        (_job(JobStatus.FAILED, completed_at=NOW), False),
        (_job(JobStatus.FAILED, completed_at=OLD), True),
        # CANCELED before it ever printed — dead weight now.
        (_job(JobStatus.CANCELED, started_at=None, completed_at=NOW), True),
        # CANCELED mid-print — kept so it can be reset + reprinted.
        (_job(JobStatus.CANCELED, started_at=NOW, completed_at=NOW), False),
        (_job(JobStatus.CANCELED, started_at=OLD, completed_at=OLD), True),
    ],
)
def test_should_discard(job, expected):
    assert _should_discard(job) is expected
