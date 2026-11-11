"""필라멘트 소진 후 다른 슬롯으로 이어서 출력하는 절차."""
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from filament_swap import SwapError, format_error, pause_info, run_swap
from printer_gateway import GatewaySnapshot, PrinterSession

RUNOUT = 0x12008011  # A1 AMS lite "AMS filament ran out"


def _tray(idx, material="PLA", lo="190", hi="230"):
    if material is None:
        return {"id": str(idx)}
    return {"id": str(idx), "tray_type": material, "nozzle_temp_min": lo, "nozzle_temp_max": hi}


def _data(**print_values):
    values = {
        "gcode_state": "PAUSE",
        "print_error": RUNOUT,
        "subtask_id": "42",
        "ams_status": 0,
        "hms": [{"attr": 0x12002100, "code": 0x00020001}],  # AMS lite slot 2 ran out
        "ams": {
            "tray_now": "1",
            "ams": [{"id": "0", "tray": [
                _tray(0, "PETG", "230", "260"), _tray(1, None), _tray(2), _tray(3, None),
            ]}],
        },
    }
    values.update(print_values)
    return {"print": values}


class FakePrinter:
    """Moves through a scripted sequence of states per poll after each command."""

    def __init__(self, data, after_change=None, after_resume=None):
        self.data = data
        self.after_change = list(after_change or [])
        self.after_resume = list(after_resume or [])
        self.queue = []
        self.sent = []
        self.replies = {}

    def snapshot(self):
        if self.queue:
            update = self.queue.pop(0)
            p = self.data["print"]
            ams = update.pop("ams", None)
            p.update(update)
            if ams:
                p["ams"].update(ams)
        return GatewaySnapshot(True, True, 0.0, json.loads(json.dumps(self.data)))

    def change_filament(self, slot, curr, tar):
        self.sent.append(("change", slot, curr, tar))
        self.queue = list(self.after_change)
        return "c1"

    def clear_print_error(self, subtask, code):
        self.sent.append(("clear", subtask, code))
        self.data["print"]["print_error"] = 0

    def resume_print(self):
        self.sent.append(("resume",))
        self.queue = list(self.after_resume)
        return "r1"

    def request_full_status(self):
        pass

    def reply(self, seq):
        return self.replies.get(seq)


def _run(printer, slot=2, **kw):
    clock = iter(range(0, 10_000))
    return run_swap(printer, slot, sleep=lambda _s: None, clock=lambda: next(clock),
                    settle=2, **kw)


def test_pause_info_detects_ams_lite_runout_slot():
    info = pause_info(_data())
    assert info == {"print_error": "1200-8011", "runout": True, "runout_slot": 1, "tray_now": 1}
    assert pause_info(_data(gcode_state="RUNNING")) is None
    assert format_error(0) is None


def test_swap_loads_new_slot_clears_error_and_resumes():
    printer = FakePrinter(
        _data(),
        after_change=[{"ams_status": 0x0100}, {"ams_status": 0x0100, "ams": {"tray_now": "2"}},
                      {"ams_status": 0, "ams": {"tray_now": "2"}}],
        after_resume=[{"gcode_state": "RUNNING"}] * 5,
    )
    message = _run(printer)
    assert "슬롯 3" in message
    # The runout slot is now empty, so unload at the new filament's temperature.
    assert printer.sent[0] == ("change", 2, 210, 210)
    assert ("clear", "42", RUNOUT) in printer.sent
    assert printer.sent[-1] == ("resume",)


def test_swap_uses_job_slot_temps_when_runout_slot_still_has_tray_info():
    data = _data()
    data["print"]["ams"]["ams"][0]["tray"][1] = _tray(1, "PETG", "230", "260")
    data["print"]["hms"] = []
    printer = FakePrinter(
        data,
        after_change=[{"ams_status": 0x0100}, {"ams_status": 0, "ams": {"tray_now": "2"}}],
        after_resume=[{"gcode_state": "RUNNING"}] * 5,
    )
    _run(printer)
    assert printer.sent[0] == ("change", 2, 245, 210)


def test_swap_refuses_when_not_paused_or_target_empty():
    with pytest.raises(SwapError, match="일시정지"):
        _run(FakePrinter(_data(gcode_state="RUNNING")))
    with pytest.raises(SwapError, match="필라멘트가 없습니다"):
        _run(FakePrinter(_data()), slot=3)


def test_swap_reports_new_printer_error_during_load():
    printer = FakePrinter(_data(), after_change=[{"ams_status": 0x0100, "print_error": 0x12008007}])
    with pytest.raises(SwapError, match="1200-8007"):
        _run(printer)
    assert ("resume",) not in printer.sent


def test_swap_reports_rejected_change_command():
    printer = FakePrinter(_data())
    printer.replies["c1"] = {"result": "FAIL", "reason": "busy"}
    with pytest.raises(SwapError, match="busy"):
        _run(printer)


def test_swap_detects_pause_again_after_resume():
    printer = FakePrinter(
        _data(),
        after_change=[{"ams_status": 0x0100}, {"ams_status": 0, "ams": {"tray_now": "2"}}],
        after_resume=[{"gcode_state": "RUNNING"}, {"gcode_state": "PAUSE", "print_error": RUNOUT}],
    )
    with pytest.raises(SwapError, match="다시 일시정지"):
        _run(printer)


def test_swap_skips_load_when_target_already_loaded():
    printer = FakePrinter(_data(ams={"tray_now": "2", "ams": _data()["print"]["ams"]["ams"]}),
                          after_resume=[{"gcode_state": "RUNNING"}] * 5)
    _run(printer)
    assert not any(cmd[0] == "change" for cmd in printer.sent)


def _session():
    session = PrinterSession("127.0.0.1", "test", "test-serial", "test")
    session._connected = True
    session.publish = Mock()
    return session


def test_gateway_change_filament_matches_studio_message_and_tracks_reply():
    session = _session()
    seq = session.change_filament(2, 245, 210)
    payload = session.publish.call_args.args[0]["print"]
    assert payload["command"] == "ams_change_filament"
    assert (payload["ams_id"], payload["slot_id"], payload["target"]) == (0, 2, 2)
    assert (payload["curr_temp"], payload["tar_temp"]) == (245, 210)
    reply = {"print": {"command": "ams_change_filament", "sequence_id": seq, "result": "fail", "reason": "x"}}
    session._on_message(None, None, SimpleNamespace(payload=json.dumps(reply).encode()))
    assert session.reply(seq)["result"] == "FAIL"


def test_gateway_clear_print_error_sends_both_messages():
    session = _session()
    session.clear_print_error("42", RUNOUT)
    first, second = (call.args[0] for call in session.publish.call_args_list)
    assert first["print"]["command"] == "clean_print_error"
    assert first["print"]["print_error"] == RUNOUT
    assert second["system"]["command"] == "uiop" and second["system"]["err"] == "12008011"
