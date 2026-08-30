"""Kinematic time sim + progressive M73 injection in bambu_postprocess."""
from bambu_postprocess import _inject_progress_markers, _move_seconds, _simulate_print


def _layer(print_mm: float) -> list[str]:
    return [
        ";LAYER_CHANGE\n",
        ";Z:0.2\n",
        f"G1 X{print_mm} Y0 E4 F1800\n",   # print move, 30 mm/s
        "G1 X0 Y0 F9000\n",                # travel back, 150 mm/s
        "M73 P1 R500\n",                    # PrusaSlicer stray — must be dropped
    ]


def test_move_seconds_trapezoid_vs_triangle():
    # long move: reaches feedrate, ~ dist / v
    assert _move_seconds(200.0, 50.0) > _move_seconds(20.0, 50.0)
    # zero / degenerate
    assert _move_seconds(0.0, 50.0) == 0.0
    assert _move_seconds(10.0, 0.0) == 0.0


def test_simulate_scales_with_work():
    short = _simulate_print(_layer(50.0) * 3)[0]
    long = _simulate_print(_layer(150.0) * 3)[0]
    assert long > short > 0


def test_inject_replaces_stray_m73_with_time_weighted_markers():
    body = _layer(120.0) * 4
    total_s, layer_cum = _simulate_print(body)
    out = _inject_progress_markers(body, total_s, layer_cum)

    assert not any("M73 P1 R500" in line for line in out)          # stray dropped
    markers = [line.strip() for line in out if line.startswith("M73 P")]
    assert len(markers) >= 2                                        # ours added
    pcts = [int(m.split()[1][1:]) for m in markers]                 # "P37" -> 37
    assert pcts == sorted(pcts)                                     # monotone up
    assert pcts[0] == 0 and pcts[-1] <= 99


def test_inject_bails_when_sim_is_empty():
    body = ["G1 X0 Y0\n", "M73 P5 R9\n"]
    out = _inject_progress_markers(body, 0.0, [])
    assert not any(line.lstrip().startswith("M73") for line in out)
