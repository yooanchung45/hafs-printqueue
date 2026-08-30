"""merge_stls: several STL parts -> one bed-positioned binary STL."""
import struct
from pathlib import Path

import pytest

from stl_transform import merge_stls


def _cube_stl(path: Path, size: float) -> None:
    """Write an axis-aligned binary-STL cube spanning (0,0,0)..(size,size,size)."""
    s = size
    corners = [
        (0, 0, 0), (s, 0, 0), (s, s, 0), (0, s, 0),
        (0, 0, s), (s, 0, s), (s, s, s), (0, s, s),
    ]
    faces = [
        (0, 1, 2), (0, 2, 3),  # bottom
        (4, 6, 5), (4, 7, 6),  # top
        (0, 5, 1), (0, 4, 5),  # front
        (2, 7, 3), (2, 6, 7),  # back
        (1, 6, 2), (1, 5, 6),  # right
        (0, 3, 7), (0, 7, 4),  # left
    ]
    buf = bytearray(b"\x00" * 80)
    buf += struct.pack("<I", len(faces))
    for a, b, c in faces:
        buf += struct.pack("<fff", 0.0, 0.0, 0.0)  # normal (slicer recomputes)
        for idx in (a, b, c):
            buf += struct.pack("<fff", *(float(v) for v in corners[idx]))
        buf += b"\x00\x00"
    path.write_bytes(buf)


def _read_vertices(path: str):
    data = Path(path).read_bytes()
    count = struct.unpack_from("<I", data, 80)[0]
    assert len(data) == 84 + count * 50
    verts = []
    offset = 84
    for _ in range(count):
        offset += 12  # normal
        for _ in range(3):
            verts.append(struct.unpack_from("<fff", data, offset))
            offset += 12
        offset += 2
    return count, verts


def test_merge_positions_parts_on_the_bed(tmp_path):
    a = tmp_path / "a.stl"
    b = tmp_path / "b.stl"
    _cube_stl(a, 10.0)
    _cube_stl(b, 20.0)

    out = merge_stls([
        {"path": str(a), "scale": 1.0, "rotation_x": 0, "rotation_y": 0, "rotation_z": 0, "x": -50.0, "y": 0.0},
        {"path": str(b), "scale": 1.0, "rotation_x": 0, "rotation_y": 0, "rotation_z": 0, "x": 40.0, "y": 30.0},
    ])

    count, verts = _read_vertices(out)
    assert count == 24  # 12 + 12 triangles
    xs = [v[0] for v in verts]
    ys = [v[1] for v in verts]
    zs = [v[2] for v in verts]

    assert min(zs) == pytest.approx(0.0, abs=1e-4)   # both cubes sit on Z=0
    assert max(zs) == pytest.approx(20.0, abs=1e-4)  # tallest part
    # cube A footprint centre -> (128-50, 128) => x in [73, 83]
    # cube B footprint centre -> (128+40, 128+30) => x in [158, 178]
    assert min(xs) == pytest.approx(73.0, abs=1e-3)
    assert max(xs) == pytest.approx(178.0, abs=1e-3)
    assert min(ys) == pytest.approx(123.0, abs=1e-3)
    assert max(ys) == pytest.approx(168.0, abs=1e-3)


def test_merge_applies_scale(tmp_path):
    a = tmp_path / "a.stl"
    _cube_stl(a, 10.0)
    out = merge_stls([
        {"path": str(a), "scale": 2.0, "rotation_x": 0, "rotation_y": 0, "rotation_z": 0, "x": 0.0, "y": 0.0},
    ])
    _, verts = _read_vertices(out)
    xs = [v[0] for v in verts]
    assert max(xs) - min(xs) == pytest.approx(20.0, abs=1e-3)  # 10mm * 2
    assert min(v[2] for v in verts) == pytest.approx(0.0, abs=1e-4)


def test_merge_rejects_ascii_stl(tmp_path):
    p = tmp_path / "ascii.stl"
    p.write_text(
        "solid cube\nfacet normal 0 0 0\nouter loop\n"
        "vertex 0 0 0\nvertex 1 0 0\nvertex 0 1 0\n"
        "endloop\nendfacet\nendsolid cube\n"
    )
    with pytest.raises(Exception):
        merge_stls([{"path": str(p), "x": 0.0, "y": 0.0}])
