"""Bed-eject gcode must never leave the A1's envelope (Z-too-low stall)."""
import re

import pytest

from bambu_postprocess import (
    _A1_LIMITS,
    _eject_gcode_nozzle,
    _eject_gcode_rail,
    _validate_eject_gcode,
)


def _axis_values(gcode: str, axis: str) -> list[float]:
    return [float(v) for v in re.findall(rf"\b{axis}(-?\d+(?:\.\d+)?)", gcode)]


@pytest.mark.parametrize("reversed_direction", [False, True])
@pytest.mark.parametrize("make", [_eject_gcode_rail, _eject_gcode_nozzle])
def test_generated_eject_gcode_stays_in_envelope(make, reversed_direction):
    gcode = make(180.0, reversed_direction)
    for axis, (low, high) in _A1_LIMITS.items():
        for value in _axis_values(gcode, axis):
            assert low <= value <= high, (axis, value)
    assert "M211" not in gcode


def test_push_is_fast_but_under_ceiling():
    from bambu_postprocess import _EJECT_MAX_FEED, _EJECT_PUSH_FEED

    assert 6000 <= _EJECT_PUSH_FEED <= _EJECT_MAX_FEED
    for gcode in (_eject_gcode_rail(180.0), _eject_gcode_nozzle(180.0)):
        assert f"F{_EJECT_PUSH_FEED}" in gcode
        assert all(
            float(f) <= _EJECT_MAX_FEED
            for f in re.findall(r"\bF(\d+(?:\.\d+)?)", gcode)
        )


def test_rail_eject_never_sends_negative_z():
    assert min(_axis_values(_eject_gcode_rail(180.0), "Z")) >= 0.0


@pytest.mark.parametrize(
    "bad",
    [
        "G1 Z-4 F600\n",
        "G1 X-60 Y10\n",
        "G1 X300 Y10\n",
        "G1 Y-5 F900\n",
        "G1 Y260 F900\n",
        "G1 Z300\n",
        "M211 X0 Y0 Z0\n",
        "G1 Y5 F30000\n",
    ],
)
def test_validator_rejects_out_of_bounds(bad):
    with pytest.raises(ValueError):
        _validate_eject_gcode(bad)


def test_validator_ignores_comments_and_non_moves():
    ok = "; G1 Z-99\nM17 X0.65 Y1.2 Z0.6\nG4 P1000\nG1 X-48 Y215 F9000\n"
    assert _validate_eject_gcode(ok) == ok
