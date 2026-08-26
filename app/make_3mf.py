"""Package gcode into a Bambu-compatible .3mf file.

The Bambu A1 requires a .3mf package (zip archive) containing:
- Metadata/plate_1.gcode        (the actual gcode)
- Metadata/plate_1.gcode.md5   (MD5 checksum of the gcode)
- Metadata/plate_1.json        (bed/filament metadata)
- Metadata/slice_info.config   (printer/slicer metadata)
- Metadata/plate_1.png         (thumbnail — we use a blank 1x1 PNG)
- Metadata/plate_1_small.png   (small thumbnail)
- [Content_Types].xml
- _rels/.rels
- 3D/3dmodel.model             (minimal 3MF model file)

Usage:
    from make_3mf import package_gcode_as_3mf
    path_3mf = package_gcode_as_3mf("/path/to/model.gcode", nozzle_temp=220, bed_temp=65)
"""

import hashlib
import io
import json
import re
import zipfile
from pathlib import Path


# Minimal 1x1 transparent PNG (67 bytes)
_BLANK_PNG = bytes([
    0x89,0x50,0x4e,0x47,0x0d,0x0a,0x1a,0x0a,0x00,0x00,0x00,0x0d,0x49,0x48,0x44,0x52,
    0x00,0x00,0x00,0x01,0x00,0x00,0x00,0x01,0x08,0x02,0x00,0x00,0x00,0x90,0x77,0x53,
    0xde,0x00,0x00,0x00,0x0c,0x49,0x44,0x41,0x54,0x08,0xd7,0x63,0xf8,0xcf,0xc0,0x00,
    0x00,0x00,0x02,0x00,0x01,0xe2,0x21,0xbc,0x33,0x00,0x00,0x00,0x00,0x49,0x45,0x4e,
    0x44,0xae,0x42,0x60,0x82,
])

_CONTENT_TYPES = '''<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
 <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
 <Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/>
 <Default Extension="png" ContentType="image/png"/>
 <Default Extension="gcode" ContentType="text/x.gcode"/>
</Types>'''

_RELS = '''<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
 <Relationship Target="/3D/3dmodel.model" Id="rel-1" Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/>
 <Relationship Target="/Metadata/plate_1.png" Id="rel-2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/thumbnail"/>
 <Relationship Target="/Metadata/plate_1.png" Id="rel-4" Type="http://schemas.bambulab.com/package/2021/cover-thumbnail-middle"/>
 <Relationship Target="/Metadata/plate_1_small.png" Id="rel-5" Type="http://schemas.bambulab.com/package/2021/cover-thumbnail-small"/>
</Relationships>'''

_3DMODEL = '''<?xml version="1.0" encoding="UTF-8"?>
<model unit="millimeter" xml:lang="en-US"
  xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02"
  xmlns:BambuStudio="http://schemas.bambulab.com/package/2021">
  <metadata name="Application">BambuStudio</metadata>
  <resources>
    <object id="1" type="model">
      <mesh>
        <vertices><vertex x="0" y="0" z="0"/></vertices>
        <triangles></triangles>
      </mesh>
    </object>
  </resources>
  <build><item objectid="1"/></build>
</model>'''


def _make_slice_info(nozzle_temp: int, filament_type: str, layer_count: int, weight_g: float = 10.0) -> str:
    return f'''<?xml version="1.0" encoding="UTF-8"?>
<config>
  <header>
    <header_item key="X-BBL-Client-Type" value="slicer"/>
    <header_item key="X-BBL-Client-Version" value="02.06.00.51"/>
  </header>
  <plate>
    <metadata key="index" value="1"/>
    <metadata key="extruder_type" value="0"/>
    <metadata key="nozzle_volume_type" value="0"/>
    <metadata key="printer_model_id" value="N2S"/>
    <metadata key="nozzle_diameters" value="0.4"/>
    <metadata key="timelapse_type" value="0"/>
    <metadata key="prediction" value="3600"/>
    <metadata key="weight" value="{weight_g:.2f}"/>
    <metadata key="outside" value="false"/>
    <metadata key="support_used" value="false"/>
    <metadata key="label_object_enabled" value="false"/>
    <metadata key="filament_maps" value="1"/>
    <filament id="1" tray_info_idx="GFA00" type="{filament_type}" color="#FFFFFF"
              used_m="100.00" used_g="{weight_g:.2f}" group_id="0"
              nozzle_diameter="0.40" volume_type="Standard"
              used_for_object="true" used_for_support="false"/>
    <nozzle id="0" extruder_id="1" nozzle_diameter="0.4" volume_type="Standard"/>
    <layer_filament_lists>
      <layer_filament_list filament_list="0" layer_ranges="0 {layer_count}" />
    </layer_filament_lists>
  </plate>
</config>'''


def _make_plate_json(nozzle_temp: int, bed_temp: int, filament_type: str) -> str:
    data = {
        "bbox_all": [10.0, 10.0, 246.0, 246.0],
        "bbox_objects": [{
            "area": 10000.0,
            "bbox": [10.0, 10.0, 246.0, 246.0],
            "id": 1,
            "layer_height": 0.2,
            "name": "model.stl",
        }],
        "bed_type": "textured_plate",
        "filament_colors": ["#FFFFFF"],
        "filament_ids": [0],
        "first_extruder": 0,
        "first_layer_time": 120.0,
        "is_seq_print": False,
        "nozzle_diameter": 0.4,
        "version": 2,
    }
    return json.dumps(data)


def _count_layers(gcode: str) -> int:
    count = sum(1 for line in gcode.splitlines()
                if '; layer' in line.lower() or '; LAYER_CHANGE' in line)
    return count if count > 0 else 100


def package_gcode_as_3mf(
    gcode_path: str,
    nozzle_temp: int = 220,
    bed_temp: int = 65,
    filament_type: str = "PLA",
) -> str:
    """Package a gcode file into a Bambu-compatible .3mf archive.

    Args:
        gcode_path: Path to the .gcode file
        nozzle_temp: Nozzle temperature in °C
        bed_temp: Bed temperature in °C
        filament_type: Filament type string (PLA, PETG, ABS, etc.)

    Returns:
        Path to the output .3mf file (same directory, .3mf extension)
    """
    gcode_path = Path(gcode_path)
    gcode_bytes = gcode_path.read_bytes()
    gcode_str = gcode_bytes.decode('utf-8', errors='replace')

    md5 = hashlib.md5(gcode_bytes).hexdigest().upper()
    layer_count = _count_layers(gcode_str)

    out_path = gcode_path.with_suffix('.3mf')

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        zf.writestr('[Content_Types].xml', _CONTENT_TYPES)
        zf.writestr('_rels/.rels', _RELS)
        zf.writestr('3D/3dmodel.model', _3DMODEL)
        zf.writestr('Metadata/plate_1.gcode', gcode_bytes)
        zf.writestr('Metadata/plate_1.gcode.md5', md5)
        zf.writestr('Metadata/plate_1.json', _make_plate_json(nozzle_temp, bed_temp, filament_type))
        zf.writestr('Metadata/slice_info.config', _make_slice_info(nozzle_temp, filament_type, layer_count))
        zf.writestr('Metadata/plate_1.png', _BLANK_PNG)
        zf.writestr('Metadata/plate_1_small.png', _BLANK_PNG)

    out_path.write_bytes(buf.getvalue())
    return str(out_path)


def patch_ams_slot(threemf_path: str, slot: int) -> str:
    """Replace AMS slot 0 references with the correct slot in a .3mf archive.

    Rewrites the gcode inside the archive and updates the MD5 checksum.
    Returns path to a new temp .3mf file (caller must delete it).
    """
    import os
    import tempfile

    entries: dict[str, bytes] = {}
    with zipfile.ZipFile(threemf_path, 'r') as zin:
        for name in zin.namelist():
            entries[name] = zin.read(name)

    if 'Metadata/plate_1.gcode' in entries:
        gcode = entries['Metadata/plate_1.gcode'].decode('utf-8', errors='replace')
        gcode = gcode.replace('M620 S0A', f'M620 S{slot}A')
        gcode = gcode.replace('M621 S0A', f'M621 S{slot}A')
        # T0 selects the physical AMS slot to pull filament from — must move
        # with the M620/M621 slot tag or the printer keeps loading from slot 0
        # no matter what the wrapper says.
        gcode = re.sub(r'(?m)^(\s*)T0(\s|$)', rf'\1T{slot}\2', gcode)
        gcode_bytes = gcode.encode('utf-8')
        entries['Metadata/plate_1.gcode'] = gcode_bytes
        entries['Metadata/plate_1.gcode.md5'] = (
            hashlib.md5(gcode_bytes).hexdigest().upper().encode()
        )

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zout:
        for name, data in entries.items():
            zout.writestr(name, data)

    tmp = tempfile.NamedTemporaryFile(suffix='.3mf', delete=False)
    tmp.write(buf.getvalue())
    tmp.close()
    return tmp.name


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python make_3mf.py <gcode_file> [nozzle_temp] [bed_temp]")
        sys.exit(1)
    path = sys.argv[1]
    nozzle = int(sys.argv[2]) if len(sys.argv) > 2 else 220
    bed = int(sys.argv[3]) if len(sys.argv) > 3 else 65
    result = package_gcode_as_3mf(path, nozzle_temp=nozzle, bed_temp=bed)
    print(f"Packaged: {result}")
