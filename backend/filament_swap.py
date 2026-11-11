"""필라멘트 소진으로 일시정지된 출력을 다른 AMS 슬롯으로 이어서 출력한다.

Bambu Studio가 일시정지 중 AMS 패널에서 하는 것과 같은 순서:
  1. ams_change_filament(target=새 슬롯) -- 툴헤드에 남은 필라멘트를 빼고 새 슬롯을 넣고 퍼지
  2. 프린터의 에러 창 닫기 (clean_print_error + uiop close)
  3. resume
각 단계는 MQTT 발행만으로 성공 처리하지 않고, 프린터 보고(tray_now, ams_status,
gcode_state)로 실제 결과를 확인한다.
"""
from __future__ import annotations

import logging
import time
from typing import Callable

logger = logging.getLogger("filament_swap")

# print_error 하위 16비트. 상위 16비트는 모듈(0x07 AMS, 0x12 AMS lite)과 AMS 번호.
RUNOUT_ERROR_LOW = 0x8011
# HMS code: 슬롯 필라멘트 소진 / 소진 후 이전 필라멘트 퍼지 이상
RUNOUT_HMS_CODES = {0x00020001, 0x00020005}
AMS_MODULES = {0x07, 0x12}

AMS_MAIN_IDLE = 0x00
AMS_MAIN_FILAMENT_CHANGE = 0x01

DEFAULT_TEMP = 220
LOAD_TIMEOUT = 420
RESUME_TIMEOUT = 120
RESUME_SETTLE = 20
POLL = 0.5


class SwapError(RuntimeError):
    pass


def format_error(code: int | None) -> str | None:
    if not code:
        return None
    code = int(code) & 0xFFFFFFFF
    return f"{code >> 16:04X}-{code & 0xFFFF:04X}"


def _int(value, default=None):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _print(data: dict) -> dict:
    return (data or {}).get("print") or {}


def _trays(data: dict) -> dict[int, dict]:
    out = {}
    for unit in (_print(data).get("ams") or {}).get("ams", []) or []:
        base = _int(unit.get("id"), 0) * 4
        for tray in unit.get("tray", []) or []:
            out[base + _int(tray.get("id"), 0)] = tray
    return out


def tray_temp(data: dict, slot: int | None) -> int | None:
    """Studio와 같이 스풀 노즐 온도 범위의 중간값."""
    if slot is None:
        return None
    tray = _trays(data).get(slot) or {}
    lo, hi = _int(tray.get("nozzle_temp_min")), _int(tray.get("nozzle_temp_max"))
    if lo and hi:
        return (lo + hi) // 2
    return None


def tray_now(data: dict) -> int | None:
    value = _int((_print(data).get("ams") or {}).get("tray_now"))
    return value if value is not None and 0 <= value < 16 else None


def ams_main(data: dict) -> int | None:
    status = _int(_print(data).get("ams_status"))
    return None if status is None else (status >> 8) & 0xFF


def _hms_runout_slot(data: dict) -> int | None:
    for item in _print(data).get("hms") or []:
        attr, code = _int(item.get("attr"), 0), _int(item.get("code"), 0)
        if (attr >> 24) & 0xFF not in AMS_MODULES or code not in RUNOUT_HMS_CODES:
            continue
        slot_byte = (attr >> 8) & 0xFF
        if 0x20 <= slot_byte <= 0x23:
            return ((attr >> 16) & 0xFF) * 4 + (slot_byte - 0x20)
    return None


def is_runout_error(code: int | None) -> bool:
    if not code:
        return False
    code = int(code) & 0xFFFFFFFF
    return (code & 0xFFFF) == RUNOUT_ERROR_LOW and (code >> 24) in AMS_MODULES


def pause_info(data: dict) -> dict | None:
    """일시정지 상태일 때 원인 요약. 일시정지가 아니면 None."""
    values = _print(data)
    if str(values.get("gcode_state") or "").upper() != "PAUSE":
        return None
    error = _int(values.get("print_error"), 0) or 0
    hms_slot = _hms_runout_slot(data)
    return {
        "print_error": format_error(error),
        "runout": is_runout_error(error) or hms_slot is not None,
        "runout_slot": hms_slot,
        "tray_now": tray_now(data),
    }


def live_pause_info(printer) -> dict | None:
    """관리자 화면용: 게이트웨이 캐시에서 바로 읽는다 (MQTT 요청 없음)."""
    if not printer.serial:
        return None
    from printer_gateway import gateway
    session = gateway.get(printer.serial)
    if session is None:
        return None
    snapshot = session.snapshot()
    if not snapshot.online:
        return None
    return pause_info(snapshot.data)


def run_swap(
    session,
    target_slot: int,
    progress: Callable[[str, str, int | None], None] = lambda *_: None,
    *,
    old_slot_hint: int | None = None,
    load_timeout: float = LOAD_TIMEOUT,
    resume_timeout: float = RESUME_TIMEOUT,
    settle: float = RESUME_SETTLE,
    poll: float = POLL,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> str:
    """일시정지된 출력을 target_slot 필라멘트로 이어서 출력한다. 블로킹."""
    data = session.snapshot().data
    values = _print(data)
    if str(values.get("gcode_state") or "").upper() != "PAUSE":
        raise SwapError("프린터가 일시정지 상태가 아닙니다. 상태를 동기화한 뒤 다시 시도해 주세요.")
    target_tray = _trays(data).get(target_slot) or {}
    if not target_tray.get("tray_type"):
        raise SwapError(f"슬롯 {target_slot + 1}에 필라멘트가 없습니다.")

    original_error = _int(values.get("print_error"), 0) or 0
    subtask_id = values.get("subtask_id")
    old_slot = _hms_runout_slot(data)
    if old_slot is None:
        old_slot = tray_now(data)
    if old_slot is None:
        old_slot = old_slot_hint

    tar_temp = tray_temp(data, target_slot) or DEFAULT_TEMP
    curr_temp = tray_temp(data, old_slot) or tar_temp

    def state_of(snapshot_data):
        return str(_print(snapshot_data).get("gcode_state") or "").upper()

    def fail_if_stopped(snapshot_data):
        if state_of(snapshot_data) in ("FAILED", "IDLE", "FINISH"):
            raise SwapError("필라멘트 교체 중 출력이 종료되었습니다. 프린터 화면을 확인해 주세요.")

    def new_error(snapshot_data):
        code = _int(_print(snapshot_data).get("print_error"), 0) or 0
        if code and code != original_error and not is_runout_error(code):
            return code
        return None

    # 1) 필라멘트 교체
    if tray_now(data) == target_slot and ams_main(data) == AMS_MAIN_IDLE:
        progress("loading", f"슬롯 {target_slot + 1}이 이미 장착되어 있어 교체를 건너뜁니다", 60)
    else:
        progress("loading", f"슬롯 {target_slot + 1} 필라멘트로 교체 중 (노즐 {tar_temp}°)", 10)
        seq = session.change_filament(target_slot, curr_temp, tar_temp)
        started_at = clock()
        saw_change = False
        while True:
            reply = session.reply(seq)
            if reply and reply.get("result") in ("FAIL", "FAILED", "ERROR"):
                raise SwapError(f"프린터가 필라멘트 교체를 거절했습니다: {reply.get('reason') or '사유 미상'}")
            snap = session.snapshot()
            if not snap.connected:
                raise SwapError("필라멘트 교체 중 프린터 연결이 끊겼습니다. 프린터 화면을 확인해 주세요.")
            fail_if_stopped(snap.data)
            code = new_error(snap.data)
            if code:
                raise SwapError(
                    f"필라멘트 교체 중 프린터 오류 ({format_error(code)}). 프린터 화면을 확인해 주세요."
                )
            main = ams_main(snap.data)
            now_slot = tray_now(snap.data)
            if main == AMS_MAIN_FILAMENT_CHANGE or (now_slot is not None and now_slot != old_slot):
                saw_change = True
            elapsed = clock() - started_at
            if (main == AMS_MAIN_IDLE and now_slot == target_slot
                    and (saw_change or elapsed >= 10)):
                break
            if elapsed >= load_timeout:
                raise SwapError("필라멘트 교체 완료를 확인하지 못했습니다. 프린터 화면을 확인해 주세요.")
            pct = min(55, 10 + int(elapsed / load_timeout * 45 * 3))
            progress("loading", f"슬롯 {target_slot + 1} 필라멘트로 교체 중 (노즐 {tar_temp}°)", pct)
            sleep(poll)

    # 2) 에러 창 닫기
    current_error = _int(_print(session.snapshot().data).get("print_error"), 0) or 0
    if current_error:
        progress("resuming", "프린터 오류 알림을 닫는 중", 70)
        session.clear_print_error(subtask_id, current_error)
        sleep(1)

    # 3) 재개
    progress("resuming", "출력 재개 명령 전송 중", 80)
    seq = session.resume_print()
    try:
        session.request_full_status()
    except Exception:
        pass
    started_at = clock()
    running_since = None
    while True:
        reply = session.reply(seq)
        if reply and reply.get("result") in ("FAIL", "FAILED", "ERROR"):
            raise SwapError(f"프린터가 출력 재개를 거절했습니다: {reply.get('reason') or '사유 미상'}")
        snap = session.snapshot()
        if not snap.connected:
            raise SwapError("출력 재개 중 프린터 연결이 끊겼습니다. 프린터 화면을 확인해 주세요.")
        fail_if_stopped(snap.data)
        state = state_of(snap.data)
        now = clock()
        if state in ("RUNNING", "PREPARE"):
            if running_since is None:
                running_since = now
                progress("resuming", "출력이 재개되었습니다. 안정적으로 이어지는지 확인 중", 90)
            elif now - running_since >= settle:
                return f"슬롯 {target_slot + 1} 필라멘트로 출력을 이어서 진행합니다"
        elif state == "PAUSE":
            code = _int(_print(snap.data).get("print_error"), 0) or 0
            # The cached print_error can lag the dismissal, so only trust it once a
            # full status (requested right after resume) has had time to arrive.
            if running_since is not None or (code and now - started_at >= 15):
                detail = f" (오류 {format_error(code)})" if code else ""
                raise SwapError(f"출력이 재개되지 않고 다시 일시정지되었습니다{detail}. 프린터 화면을 확인해 주세요.")
        if running_since is None and now - started_at >= resume_timeout:
            raise SwapError("필라멘트는 교체했지만 출력 재개를 확인하지 못했습니다. 프린터 화면에서 재개해 주세요.")
        sleep(poll)
