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
        self._connected = False
        self._last_report = None
        self._last_error = None
        self._data = {}
        self._started = False

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

    def start_print(self, filename: str, ams_slot: int):
        """Start an uploaded 3MF using the already-connected MQTT session."""
        self.publish({
            "print": {
                "command": "project_file",
                "param": "Metadata/plate_1.gcode",
                "file": filename,
                "url": f"ftp:///{filename}",
                "sequence_id": str(time.time_ns()),
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
