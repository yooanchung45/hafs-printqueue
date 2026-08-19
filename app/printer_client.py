"""프린터 통신 facade. 실제 MQTT 연결은 printer_gateway가 단독 소유한다."""
import logging
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger("printer_client")


@dataclass
class SlotInfo:
    slot_index: int
    material_type: Optional[str] = None
    color_hex: Optional[str] = None
    color_name: Optional[str] = None
    remaining_percent: Optional[int] = None
    is_empty: bool = True


@dataclass
class PrinterStatusInfo:
    online: bool = False
    state: str = "OFFLINE"
    percentage: Optional[int] = None
    remaining_minutes: Optional[int] = None
    bed_temp: Optional[float] = None
    nozzle_temp: Optional[float] = None
    slots: list = field(default_factory=list)
    error: Optional[str] = None




class PrinterClient:

    def __init__(self, ip=None, access_code=None, serial=None, name="프린터"):
        self.ip = ip
        self.access_code = access_code
        self.serial = serial
        self.name = name

    @property
    def is_mock(self):
        return not (self.ip and self.access_code and self.serial)

    def _gateway_session(self):
        from printer_gateway import gateway
        return gateway.configure(
            self.ip, self.access_code, self.serial, self.name
        )

    def get_status(self):
        if self.is_mock:
            return self._mock_status()
        try:
            snapshot = self._gateway_session().snapshot()
            if not snapshot.online:
                return PrinterStatusInfo(
                    online=False, state="OFFLINE", error=snapshot.error
                )
            values = snapshot.data.get("print") or {}
            state = str(values.get("gcode_state") or "UNKNOWN").upper()
            info = PrinterStatusInfo(online=True, state=state)
            try: info.percentage = int(values.get("mc_percent"))
            except (TypeError, ValueError): pass
            try: info.remaining_minutes = int(values.get("mc_remaining_time"))
            except (TypeError, ValueError): pass
            try: info.bed_temp = float(values.get("bed_temper"))
            except (TypeError, ValueError): pass
            try: info.nozzle_temp = float(values.get("nozzle_temper"))
            except (TypeError, ValueError): pass
            try: info.slots = _ams_slots_from_dump(snapshot.data)
            except Exception as e: logger.debug("AMS 파싱 실패: %s", e)
            return info
        except Exception as e:
            logger.warning("[%s] 상태 읽기 실패: %s", self.name, e)
            return PrinterStatusInfo(online=False, state="OFFLINE", error=str(e))

    def stop(self):
        if self.is_mock:
            return True
        try:
            self._gateway_session().stop_print()
            return True
        except Exception as e:
            logger.warning("[%s] 출력 중단 실패: %s", self.name, e)
            return False

    def set_light(self, on: bool):
        """조명 켜기/끄기. 반환 (성공, 메시지)."""
        if self.is_mock:
            return True, "[Mock] 조명 " + ("켜짐" if on else "꺼짐")
        try:
            self._gateway_session().set_light(on)
            return True, "조명 " + ("켜짐" if on else "꺼짐")
        except Exception as e:
            logger.warning("[%s] 조명 제어 실패: %s", self.name, e)
            return False, f"조명 오류: {e}"

    def _mock_status(self):
        return PrinterStatusInfo(online=False, state="OFFLINE", slots=[
            SlotInfo(0, "PLA", "#FFFFFF", "흰색", 80, False),
            SlotInfo(1, "PLA", "#000000", "검정", 65, False),
            SlotInfo(2, "PLA", "#FF3B30", "빨강", 40, False),
            SlotInfo(3, None, None, None, None, True),
        ])


def _color_name(hex6):
    """#RRGGBB → 가장 가까운 한글 색 이름."""
    if not hex6 or len(hex6) < 7:
        return None
    r, g, b = int(hex6[1:3], 16), int(hex6[3:5], 16), int(hex6[5:7], 16)
    palette = {
        "검정": (0, 0, 0), "흰색": (255, 255, 255), "회색": (128, 128, 128),
        "빨강": (220, 30, 30), "주황": (255, 140, 0), "노랑": (245, 220, 40),
        "초록": (40, 180, 70), "파랑": (40, 90, 210), "보라": (140, 60, 200),
        "분홍": (240, 120, 170),
    }
    best, bestd = None, 1e9
    for name, (pr, pg, pb) in palette.items():
        d = (r - pr) ** 2 + (g - pg) ** 2 + (b - pb) ** 2
        if d < bestd:
            best, bestd = name, d
    return best


def _ams_slots_from_dump(dump):
    """mqtt_dump() → [SlotInfo]. print.ams.ams[*].tray[*] 파싱."""
    out = []
    ams_root = ((dump or {}).get("print") or {}).get("ams") or {}
    for unit in ams_root.get("ams", []):
        try:
            base = int(unit.get("id", 0)) * 4
        except (TypeError, ValueError):
            base = 0
        for tray in unit.get("tray", []):
            try:
                idx = base + int(tray.get("id", 0))
            except (TypeError, ValueError):
                idx = base
            mat = tray.get("tray_type") or ""
            if not mat:                                  # 빈 슬롯: {"id": "n"}만 있음
                out.append(SlotInfo(idx, None, None, None, None, True))
                continue
            raw = (tray.get("tray_color") or "")[:6]     # RRGGBBAA → RRGGBB
            hex6 = "#" + raw.upper() if len(raw) == 6 else None
            remain = tray.get("remain", -1)
            remain = remain if isinstance(remain, int) and 0 <= remain <= 100 else None
            out.append(SlotInfo(idx, mat, hex6, _color_name(hex6), remain, False))
    out.sort(key=lambda s: s.slot_index)
    return out


def _first_loaded_slot(dump):
    """AMS에서 필라멘트가 실제로 든 첫 슬롯 인덱스. 없으면 None."""
    for _s in _ams_slots_from_dump(dump):
        if not _s.is_empty:
            return _s.slot_index
    return None
