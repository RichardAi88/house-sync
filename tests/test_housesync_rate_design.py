"""Explicit design overrides and captured rate-library revision provenance."""

from copy import deepcopy
from dataclasses import replace

import pytest
from pydantic import ValidationError

from housesync.engine import _boq, _revision, revision_for
from housesync.rates import BOQ_CODES, RateLibraryError, load_rate_library
from housesync.schema import HouseSettings

QUANTITIES = {"floor_area_m2": 80, "floor_finish_area_m2": 80, "ceiling_area_m2": 80,
              "wall_volume_m3": 20.94, "roof_area_m2": 99, "paint_area_m2": 209.4,
              "skirting_length_m": 35, "internal_plaster_area_m2": 102.3, "external_render_area_m2": 107.1,
              "door_count": 1, "window_count": 2}


def test_new_settings_roundtrip_remains_library_driven():
    settings = HouseSettings()
    saved = settings.model_dump(mode="json")
    assert saved["rate_overrides"] == {}
    assert "rates" not in saved
    assert HouseSettings.model_validate(saved).rate_overrides == {}


def test_partial_legacy_rates_pin_only_supplied_prices_and_expand_opening_allowance():
    settings = HouseSettings.model_validate({"currency": "SGD", "rates": {"floor_m2": 80, "opening_each": 500}})
    assert settings.rate_overrides == {"FLOOR": 80, "DOOR": 500, "WINDOW": 500}
    assert settings.currency == "SGD"
    assert "rates" not in settings.model_dump(mode="json")
    assert HouseSettings.model_validate(settings.model_dump(mode="json")) == settings


@pytest.mark.parametrize("overrides", [{}, {"PAINT": 0}])
def test_explicit_override_map_ignores_stale_legacy_prices(overrides):
    settings = HouseSettings.model_validate({"rate_overrides": overrides, "rates": {"paint_m2": -123, "obsolete": True}})
    assert settings.rate_overrides == overrides


@pytest.mark.parametrize("overrides", [None, [], {"UNKNOWN": 1}, {"PAINT": -1}, {"PAINT": True},
                                      {"PAINT": float("nan")}, {"PAINT": float("inf")}, {"PAINT": 1_000_001}])
def test_invalid_override_values_fail_validation(overrides):
    with pytest.raises(ValidationError):
        HouseSettings(rate_overrides=overrides)


@pytest.mark.parametrize("legacy", [None, [], {"obsolete": 1}, {"paint_m2": True}, {"paint_m2": -1}])
def test_invalid_legacy_prices_fail_when_no_authoritative_override_map(legacy):
    with pytest.raises(ValidationError):
        HouseSettings.model_validate({"rates": legacy})


def test_library_lookup_and_zero_override_have_independent_provenance():
    library = load_rate_library()
    settings = HouseSettings(rate_overrides={"PAINT": 0, "DOOR": 700},
                             rate_source="Design-specific allowance; not a supplier quote", rate_date="2026-11-16")
    rows = {row["code"]: row for row in _boq(settings, QUANTITIES, library)}
    assert rows["PAINT"]["rate"] == 0
    assert rows["PAINT"]["amount"] == 0
    assert rows["PAINT"]["rate_origin"] == "override"
    assert rows["PAINT"]["rate_source"] == settings.rate_source
    assert rows["PAINT"]["rate_effective_date"] == "2026-11-16"
    assert rows["PAINT"]["library_rate"] == library.rows["PAINT"]["rate"]
    assert rows["DOOR"]["rate"] == 700
    assert rows["WINDOW"]["rate"] == library.rows["WINDOW"]["rate"]
    for code, row in rows.items():
        assert row["library_currency"] == "AUD"
        assert row["unit"] == BOQ_CODES[code][0]
        if code not in settings.rate_overrides:
            assert row["rate_origin"] == "library"
            assert row["rate_source"] == library.rows[code]["source"]
            assert row["rate_effective_date"] == library.rows[code]["effective_date"]


def test_library_edit_changes_revision_and_unpinned_rates_only():
    library = load_rate_library()
    edited_rows = deepcopy(library.rows)
    edited_rows["FLOOR"]["rate"] += 10
    edited_rows["PAINT"]["rate"] += 2
    edited = replace(library, rows=edited_rows, sha256="1" * 64)
    settings = HouseSettings(rate_overrides={"PAINT": 3})
    before = {row["code"]: row for row in _boq(settings, QUANTITIES, library)}
    after = {row["code"]: row for row in _boq(settings, QUANTITIES, edited)}
    assert after["FLOOR"]["amount"] - before["FLOOR"]["amount"] == 800
    assert after["PAINT"]["rate"] == before["PAINT"]["rate"] == 3
    assert after["PAINT"]["library_rate"] != before["PAINT"]["library_rate"]
    assert _revision(settings, library) != _revision(settings, edited)
    assert revision_for(settings, library) == _revision(settings, library)


def test_foreign_currency_requires_full_explicit_prices_without_relabelling_library():
    library = load_rate_library()
    with pytest.raises(RateLibraryError, match="No currency conversion"):
        _boq(HouseSettings(currency="SGD", rates={"floor_m2": 80}), QUANTITIES, library)
    settings = HouseSettings(currency="SGD", rate_overrides={code: 1 for code in BOQ_CODES})
    rows = _boq(settings, QUANTITIES, library)
    assert all(row["rate"] == 1 and row["rate_origin"] == "override" for row in rows)
    assert all(row["library_currency"] == "AUD" for row in rows)
    assert settings.currency == "SGD"


def test_captured_library_avoids_reload_for_boq_and_revision(monkeypatch):
    library = load_rate_library()

    def unexpected_load():
        raise AssertionError("captured library must not be read from disk again")

    monkeypatch.setattr("housesync.engine.load_rate_library", unexpected_load)
    assert len(_boq(HouseSettings(), QUANTITIES, library)) == 11
    assert len(_revision(HouseSettings(), library)) == 12
