"""PrusaSlicer CLI 래퍼.

STL 파일을 받아서 PrusaSlicer로 슬라이싱 후 .gcode 파일 경로를 반환합니다.
Bambu A1 기본 설정 사용.

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

# Bambu A1 기본 슬라이싱 설정
# 나중에 학생이 업로드 폼에서 선택할 수 있게 확장 가능
DEFAULT_SETTINGS = {
    "layer-height": "0.2",
    "fill-density": "15%",
    "perimeters": "3",
    "support-material": False,
    "bed-temperature": "65",
    "temperature": "220",        # nozzle
    "first-layer-temperature": "215",
    "first-layer-bed-temperature": "65",
    "--fill-pattern": "gyroid",
}

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
) -> str:
    """STL → .gcode 슬라이싱.

    Args:
        stl_path: 입력 STL 파일 경로
        layer_height: 레이어 높이 (mm)
        infill: 인필 밀도 (%)
        supports: 서포트 사용 여부

    Returns:
        슬라이싱된 .gcode 파일 경로

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
        "--output", str(out_path),
        "--layer-height", str(layer_height),
        "--fill-density", f"{infill}%",
        "--bed-temperature", "65",
        "--temperature", "220",
        "--first-layer-temperature", "215",
        "--first-layer-bed-temperature", "65",
        "--fill-pattern", "gyroid",
        "--perimeters", "3",
    ]

    if supports:
        cmd.append("--support-material")

    # 슬라이싱은 CPU를 많이 쓰므로 asyncio로 subprocess 실행
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

    if proc.returncode != 0:
        err = stderr.decode("utf-8", errors="replace")[:500]
        logger.error("슬라이싱 실패 (exit %d): %s", proc.returncode, err)
        raise SlicingError(f"슬라이싱 실패: {err}")

    if not out_path.exists():
        raise SlicingError("슬라이싱 완료됐지만 출력 파일이 없습니다.")

    size_kb = out_path.stat().st_size / 1024
    logger.info("슬라이싱 완료: %s (%.1f KB)", out_path.name, size_kb)
    return str(out_path)


def is_slicer_available() -> bool:
    """PrusaSlicer 설치 여부 확인."""
    return shutil.which(PRUSA_SLICER) is not None
