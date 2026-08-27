import pytest
from types import SimpleNamespace

from api_serializers import slot_dict
from printer_client import (
    FILAMENT_COLOR_NAMES,
    _ams_slots_from_dump,
    _color_name,
    _normalize_color,
)


@pytest.mark.parametrize(
    ("hex_color", "expected"),
    [
        ("#000000", "검정"),
        ("#808080", "회색"),
        ("#FFFFFF", "흰색"),
        ("#E02020", "빨강"),
        ("#FF8C00", "주황"),
        ("#704020", "갈색"),
        ("#F4EE2A", "노랑"),
        ("#28B446", "초록"),
        ("#20BFC0", "청록"),
        ("#0A2989", "파랑"),
        ("#482960", "보라"),  # A1-1 슬롯 2의 실제 색
        ("#F078AA", "분홍"),
    ],
)
def test_color_name_options(hex_color, expected):
    assert _color_name(hex_color) == expected
    assert expected in FILAMENT_COLOR_NAMES


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("482960FF", "#482960"),
        ("#482960FF", "#482960"),
        ("482960", "#482960"),
        ("not-a-color", None),
        (None, None),
    ],
)
def test_normalize_bambu_color_formats(raw, expected):
    assert _normalize_color(raw) == expected


def test_ams_slot_keeps_purple_hex_and_name():
    dump = {
        "print": {
            "ams": {
                "ams": [{
                    "id": "0",
                    "tray": [{
                        "id": "1",
                        "tray_type": "PLA",
                        "tray_color": "482960FF",
                        "remain": 75,
                    }],
                }]
            }
        }
    }

    assert _ams_slots_from_dump(dump)[0].color_hex == "#482960"
    assert _ams_slots_from_dump(dump)[0].color_name == "보라"


def test_slot_api_repairs_stale_color_name():
    slot = SimpleNamespace(
        id=1,
        printer_id=1,
        slot_index=1,
        material_type="PLA",
        color_hex="#482960",
        color_name="회색",
        remaining_percent=75,
        is_empty=False,
        updated_at=None,
    )

    assert slot_dict(slot)["color_name"] == "보라"
