"""STL 변환 유틸리티.

학생이 뷰어에서 설정한 스케일/회전/위치를 실제 STL 파일에 적용해 베드 중앙에
정렬된 하나의 STL로 합칩니다 (파일이 하나여도 동일하게 처리 -- 업로드된 STL의
원본 좌표는 베드에 맞춰 정렬돼 있다는 보장이 없음). 바이너리 STL 포맷의
vertex/normal 좌표를 직접 수정합니다.

사용법:
    from stl_transform import merge_stls
    new_path = merge_stls([{"path": p, "scale": 0.5, "rotation_z": -90, "x": 0, "y": 0}])
"""
import math
import struct
from pathlib import Path
import uuid


def _rotation_matrix(rx_deg: float, ry_deg: float, rz_deg: float):
    """XYZ 순서 회전 행렬 반환 (3x3 리스트)."""
    rx = math.radians(rx_deg)
    ry = math.radians(ry_deg)
    rz = math.radians(rz_deg)

    # Rotation around X
    Rx = [
        [1, 0,           0          ],
        [0, math.cos(rx), -math.sin(rx)],
        [0, math.sin(rx),  math.cos(rx)],
    ]
    # Rotation around Y
    Ry = [
        [ math.cos(ry), 0, math.sin(ry)],
        [0,             1, 0            ],
        [-math.sin(ry), 0, math.cos(ry)],
    ]
    # Rotation around Z
    Rz = [
        [math.cos(rz), -math.sin(rz), 0],
        [math.sin(rz),  math.cos(rz), 0],
        [0,             0,            1],
    ]

    def matmul(A, B):
        return [[sum(A[i][k]*B[k][j] for k in range(3)) for j in range(3)] for i in range(3)]

    return matmul(matmul(Rz, Ry), Rx)


def _apply_matrix(mat, x, y, z):
    """3x3 행렬을 벡터에 적용."""
    nx = mat[0][0]*x + mat[0][1]*y + mat[0][2]*z
    ny = mat[1][0]*x + mat[1][1]*y + mat[1][2]*z
    nz = mat[2][0]*x + mat[2][1]*y + mat[2][2]*z
    return nx, ny, nz




def merge_stls(parts: list[dict], bed_mm: int = 256) -> str:
    """여러 STL을 한 베드 위에 배치한 단일 바이너리 STL로 합칩니다.

    각 part 는 {path, scale, rotation_x, rotation_y, rotation_z, x, y} 형태이며
    x/y 는 베드 중앙 기준 부품 풋프린트 중심의 mm 오프셋입니다. 각 부품은
    회전→스케일 적용 후 바닥(Z=0)에 내려앉히고 (bed_mm/2 + x, bed_mm/2 + y)
    위치에 놓습니다. 삼각형을 이어붙여 하나의 STL로 저장합니다.

    Raises:
        SlicingError: 바이너리가 아닌(ASCII 등) STL 이 섞여 있을 때
    """
    from slicer import SlicingError

    if not parts:
        raise SlicingError("배치할 STL이 없습니다")

    body = bytearray()
    total_tris = 0
    out_dir = Path(parts[0]["path"]).parent

    for part in parts:
        source = Path(part["path"])
        data = source.read_bytes()
        try:
            tri_count = struct.unpack_from("<I", data, 80)[0]
        except Exception:
            raise SlicingError(f"STL을 읽을 수 없습니다: {source.name}")
        if len(data) != 84 + tri_count * 50:
            raise SlicingError(f"바이너리 STL만 여러 개 배치할 수 있습니다: {source.name}")

        mat = _rotation_matrix(
            float(part.get("rotation_x", 0.0)),
            float(part.get("rotation_y", 0.0)),
            float(part.get("rotation_z", 0.0)),
        )
        scale = float(part.get("scale", 1.0))

        # Pass 1 — bounds of the rotated + scaled part (translation comes after).
        min_x = min_y = min_z = math.inf
        max_x = max_y = -math.inf
        offset = 84
        for _ in range(tri_count):
            offset += 12  # skip the facet normal
            for _ in range(3):
                x, y, z = struct.unpack_from("<fff", data, offset)
                x, y, z = _apply_matrix(mat, x, y, z)
                x *= scale; y *= scale; z *= scale
                min_x = x if x < min_x else min_x
                max_x = x if x > max_x else max_x
                min_y = y if y < min_y else min_y
                max_y = y if y > max_y else max_y
                min_z = z if z < min_z else min_z
                offset += 12
            offset += 2

        tx = bed_mm / 2.0 + float(part.get("x", 0.0)) - (min_x + max_x) / 2.0
        ty = bed_mm / 2.0 + float(part.get("y", 0.0)) - (min_y + max_y) / 2.0

        # Authoritative bed-fit check on the actual transformed geometry --
        # doesn't depend on the frontend's bounds check, which reports each
        # part's size back asynchronously (one render cycle after a scale
        # change) and can be raced by submitting right after a resize.
        final_min_x, final_max_x = tx + min_x, tx + max_x
        final_min_y, final_max_y = ty + min_y, ty + max_y
        if final_min_x < -1e-6 or final_max_x > bed_mm + 1e-6 or \
           final_min_y < -1e-6 or final_max_y > bed_mm + 1e-6:
            raise SlicingError(
                f"{source.name}: 출력 범위(256×256mm)를 벗어났습니다 "
                f"(크기 {max_x - min_x:.1f}×{max_y - min_y:.1f}mm). "
                "크기를 줄이거나 위치를 조정해 주세요."
            )
        tz = -min_z

        # Pass 2 — emit the transformed triangles into the shared body.
        offset = 84
        for _ in range(tri_count):
            nx, ny, nz = _apply_matrix(mat, *struct.unpack_from("<fff", data, offset))
            body += struct.pack("<fff", nx, ny, nz)
            offset += 12
            for _ in range(3):
                x, y, z = struct.unpack_from("<fff", data, offset)
                x, y, z = _apply_matrix(mat, x, y, z)
                body += struct.pack("<fff", x * scale + tx, y * scale + ty, z * scale + tz)
                offset += 12
            body += b"\x00\x00"
            offset += 2

        total_tris += tri_count

    out_path = out_dir / (uuid.uuid4().hex + ".stl")
    out_path.write_bytes(b"\x00" * 80 + struct.pack("<I", total_tris) + bytes(body))
    return str(out_path)
