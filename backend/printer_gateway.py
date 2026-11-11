"""프린터별 단일 paho MQTT 연결을 소유하는 프로세스 내부 게이트웨이."""
from __future__ import annotations

import copy
import json
import logging
import ssl
import threading
import time
from dataclasses import dataclass

import paho.mqtt.client as mqtt

logger = logging.getLogger("printer_gateway")

MQTT_PORT = 8883
MQTT_KEEPALIVE = 60
RECONNECT_DELAY = 20
OFFLINE_AFTER = 90
PRINT_START_TIMEOUT = 60


@dataclass
class PrintStart:
    sequence_id: str
    filename: str
    confirmed: bool = False
    error: str | None = None


def _deep_merge(target: dict, update: dict) -> None:
    for key, value in update.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            _deep_merge(target[key], value)
        else:
            target[key] = value


@dataclass(frozen=True)
class GatewaySnapshot:
    connected: bool
    online: bool
    last_report_age: float | None
    data: dict
    error: str | None = None


class PrinterSession:
    def __init__(self, ip: str, access_code: str, serial: str, name: str):
        self.ip = ip
        self.access_code = access_code
        self.serial = serial
        self.name = name
        self.report_topic = f"device/{serial}/report"
        self.request_topic = f"device/{serial}/request"
        self._lock = threading.RLock()
        self._start_condition = threading.Condition(self._lock)
        self._pending_start = None
        self._connected = False
        self._last_report = None
        self._last_error = None
        self._data = {}
        self._started = False
        # sequence_id -> the printer's reply to a command we sent (result/reason).
        self._replies: dict[str, dict] = {}
        # Recent command echoes/acks from the printer (raw, newest last).
        self._command_log: list[dict] = []

        client_id = f"hafs-{serial}"[:23]
        self.client = mqtt.Client(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
            client_id=client_id,
            clean_session=True,
        )
        self.client.username_pw_set("bblp", access_code)
        self.client.tls_set(cert_reqs=ssl.CERT_NONE)
        self.client.tls_insecure_set(True)
        self.client.reconnect_delay_set(RECONNECT_DELAY, RECONNECT_DELAY)
        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self.client.on_message = self._on_message

    @property
    def config_key(self):
        return self.ip, self.access_code, self.serial

    def start(self):
        with self._lock:
            if self._started:
                return
            self._started = True
            self._last_error = None
        logger.info("[%s] MQTT gateway starting (%s)", self.name, self.ip)
        self.client.connect_async(self.ip, MQTT_PORT, MQTT_KEEPALIVE)
        self.client.loop_start()

    def stop(self):
        with self._lock:
            if not self._started:
                return
            self._started = False
            self._connected = False
        try:
            self.client.disconnect()
        except Exception:
            pass
        self.client.loop_stop()
        logger.info("[%s] MQTT gateway stopped", self.name)

    def _on_connect(self, client, _userdata, _flags, reason_code, _properties=None):
        if reason_code != 0:
            with self._lock:
                self._connected = False
                self._last_error = f"connect failed: {reason_code}"
            logger.warning("[%s] MQTT connect failed: %s", self.name, reason_code)
            return
        with self._lock:
            self._connected = True
            self._last_error = None
        client.subscribe(self.report_topic, qos=0)
        self.request_full_status()
        logger.info("[%s] MQTT connected and subscribed", self.name)

    def _on_disconnect(self, _client, _userdata, _disconnect_flags, reason_code, _properties=None):
        with self._lock:
            self._connected = False
            if self._started:
                self._last_error = f"disconnected: {reason_code}"
        if self._started:
            logger.warning(
                "[%s] MQTT disconnected (%s); retrying in %ss",
                self.name, reason_code, RECONNECT_DELAY,
            )

    def _on_message(self, _client, _userdata, message):
        try:
            payload = json.loads(message.payload.decode("utf-8"))
            if not isinstance(payload, dict):
                return
            with self._lock:
                report = payload.get("print") or {}
                cached_print = self._data.get("print") or {}
                if report.get("gcode_state") in ("PREPARE", "RUNNING") and cached_print.get("gcode_state") in ("IDLE", "FINISH", "FAILED"):
                    # Partial startup reports may omit progress from the new job.
                    cached_print.pop("mc_percent", None)
                    cached_print.pop("mc_remaining_time", None)
                pending = self._pending_start
                if pending is not None:
                    # Inspect this report, never the merged previous-job cache.
                    if str(report.get("sequence_id")) == pending.sequence_id and str(report.get("result", "")).upper() in ("FAIL", "FAILED", "ERROR"):
                        pending.error = str(report.get("reason") or report.get("error") or "프린터가 출력 시작을 거절했습니다")
                    filename = str(report.get("gcode_file") or "").replace("\\", "/").rsplit("/", 1)[-1]
                    if report.get("gcode_state") == "PAUSE" and (not filename or filename == pending.filename):
                        pending.error = "프린터가 일시 정지되었습니다. 프린터 화면을 확인해 주세요."
                    if report.get("gcode_state") in ("PREPARE", "RUNNING") and (not filename or filename == pending.filename):
                        pending.confirmed = True
                    self._start_condition.notify_all()
                seq = report.get("sequence_id")
                if report.get("command") and seq is not None and "result" in report:
                    self._replies[str(seq)] = {
                        "command": report.get("command"),
                        "result": str(report.get("result") or "").upper(),
                        "reason": report.get("reason") or report.get("err_code") or report.get("errno"),
                    }
                    if len(self._replies) > 50:
                        self._replies.pop(next(iter(self._replies)))
                # Keep every command echo/ack (not the periodic push_status) for diagnosis.
                for section in ("print", "system"):
                    part = payload.get(section)
                    if isinstance(part, dict) and part.get("command") and part.get("command") != "push_status":
                        self._command_log.append({
                            "at": time.strftime("%Y-%m-%d %H:%M:%S"),
                            "section": section,
                            **{k: v for k, v in part.items() if k not in ("ams", "hms", "vt_tray")},
                        })
                        del self._command_log[:-40]
                _deep_merge(self._data, payload)
                self._last_report = time.monotonic()
                self._last_error = None
        except Exception as exc:
            logger.warning("[%s] invalid MQTT report: %s", self.name, exc)

    def publish(self, payload: dict):
        with self._lock:
            if not self._connected:
                raise ConnectionError(f"{self.name} MQTT is not connected")
        result = self.client.publish(self.request_topic, json.dumps(payload), qos=0)
        if result.rc != mqtt.MQTT_ERR_SUCCESS:
            raise ConnectionError(f"MQTT publish failed: {mqtt.error_string(result.rc)}")

    def request_full_status(self):
        self.publish({"pushing": {"sequence_id": "0", "command": "pushall"}})

    def set_light(self, on: bool):
        self.publish({
            "system": {
                "sequence_id": str(time.time_ns()),
                "command": "ledctrl",
                "led_node": "chamber_light",
                "led_mode": "on" if on else "off",
                "led_on_time": 500,
                "led_off_time": 500,
                "loop_times": 1,
                "interval_time": 1000,
            }
        })

    def stop_print(self):
        self.publish({
            "print": {"sequence_id": str(time.time_ns()), "command": "stop"}
        })

    def send_gcode(self, gcode: str):
        """Run raw gcode lines on an idle printer (LAN-mode gcode_line command)."""
        self.publish({
            "print": {
                "sequence_id": str(time.time_ns()),
                "command": "gcode_line",
                "param": gcode,
            }
        })

    def change_filament(self, slot: int, curr_temp: int, tar_temp: int) -> str:
        """Load AMS ``slot`` (0-3), unloading whatever is in the toolhead first.

        Same message Bambu Studio sends from the AMS panel; Studio allows it
        while a print is paused. The A1 refuses filament moves without temps.
        """
        seq = str(time.time_ns())
        slot = int(slot)
        self.publish({
            "print": {
                "sequence_id": seq,
                "command": "ams_change_filament",
                "ams_id": slot // 4,
                "slot_id": slot % 4,
                "target": slot,
                "curr_temp": int(curr_temp),
                "tar_temp": int(tar_temp),
            }
        })
        return seq

    def clear_print_error(self, subtask_id, print_error: int):
        """Dismiss the printer's error dialog (Studio sends both messages)."""
        self.publish({
            "print": {
                "sequence_id": str(time.time_ns()),
                "command": "clean_print_error",
                "subtask_id": str(subtask_id or ""),
                "print_error": int(print_error),
            }
        })
        self.publish({
            "system": {
                "sequence_id": str(time.time_ns()),
                "command": "uiop",
                "name": "print_error",
                "action": "close",
                "source": 1,
                "type": "dialog",
                "err": f"{int(print_error) & 0xFFFFFFFF:08X}",
            }
        })

    def resume_print(self) -> str:
        seq = str(time.time_ns())
        self.publish({"print": {"sequence_id": seq, "command": "resume", "param": ""}})
        return seq

    def set_ams_tray(self, slot: int, tray_info_idx: str, tray_type: str, color_rgba: str,
                     temp_min: int, temp_max: int) -> str:
        """Write filament info for an AMS slot on the printer (same as editing it in Studio)."""
        seq = str(time.time_ns())
        slot = int(slot)
        self.publish({
            "print": {
                "sequence_id": seq,
                "command": "ams_filament_setting",
                "ams_id": slot // 4,
                "tray_id": slot % 4,
                "slot_id": slot % 4,
                "tray_info_idx": tray_info_idx,
                "tray_color": color_rgba,
                "nozzle_temp_min": int(temp_min),
                "nozzle_temp_max": int(temp_max),
                "tray_type": tray_type,
            }
        })
        return seq

    def set_auto_refill(self, enabled: bool) -> str:
        """AMS filament backup / Auto Refill on or off."""
        seq = str(time.time_ns())
        self.publish({
            "print": {"sequence_id": seq, "command": "print_option", "auto_switch_filament": bool(enabled)}
        })
        return seq

    def ams_control(self, param: str) -> str:
        seq = str(time.time_ns())
        self.publish({"print": {"sequence_id": seq, "command": "ams_control", "param": param}})
        return seq

    def command_log(self) -> list[dict]:
        with self._lock:
            return copy.deepcopy(self._command_log)

    def reply(self, sequence_id: str) -> dict | None:
        with self._lock:
            found = self._replies.get(str(sequence_id))
            return dict(found) if found else None

    def start_print(self, filename: str, ams_slot: int, plate_gcode: str = "Metadata/plate_1.gcode"):
        """Start an uploaded 3MF using the already-connected MQTT session."""
        pending = PrintStart(str(time.time_ns()), filename)
        with self._lock:
            if (self._data.get("print") or {}).get("gcode_state") in ("PREPARE", "RUNNING", "PAUSE"):
                raise RuntimeError("프린터가 이미 사용 중입니다. 프린터 화면을 확인해 주세요.")
            self._pending_start = pending
        self.publish({
            "print": {
                "command": "project_file",
                "param": plate_gcode,
                "file": filename,
                "url": f"ftp:///{filename}",
                "sequence_id": pending.sequence_id,
                "bed_leveling": True,
                "bed_type": "textured_plate",
                "flow_cali": True,
                "vibration_cali": True,
                "layer_inspect": False,
                "use_ams": True,
                "ams_mapping": [ams_slot],
                "skip_objects": None,
            }
        })
        return pending

    def wait_for_print_start(self, pending: PrintStart, timeout=PRINT_START_TIMEOUT):
        """Wait for fresh printer activity; MQTT publish alone is not success."""
        deadline = time.monotonic() + timeout
        try:
            with self._start_condition:
                while True:
                    if pending.error:
                        raise RuntimeError(f"출력 시작 실패: {pending.error}")
                    if pending.confirmed:
                        return
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError("파일은 전송되었지만 프린터의 출력 시작을 확인하지 못했습니다. 프린터 화면과 파일을 확인한 뒤 다시 시도해 주세요.")
                    if not self._connected:
                        raise ConnectionError("출력 시작 확인 중 프린터 연결이 끊겼습니다. 프린터 화면을 확인해 주세요.")
                    self._start_condition.wait(min(remaining, 1))
        finally:
            with self._lock:
                if self._pending_start is pending:
                    self._pending_start = None


    def snapshot(self) -> GatewaySnapshot:
        now = time.monotonic()
        with self._lock:
            age = None if self._last_report is None else now - self._last_report
            online = self._connected and age is not None and age <= OFFLINE_AFTER
            error = self._last_error
            if self._connected and self._last_report is None:
                error = "connected; waiting for first report"
            elif self._connected and age is not None and age > OFFLINE_AFTER:
                error = f"no MQTT report for {age:.0f}s"
            return GatewaySnapshot(
                connected=self._connected,
                online=online,
                last_report_age=age,
                data=copy.deepcopy(self._data),
                error=error,
            )


class PrinterGateway:
    def __init__(self):
        self._lock = threading.RLock()
        self._sessions: dict[str, PrinterSession] = {}

    def configure(self, ip, access_code, serial, name):
        if not (ip and access_code and serial):
            return None
        with self._lock:
            current = self._sessions.get(serial)
            key = (ip, access_code, serial)
            if current and current.config_key == key:
                current.name = name
                return current
            if current:
                current.stop()
            session = PrinterSession(ip, access_code, serial, name)
            self._sessions[serial] = session
            session.start()
            return session

    def get(self, serial) -> PrinterSession | None:
        with self._lock:
            return self._sessions.get(serial)

    def remove(self, serial):
        with self._lock:
            session = self._sessions.pop(serial, None)
        if session:
            session.stop()

    def prune(self, active_serials):
        active = set(active_serials)
        with self._lock:
            stale = [serial for serial in self._sessions if serial not in active]
        for serial in stale:
            self.remove(serial)

    def close(self):
        with self._lock:
            sessions = list(self._sessions.values())
            self._sessions.clear()
        for session in sessions:
            session.stop()


gateway = PrinterGateway()
