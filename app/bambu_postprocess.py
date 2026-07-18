"""Bambu A1 gcode post-processor.

Takes PrusaSlicer-generated gcode and wraps it with the proper
Bambu A1 header block, config block, machine start gcode, and
machine end gcode so the printer accepts and runs it correctly.

Usage:
    from bambu_postprocess import postprocess_for_bambu_a1
    output_path = postprocess_for_bambu_a1("/path/to/input.gcode", nozzle_temp=220, bed_temp=65)
"""

import re
from pathlib import Path


# ── Bambu A1 machine start gcode ─────────────────────────────────────────────
# Extracted and simplified from real Bambu Studio output.
# Handles: homing, bed leveling, filament priming, runout detection.

def _make_start_gcode(nozzle_temp: int, bed_temp: int, filament_type: str = "PLA", estimated_minutes: int = 30) -> str:
    return f"""; FEATURE: Custom
;===== machine: A1 =========================
M73 P0 R{estimated_minutes}
M201 X12000 Y12000 Z1500 E5000
M203 X500 Y500 Z30 E30
M204 P12000 R5000 T12000
M205 X9.00 Y9.00 Z3.00 E3.00
M106 S0
G392 S0
M9833.2

;===== start to heat heatbed and hotend ==========
M1002 gcode_claim_action : 2
M1002 set_filament_type:{filament_type}
M104 S140
M140 S{bed_temp}

;===== avoid end stop =================
G91
G380 S2 Z40 F1200
G380 S3 Z-15 F1200
G90

;===== reset machine status =================
M204 S6000
M630 S0 P0
G91
M17 Z0.3
G90
M17 X0.65 Y1.2 Z0.6
M960 S5 P1 ; turn on logo lamp
G90
M220 S100
M221 S100
M73.2 R1.0
M982.2 S1 ; cog noise reduction

;===== homing =================
M1002 gcode_claim_action : 13
G28 X
G91
G1 Z5 F1200
G90
G0 X128 F30000
G0 Y254 F3000
G91
G1 Z-5 F1200

M109 S25 H140
M17 E0.3
M83
G1 E10 F1200
G1 E-0.5 F30
M17 D

G28 Z P0 T140
M104 S{nozzle_temp}

;===== bed leveling =================
M1002 judge_flag build_plate_detect_flag
M622 S1
  G39.4
  G90
  G1 Z5 F1200
M623

;===== prepare print temperature and material ==========
M1002 gcode_claim_action : 24
M400
M211 X0 Y0 Z0
M975 S1

G90
G1 X-28.5 F30000
G1 X-48.2 F3000

M620 M
M620 S0A
    M1002 gcode_claim_action : 4
    M400
    M1002 set_filament_type:UNKNOWN
    M109 S{nozzle_temp}
    M400
    T0
    G1 X-48.2 F3000
    M400
    M620.1 E F300 T{nozzle_temp}
    M109 S{nozzle_temp}
    M106 P1 S0
    G92 E0
    G1 E50 F200
    M400
    M1002 set_filament_type:{filament_type}
M621 S0A

M109 S{nozzle_temp} H300
G92 E0
G1 E50 F200
M400
M106 P1 S178
G92 E0
G1 E5 F200
M104 S{nozzle_temp}
G92 E0
G1 E-0.5 F300
G1 X-28.5 F30000
G1 X-48.2 F3000
G1 X-28.5 F30000
G1 X-48.2 F3000
M400
M106 P1 S0

;===== filament runout detection ==========
M412 S1
M400 P10
M620.3 W1
M400 S2
M1002 set_filament_type:{filament_type}

;===== start print ==========
M1002 gcode_claim_action : 0
M400
G92 E0
G90
M83
G1 Z5 F3000 ; lift before moving to print start
G1 X128 Y128 F9000 ; move to bed center at safe height
G92 E0
"""


# ── Bambu A1 machine end gcode ────────────────────────────────────────────────

_END_GCODE = """;===== end print ==========
G392 S0
M400
G92 E0
G1 E-0.8 F1800
G1 Z{z_safe} F900
G1 X0 Y128 F18000
G1 X-13.0 F3000

M140 S0
M106 S0
M106 P2 S0

M104 S0

M400
M17 S
M17 Z0.4
G1 Z{z_park} F600
M400 P100
M17 R

G90
G1 X-48 Y180 F3600

M220 S100
M201.2 K1.0
M73.2 R1.0
M1002 set_gcode_claim_speed_level : 0

M400
M18 X Y Z

M73 P100 R0
; EXECUTABLE_BLOCK_END
"""


def _estimate_max_z(gcode_lines: list[str]) -> float:
    """Find the highest Z move in the gcode to compute safe park position."""
    max_z = 10.0
    for line in gcode_lines:
        m = re.search(r'G1[^;]*Z([\d.]+)', line)
        if m:
            z = float(m.group(1))
            if z > max_z:
                max_z = z
    return max_z


_SETUP_CMD = re.compile(
    r'^(M104|M109|M140|M190|M82|M83|G28|G29|G90|G91|G92|G21|M107|M106|T0)\b'
)


def _strip_prusa_start_end(lines: list[str]) -> list[str]:
    """Remove PrusaSlicer's own start/end gcode sequences.

    PrusaSlicer adds M83, G28, M109, etc. at the top and M104 S0 / M140 S0
    at the bottom.  We strip those since our Bambu wrappers handle them.

    Key fix: once setup commands are consumed, the body starts at the VERY
    NEXT non-comment line (which is usually G1 Z0.2 — the first layer height
    move).  The old code waited for the first G1.*E (extrusion) line, which
    caused the Z-move and travel-to-start-point to be silently dropped,
    leaving the nozzle at Z=5 while trying to print layer 1.
    """
    # ── find start ────────────────────────────────────────────────────────────
    start_idx = 0
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or stripped.startswith(';'):
            continue                        # blank / comment → skip
        if _SETUP_CMD.match(stripped):
            start_idx = i + 1              # setup command → consume it
            continue
        # First real non-setup line (G1 Z0.2, G0 travel, etc.) → body starts
        start_idx = i
        break

    # ── find end ─────────────────────────────────────────────────────────────
    # PrusaSlicer's end gcode starts with M104 S0 (nozzle off) or M140 S0
    # (bed off) — but only AFTER we've seen at least one extrusion, so we
    # don't accidentally cut the body short on a line like "M104 S0" that
    # appears inside start setup.
    end_idx = len(lines)
    seen_extrusion = False
    for i in range(start_idx, len(lines)):
        stripped = lines[i].strip()
        if re.match(r'^G1[^;]*E', stripped):
            seen_extrusion = True
        if seen_extrusion:
            if re.match(r'^M104\b', stripped) and re.search(r'\bS0\b', stripped):
                end_idx = i
                break
            if re.match(r'^M140\b', stripped) and re.search(r'\bS0\b', stripped):
                end_idx = i
                break

    return lines[start_idx:end_idx]


def _prusa_time_estimate(all_lines: list) -> int | None:
    """Read PrusaSlicer's own M73 P0 R{n} total-time estimate from the raw gcode.

    With remaining_times=1 in the ini, PrusaSlicer emits M73 P0 R{total_minutes}
    near the start of the file. This is more accurate than our custom estimator.
    """
    for line in all_lines[:500]:
        m = re.match(r'^M73\s+P0\s+R(\d+)', line.strip())
        if m:
            minutes = int(m.group(1))
            return minutes if minutes > 0 else None
    return None


def postprocess_for_bambu_a1(
    input_gcode_path: str,
    nozzle_temp: int = 220,
    bed_temp: int = 65,
    filament_type: str = "PLA",
) -> str:
    """Wrap PrusaSlicer gcode with Bambu A1 header and machine sequences.

    Args:
        input_gcode_path: Path to PrusaSlicer-generated .gcode file
        nozzle_temp: Nozzle temperature in °C
        bed_temp: Bed temperature in °C
        filament_type: Filament type string (PLA, PETG, ABS, etc.)

    Returns:
        Path to the post-processed .gcode file (overwrites input)
    """
    input_path = Path(input_gcode_path)

    with open(input_path, 'r', encoding='utf-8', errors='replace') as f:
        original_lines = f.readlines()

    body_lines = _strip_prusa_start_end(original_lines)

    ps_est = _prusa_time_estimate(original_lines)
    estimated_minutes = ps_est if ps_est is not None else 30
    max_z = _estimate_max_z(body_lines)
    z_safe = round(max_z + 0.5, 1)
    z_park = min(round(max_z + 100.0, 1), 256.0)

    layer_count = sum(1 for l in body_lines if '; layer' in l.lower() or '; LAYER_CHANGE' in l)
    if layer_count == 0:
        layer_count = 185  # fallback estimate

    header = f"""; HEADER_BLOCK_START
; BambuStudio compatible (post-processed by HAFS PrintQueue)
; total layer number: {layer_count}
; max_z_height: {max_z:.2f}
; filament: 1
; HEADER_BLOCK_END

; CONFIG_BLOCK_START
; filament_type = {filament_type}
; nozzle_temperature = {nozzle_temp}
; bed_temperature = {bed_temp}
; curr_bed_type = Textured PEI Plate
; default_filament_profile = Bambu {filament_type} Basic @BBL A1
; CONFIG_BLOCK_END

; EXECUTABLE_BLOCK_START
"""

    start_gcode = _make_start_gcode(nozzle_temp, bed_temp, filament_type, estimated_minutes)

    end_gcode = _END_GCODE.format(
        z_safe=z_safe,
        z_park=z_park,
    )

    final_gcode = ''.join([
        header,
        start_gcode,
        ''.join(body_lines),
        '\n',
        end_gcode,
    ])

    with open(input_path, 'w', encoding='utf-8') as f:
        f.write(final_gcode)

    return str(input_path), estimated_minutes


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python bambu_postprocess.py <gcode_file> [nozzle_temp] [bed_temp]")
        sys.exit(1)
    path = sys.argv[1]
    nozzle = int(sys.argv[2]) if len(sys.argv) > 2 else 220
    bed = int(sys.argv[3]) if len(sys.argv) > 3 else 65
    result = postprocess_for_bambu_a1(path, nozzle_temp=nozzle, bed_temp=bed)
    print(f"Post-processed: {result}")
