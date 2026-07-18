"""PrusaSlicer CLI 래퍼.

STL 파일을 받아서 PrusaSlicer로 슬라이싱 후 Bambu A1용으로 후처리합니다.

사용법:
    from slicer import slice_stl, SlicingError
    try:
        gcode_path = await slice_stl("/app/data/uploads/model.stl")
    except SlicingError as e:
        print(e.message)
"""
import asyncio
import logging
import shutil
from pathlib import Path

logger = logging.getLogger("slicer")

PRUSA_SLICER = "prusa-slicer"

SLICE_TIMEOUT = 300  # 5분 타임아웃


class SlicingError(Exception):
    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


async def slice_stl(
    stl_path: str,
    layer_height: float = 0.2,
    infill: int = 15,
    supports: bool = False,
    nozzle_temp: int = 220,
    bed_temp: int = 65,
    filament_type: str = "PLA",
) -> tuple[str, int]:
    """STL → Bambu A1 호환 .gcode 슬라이싱.

    Args:
        stl_path: 입력 STL 파일 경로
        layer_height: 레이어 높이 (mm)
        infill: 인필 밀도 (%)
        supports: 서포트 사용 여부
        nozzle_temp: 노즐 온도 (°C)
        bed_temp: 베드 온도 (°C)
        filament_type: 필라멘트 종류 (PLA, PETG, ABS 등)

    Returns:
        슬라이싱 + 후처리된 .gcode 파일 경로

    Raises:
        SlicingError: 슬라이싱 실패시
    """
    stl = Path(stl_path)
    if not stl.exists():
        raise SlicingError(f"STL 파일을 찾을 수 없습니다: {stl_path}")

    out_path = stl.with_suffix(".gcode")

    cmd = [
        PRUSA_SLICER,
        "--export-gcode",
        "--load", "/app/slicer_profiles/bambu_a1.ini",
        "--output", str(out_path),
        "--layer-height", str(layer_height),
        "--fill-density", f"{infill}%",
        "--bed-temperature", str(bed_temp),
        "--temperature", str(nozzle_temp),
        "--first-layer-temperature", str(nozzle_temp - 5),
        "--first-layer-bed-temperature", str(bed_temp),
        "--fill-pattern", "gyroid",
        "--perimeters", "3",
    ]

    if supports:
        cmd.append("--support-material")

    cmd.append(str(stl))

    logger.info("슬라이싱 시작: %s", stl.name)
    logger.debug("명령어: %s", " ".join(cmd))

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(),
            timeout=SLICE_TIMEOUT,
        )
    except FileNotFoundError:
        raise SlicingError("PrusaSlicer가 설치되지 않았습니다.")
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except Exception:
            pass
        raise SlicingError("슬라이싱 타임아웃 (5분 초과). 파일이 너무 복잡합니다.")

    combined = (stdout or b"").decode("utf-8", errors="replace")
    if "outside of the print volume" in combined:
        raise SlicingError("모델이 출력 범위 밖에 있습니다. STL 파일을 다시 업로드해 주세요.")

    if proc.returncode != 0:
        err = (stderr or b"").decode("utf-8", errors="replace")[:500]
        logger.error("슬라이싱 실패 (exit %d): %s", proc.returncode, err)
        raise SlicingError(f"슬라이싱 실패: {err}")

    if not out_path.exists():
        raise SlicingError("슬라이싱 완료됐지만 출력 파일이 없습니다.")

    # ── Bambu A1 후처리 ──────────────────────────────────────────────────────
    # PrusaSlicer gcode를 Bambu A1이 인식할 수 있는 형식으로 변환
    estimated_minutes = 30  # fallback if postprocess fails
    try:
        from bambu_postprocess import postprocess_for_bambu_a1
        loop = asyncio.get_event_loop()
        _, estimated_minutes = await loop.run_in_executor(
            None,
            postprocess_for_bambu_a1,
            str(out_path),
            nozzle_temp,
            bed_temp,
            filament_type,
        )
        logger.info("Bambu 후처리 완료: %s (%d분 추정)", out_path.name, estimated_minutes)
    except Exception as e:
        logger.warning("Bambu 후처리 실패 (원본 gcode 유지): %s", e)

    # ── .3mf 패키징 ──────────────────────────────────────────────────────────
    # Bambu A1은 .3mf 포맷을 요구함 (gcode + MD5 + 메타데이터 zip 아카이브)
    try:
        from make_3mf import package_gcode_as_3mf
        loop = asyncio.get_event_loop()
        final_path = await loop.run_in_executor(
            None,
            package_gcode_as_3mf,
            str(out_path),
            nozzle_temp,
            bed_temp,
            filament_type,
        )
        out_path.unlink(missing_ok=True)
        out_path = Path(final_path)
        logger.info(".3mf 패키징 완료: %s", out_path.name)
    except Exception as e:
        logger.warning(".3mf 패키징 실패 (gcode 유지): %s", e)

    size_kb = out_path.stat().st_size / 1024
    logger.info("슬라이싱 완료: %s (%.1f KB)", out_path.name, size_kb)
    return str(out_path), estimated_minutes


def is_slicer_available() -> bool:
    """PrusaSlicer 설치 여부 확인."""
    return shutil.which(PRUSA_SLICER) is not None
