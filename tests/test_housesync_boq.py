"""Hand-measured finish quantities and explicit AUD pricing arithmetic."""

from decimal import ROUND_HALF_UP, Decimal

import pytest
from pydantic import ValidationError

from housesync.engine import _boq, build_house
from housesync.schema import HouseSettings


@pytest.fixture(scope="module", params=[10, 12], ids=["10x8", "12x8"])
def measured_house(request, tmp_path_factory):
    length_m = request.param
    result = build_house(HouseSettings(length_mm=length_m * 1000), tmp_path_factory.mktemp(f"boq-{length_m}x8"))
    return length_m, result


def _rows(result):
    return {row["code"]: row for row in result["boq"]}


def test_each_finish_quantity_matches_independent_hand_measurement(measured_house):
    length, result = measured_house
    quantities = result["quantities"]
    # All dimensions here are a hand take-off in metres, independent of IR data.
    clear_area = length * 8
    internal_perimeter = 2 * (length + 8)
    external_perimeter = 2 * (length + 0.4 + 8.4)
    opening_void = 1 * 2.1 + 2 * (1.5 * 1.2)
    internal_faces = internal_perimeter * 3 - opening_void
    external_faces = external_perimeter * 3 - opening_void
    assert quantities["floor_finish_area_m2"] == pytest.approx(clear_area)
    assert quantities["ceiling_area_m2"] == pytest.approx(clear_area)
    assert quantities["skirting_length_m"] == pytest.approx(internal_perimeter - 1)
    assert quantities["internal_plaster_area_m2"] == pytest.approx(internal_faces)
    assert quantities["external_render_area_m2"] == pytest.approx(external_faces)
    assert quantities["paint_area_m2"] == pytest.approx(internal_faces + external_faces)
    assert quantities["wall_volume_m3"] == pytest.approx(20.94 if length == 10 else 23.34)
    assert quantities["roof_area_m2"] == pytest.approx(99 if length == 10 else 117)

    rows = _rows(result)
    assert rows["FLOOR_FINISH"]["net_quantity"] == pytest.approx(clear_area)
    assert rows["CEILING"]["net_quantity"] == pytest.approx(clear_area)
    assert rows["SKIRTING"]["net_quantity"] == pytest.approx(internal_perimeter - 1)
    assert rows["INTERNAL_PLASTER"]["net_quantity"] == pytest.approx(internal_faces)
    assert rows["EXTERNAL_RENDER"]["net_quantity"] == pytest.approx(external_faces)
    assert len(rows) == 11


def test_default_coats_and_wastage_price_gross_quantity(measured_house):
    _, result = measured_house
    rows = _rows(result)
    defaults = {"FLOOR_FINISH": 10, "CEILING": 5, "SKIRTING": 10, "INTERNAL_PLASTER": 5, "EXTERNAL_RENDER": 5}
    for code, row in rows.items():
        waste = defaults.get(code, 0)
        assert row["wastage_percent"] == waste
        expected_gross = Decimal(str(row["net_quantity"])) * (1 + Decimal(waste) / 100)
        assert row["gross_quantity"] == pytest.approx(float(expected_gross))
        assert row["quantity"] == row["gross_quantity"]
        expected_amount = (expected_gross * Decimal(str(row["rate"]))).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        assert row["amount"] == float(expected_amount)
    paint = rows["PAINT"]
    area = result["quantities"]["paint_area_m2"]
    assert paint["measured_quantity"] == area
    assert paint["net_quantity"] == pytest.approx(area * 2)
    assert paint["gross_quantity"] == pytest.approx(area * 2)
    assert f"{area:g}" in paint["description"]
    assert "2" in paint["description"] and "coat" in paint["description"].lower()


def test_default_aud_rates_and_exact_subtotal(measured_house):
    length, result = measured_house
    # An explicit independent price schedule catches missed trades and old SGD rates.
    net = {
        "FLOOR": Decimal(length * 8),
        "WALL": Decimal("20.94" if length == 10 else "23.34"),
        "ROOF": Decimal(99 if length == 10 else 117),
        "PAINT": Decimal("209.4" if length == 10 else "233.4") * 2,
        "DOOR": Decimal(1), "WINDOW": Decimal(2),
        "FLOOR_FINISH": Decimal(length * 8), "CEILING": Decimal(length * 8),
        "SKIRTING": Decimal(35 if length == 10 else 39),
        "INTERNAL_PLASTER": Decimal("102.3" if length == 10 else "114.3"),
        "EXTERNAL_RENDER": Decimal("107.1" if length == 10 else "119.1"),
    }
    rates = {"FLOOR": 95, "WALL": 380, "ROOF": 145, "PAINT": 9, "DOOR": 650, "WINDOW": 650,
             "FLOOR_FINISH": 65, "CEILING": 45, "SKIRTING": 18, "INTERNAL_PLASTER": 28, "EXTERNAL_RENDER": 40}
    waste = {"FLOOR_FINISH": 10, "CEILING": 5, "SKIRTING": 10, "INTERNAL_PLASTER": 5, "EXTERNAL_RENDER": 5}
    amounts = {code: (quantity * (1 + Decimal(waste.get(code, 0)) / 100) * rates[code]).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP) for code, quantity in net.items()}
    for code, row in _rows(result).items():
        assert row["rate"] == rates[code]
        assert row["amount"] == float(amounts[code])
    assert result["totals"] == {"currency": "AUD", "subtotal": float(sum(amounts.values()))}
    assert sum(Decimal(str(row["amount"])) for row in result["boq"]) == sum(amounts.values())


def test_editing_coats_only_preserves_measured_geometry(measured_house):
    _, result = measured_house
    settings = HouseSettings.model_validate(result["settings"])
    settings.paint_coats = 3
    rows = {row["code"]: row for row in _boq(settings, result["quantities"])}
    paint = rows["PAINT"]
    assert paint["measured_quantity"] == result["quantities"]["paint_area_m2"]
    assert paint["net_quantity"] == pytest.approx(paint["measured_quantity"] * 3)
    assert paint["gross_quantity"] == paint["net_quantity"]
    assert paint["amount"] == pytest.approx(paint["gross_quantity"] * 9)
    for code, row in _rows(result).items():
        if code != "PAINT":
            assert rows[code] == row


def test_every_item_wastage_is_editable_including_existing_items(measured_house):
    _, result = measured_house
    settings = HouseSettings.model_validate(result["settings"])
    for field in type(settings.wastage).model_fields:
        setattr(settings.wastage, field, 12.5)
    for row in _boq(settings, result["quantities"]):
        assert row["wastage_percent"] == 12.5
        assert row["gross_quantity"] == pytest.approx(row["net_quantity"] * 1.125)
        assert row["quantity"] == row["gross_quantity"]


def test_legacy_settings_fill_new_defaults_and_preserve_explicit_old_prices():
    legacy = {"project_name": "Saved SGD house", "length_mm": 10000, "width_mm": 8000,
              "currency": "SGD", "rates": {"floor_m2": 80, "wall_m3": 300, "roof_m2": 120, "paint_m2": 12, "opening_each": 500}}
    settings = HouseSettings.model_validate(legacy)
    assert settings.currency == "SGD"
    assert settings.rates.floor_m2 == 80
    assert settings.rates.paint_m2 == 12
    assert settings.rates.floor_finish_m2 == 65
    assert settings.paint_coats == 2
    assert settings.wastage.floor_finish == 10
    assert settings.wastage.ceiling == 5
    assert settings.wastage.skirting == 10
    assert settings.wastage.internal_plaster == 5
    assert settings.wastage.external_render == 5
    assert settings.wastage.paint == 0
    assert len(settings.openings) == 3
    assert settings.model_dump(mode="json")["rate_date"] == "2026-11-17"
    assert "illustrative" in settings.rate_source.lower()
    assert "not a tender estimate" in settings.rate_source.lower()
    assert HouseSettings.model_validate(settings.model_dump(mode="json")) == settings
    assert HouseSettings().currency == "AUD"


@pytest.mark.parametrize("updates", [
    {"paint_coats": 0}, {"paint_coats": 11}, {"paint_coats": 1.5}, {"paint_coats": True},
    {"wastage": {"floor_finish": -1}}, {"wastage": {"ceiling": 101}}, {"wastage": {"paint": float("nan")}},
    {"wastage": {"skirting": float("inf")}}, {"wastage": {"internal_plaster": True}},
    {"wastage": {"unknown_trade": 5}}, {"rates": {"floor_finish_m2": -1}},
    {"rates": {"ceiling_m2": float("nan")}}, {"rates": {"skirting_m": float("inf")}},
    {"rates": {"internal_plaster_m2": -1}}, {"rates": {"external_render_m2": -1}},
    {"rate_source": ""}, {"rate_source": "bad\nsource"}, {"rate_date": "2026-02-30"},
])
def test_new_settings_reject_invalid_values(updates):
    with pytest.raises(ValidationError):
        HouseSettings.model_validate(updates)


def test_opening_edits_change_finish_quantities_without_deducting_windows_from_skirting(tmp_path):
    settings = HouseSettings(wall_thickness_mm=300, openings=[
        {"id": "D1", "kind": "door", "host": "W1", "width_mm": 1200, "height_mm": 2200},
        {"id": "D2", "kind": "door", "host": "W3", "width_mm": 900, "height_mm": 2100},
        {"id": "N1", "kind": "window", "host": "W2", "width_mm": 1800, "height_mm": 1000, "sill_mm": 1000},
    ])
    result = build_house(settings, tmp_path)
    opening_void = 1.2 * 2.2 + 0.9 * 2.1 + 1.8 * 1
    assert result["quantities"]["floor_finish_area_m2"] == 80
    assert result["quantities"]["ceiling_area_m2"] == 80
    assert result["quantities"]["skirting_length_m"] == pytest.approx(36 - 1.2 - 0.9)
    assert result["quantities"]["internal_plaster_area_m2"] == pytest.approx(36 * 3 - opening_void)
    assert result["quantities"]["external_render_area_m2"] == pytest.approx(2 * (10.6 + 8.6) * 3 - opening_void)


@pytest.mark.parametrize("openings", [[], [
    {"id": "D1", "kind": "door", "host": "W1", "width_mm": 1111.123, "height_mm": 2200.234},
    {"id": "N1", "kind": "window", "host": "W2", "width_mm": 1400.567, "height_mm": 1100.789, "sill_mm": 900.123},
]], ids=["plain-faces", "opening-voids"])
def test_fractional_dimensions_retain_all_internal_and_external_wall_faces(tmp_path, openings):
    settings = HouseSettings(length_mm=10000.123, width_mm=8000.456, height_mm=3000.789,
                             wall_thickness_mm=200.321, openings=openings)
    result = build_house(settings, tmp_path)
    length, width, height, thickness = 10.000123, 8.000456, 3.000789, 0.200321
    void_area = sum(opening["width_mm"] * opening["height_mm"] / 1e6 for opening in openings)
    internal_area = 2 * (length + width) * height - void_area
    external_area = 2 * (length + width + 4 * thickness) * height - void_area
    # Use the published six-decimal measurement precision. Rounded browser OBJ
    # vertices cannot reliably identify these exact compiled boundary planes.
    assert result["quantities"]["internal_plaster_area_m2"] == pytest.approx(internal_area, abs=1e-6, rel=0)
    assert result["quantities"]["external_render_area_m2"] == pytest.approx(external_area, abs=1e-6, rel=0)
    assert result["quantities"]["paint_area_m2"] == pytest.approx(internal_area + external_area, abs=1e-6, rel=0)
    assert result["quantities"]["floor_finish_area_m2"] == pytest.approx(length * width, abs=1e-6, rel=0)
    assert result["quantities"]["ceiling_area_m2"] == pytest.approx(length * width, abs=1e-6, rel=0)
    door_width = sum(opening["width_mm"] / 1000 for opening in openings if opening["kind"] == "door")
    assert result["quantities"]["skirting_length_m"] == pytest.approx(2 * (length + width) - door_width, abs=1e-6, rel=0)
