import zipfile

from make_3mf import patch_ams_slot, plate_gcode_name


def _make(path, name):
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(name, "M620 S0A\nT0\n")
        zf.writestr(name + ".md5", "x")
    return str(path)


def test_plate_gcode_name_follows_sliced_plate(tmp_path):
    assert plate_gcode_name(_make(tmp_path / "a.3mf", "Metadata/plate_2.gcode")) == "Metadata/plate_2.gcode"
    assert plate_gcode_name(_make(tmp_path / "b.3mf", "Metadata/plate_1.gcode")) == "Metadata/plate_1.gcode"


def test_plate_gcode_name_falls_back_for_non_archives(tmp_path):
    bad = tmp_path / "c.3mf"
    bad.write_bytes(b"test")
    assert plate_gcode_name(str(bad)) == "Metadata/plate_1.gcode"


def test_patch_ams_slot_patches_plate_2(tmp_path):
    out = patch_ams_slot(_make(tmp_path / "d.3mf", "Metadata/plate_2.gcode"), 2)
    with zipfile.ZipFile(out) as zf:
        assert zf.read("Metadata/plate_2.gcode") == b"M620 S2A\nT2\n"
