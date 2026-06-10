"""STL 변환 유틸리티.

학생이 뷰어에서 설정한 스케일/회전을 실제 STL 파일에 적용합니다.
바이너리 STL 포맷의 vertex/normal 좌표를 직접 수정합니다.

사용법:
    from stl_transform import apply_transform
    new_path = apply_transform(original_path, scale=0.5, rotation_x=90, rotation_z=-90)
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


def apply_transform(
    input_path: str,
    scale: float = 1.0,
    rotation_x: float = 0.0,
    rotation_y: float = 0.0,
    rotation_z: float = 0.0,
) -> str:
    """
    STL 파일에 스케일/회전 변환을 적용하고 새 파일 경로를 반환합니다.
    변환이 없으면 (scale=1, 모든 rotation=0) 원본 경로를 그대로 반환합니다.

    Args:
        input_path: 원본 STL 파일 경로
        scale: 스케일 배율 (예: 0.5 = 반으로 줄이기, 2.0 = 두 배)
        rotation_x: X축 회전 각도 (도)
        rotation_y: Y축 회전 각도 (도)
        rotation_z: Z축 회전 각도 (도)

    Returns:
        변환된 STL 파일의 경로 (같은 폴더에 새 UUID 이름으로 저장)
    """
    # 변환이 없으면 원본 반환
    no_scale = abs(scale - 1.0) < 1e-6
    no_rot = (abs(rotation_x) < 1e-6 and abs(rotation_y) < 1e-6 and abs(rotation_z) < 1e-6)
    if no_scale and no_rot:
        return input_path

    input_path = Path(input_path)
    data = input_path.read_bytes()

    # ASCII STL → 바이너리로 먼저 변환하면 좋지만,
    # 일단 바이너리 STL만 처리 (Fusion/Bambu Studio 기본 출력은 바이너리)
    # ASCII면 그냥 원본 반환
    try:
        tri_count = struct.unpack_from('<I', data, 80)[0]
        expected = 84 + tri_count * 50
        if len(data) != expected:
            # ASCII STL — 변환 미지원, 원본 반환
            return str(input_path)
    except Exception:
        return str(input_path)

    mat = _rotation_matrix(rotation_x, rotation_y, rotation_z)

    # 새 파일 생성
    out_path = input_path.parent / (uuid.uuid4().hex + ".stl")
    buf = bytearray(data)  # mutable copy

    offset = 84
    for _ in range(tri_count):
        # normal (3 floats)
        nx, ny, nz = struct.unpack_from('<fff', buf, offset)
        nx, ny, nz = _apply_matrix(mat, nx, ny, nz)
        struct.pack_into('<fff', buf, offset, nx, ny, nz)
        offset += 12

        # 3 vertices (each 3 floats)
        for _ in range(3):
            x, y, z = struct.unpack_from('<fff', buf, offset)
            # Apply rotation first, then scale
            x, y, z = _apply_matrix(mat, x, y, z)
            x *= scale; y *= scale; z *= scale
            struct.pack_into('<fff', buf, offset, x, y, z)
            offset += 12

        offset += 2  # attribute byte count

    out_path.write_bytes(buf)
    return str(out_path)
