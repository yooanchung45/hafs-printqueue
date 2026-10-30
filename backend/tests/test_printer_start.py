"""Print startup must be confirmed by a fresh report, not a cached state."""
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from printer_gateway import PrinterSession


def _session():
    session = PrinterSession("127.0.0.1", "test", "test-serial", "test")
    session._connected = True
    session.publish = Mock()
    session._data = {"print": {"gcode_state": "FINISH", "mc_percent": 100}}
    return session


def _report(session, **values):
    session._on_message(None, None, SimpleNamespace(payload=json.dumps({"print": values}).encode()))


def test_publish_and_previous_finish_are_not_start_confirmation():
    session = _session()
    pending = session.start_print("new.3mf", 0)
    _report(session, gcode_state="FINISH", mc_percent=100)
    _report(session, nozzle_temper=25)
    with pytest.raises(TimeoutError):
        session.wait_for_print_start(pending, timeout=0)


def test_fresh_prepare_is_latched_even_if_print_finishes_before_wait():
    session = _session()
    pending = session.start_print("new.3mf", 0)
    _report(session, gcode_state="PREPARE", gcode_file="/sdcard/new.3mf")
    assert "mc_percent" not in session.snapshot().data["print"]
    _report(session, gcode_state="FINISH", mc_percent=100)
    session.wait_for_print_start(pending, timeout=0)


def test_partial_report_cannot_confirm_cached_running_state():
    session = _session()
    pending = session.start_print("new.3mf", 0)
    session._data["print"]["gcode_state"] = "RUNNING"
    _report(session, mc_percent=1)
    with pytest.raises(TimeoutError):
        session.wait_for_print_start(pending, timeout=0)


def test_another_file_cannot_confirm_start():
    session = _session()
    pending = session.start_print("new.3mf", 0)
    _report(session, gcode_state="RUNNING", gcode_file="old.3mf")
    with pytest.raises(TimeoutError):
        session.wait_for_print_start(pending, timeout=0)


def test_printer_rejection_surfaces_reason():
    session = _session()
    pending = session.start_print("new.3mf", 0)
    _report(session, sequence_id=pending.sequence_id, result="FAIL", reason="file missing")
    with pytest.raises(RuntimeError, match="file missing"):
        session.wait_for_print_start(pending)


def test_disconnect_does_not_report_success():
    session = _session()
    pending = session.start_print("new.3mf", 0)
    session._connected = False
    with pytest.raises(ConnectionError):
        session.wait_for_print_start(pending)


def test_busy_printer_is_not_sent_another_start():
    session = _session()
    session._data["print"]["gcode_state"] = "RUNNING"
    with pytest.raises(RuntimeError):
        session.start_print("new.3mf", 0)
    session.publish.assert_not_called()
