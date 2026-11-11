"""관리자 진단용 프린터 직접 제어 명령."""
import json
from types import SimpleNamespace
from unittest.mock import Mock

from printer_gateway import GatewaySnapshot, PrinterSession
from routes.admin import _live_view


def _session():
    session = PrinterSession("127.0.0.1", "test", "test-serial", "test")
    session._connected = True
    session.publish = Mock()
    return session


def _receive(session, payload):
    session._on_message(None, None, SimpleNamespace(payload=json.dumps(payload).encode()))


def test_set_ams_tray_matches_studio_filament_setting():
    session = _session()
    session.set_ams_tray(2, "GFA00", "PLA", "5E43B7FF", 190, 250)
    payload = session.publish.call_args.args[0]["print"]
    assert payload["command"] == "ams_filament_setting"
    assert (payload["ams_id"], payload["tray_id"], payload["slot_id"]) == (0, 2, 2)
    assert payload["tray_info_idx"] == "GFA00" and payload["tray_type"] == "PLA"
    assert payload["tray_color"] == "5E43B7FF"
    assert (payload["nozzle_temp_min"], payload["nozzle_temp_max"]) == (190, 250)


def test_auto_refill_and_ams_control_messages():
    session = _session()
    session.set_auto_refill(True)
    assert session.publish.call_args.args[0]["print"]["command"] == "print_option"
    assert session.publish.call_args.args[0]["print"]["auto_switch_filament"] is True
    session.ams_control("resume")
    assert session.publish.call_args.args[0]["print"] == {
        "sequence_id": session.publish.call_args.args[0]["print"]["sequence_id"],
        "command": "ams_control",
        "param": "resume",
    }


def test_command_log_keeps_acks_but_not_push_status():
    session = _session()
    _receive(session, {"print": {"command": "push_status", "gcode_state": "PAUSE"}})
    _receive(session, {"print": {"command": "ams_change_filament", "sequence_id": "7", "errno": 0}})
    _receive(session, {"system": {"command": "uiop", "result": "success"}})
    log = session.command_log()
    assert [entry["command"] for entry in log] == ["ams_change_filament", "uiop"]
    assert log[0]["errno"] == 0 and log[1]["section"] == "system"
    for i in range(60):
        _receive(session, {"print": {"command": "resume", "sequence_id": str(i)}})
    assert len(session.command_log()) == 40


def test_live_view_decodes_trays_and_auto_refill_flag():
    data = {"print": {
        "gcode_state": "PAUSE", "print_error": 0x12008011, "home_flag": 1 << 10,
        "ams": {"tray_now": "1", "ams": [{"id": "0", "tray": [{"id": "2", "tray_color": "C12E1FFF"}]}]},
    }}
    view = _live_view(GatewaySnapshot(True, True, 1.0, data))
    assert view["auto_refill_guess"] is True
    assert view["tray_now"] == "1"
    assert view["trays"] == [{"ams": "0", "id": "2", "tray_color": "C12E1FFF"}]
