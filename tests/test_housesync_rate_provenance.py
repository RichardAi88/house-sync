"""Saved pricing provenance, library changes and explicit override cost bridges."""
from __future__ import annotations

import csv
import re
from copy import deepcopy
from decimal import Decimal
from zipfile import ZipFile

import pytest
from openpyxl import load_workbook
from reportlab import rl_config

from housesync.changes import compare_revisions, export_change_report
from housesync.documents import export_documents
from housesync.engine import build_house
from housesync.rates import BOQ_CODES, CSV_COLUMNS, load_rate_library
from housesync.schema import HouseSettings


def edited_library(tmp_path, name="changed.csv", **edits):
    rows = deepcopy(load_rate_library().rows)
    for code, values in edits.items():
        rows[code].update(values)
    path = tmp_path / name
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        writer.writerows(rows.values())
    return load_rate_library(path)


@pytest.fixture(scope="module")
def pricing_revisions(tmp_path_factory):
    root = tmp_path_factory.mktemp("saved-rate-provenance")
    library_a = load_rate_library()
    library_b = edited_library(root, FLOOR_FINISH={"rate": 70, "source": "Fictional example B", "effective_date": "2026-11-18"})
    before = build_house(HouseSettings(), root / "a", library_a)
    library_only = build_house(HouseSettings(), root / "library-only", library_b)
    override_only = build_house(HouseSettings(rate_overrides={"FLOOR_FINISH": 75}, rate_source="Project assumption"), root / "override", library_a)
    mixed = build_house(HouseSettings(rate_overrides={"FLOOR_FINISH": 75}, rate_source="Project assumption"), root / "mixed", library_b)
    offset = build_house(HouseSettings(rate_overrides={"FLOOR_FINISH": 65}), root / "offset", library_b)
    return root, before, library_only, override_only, mixed, offset


def test_actual_library_only_edit_is_separate_from_design_and_override_changes(pricing_revisions):
    _, before, after, *_ = pricing_revisions
    saved = deepcopy(before)
    result = compare_revisions(before, after)
    assert result["setting_changes"] == result["design_changes"] == result["pricing_changes"] == []
    assert {change["field"] for change in result["library_changes"]} == {"rate_library.sha256", "rate_library.rows.FLOOR_FINISH"}
    assert result["has_changes"]
    assert result["totals"]["net_change"] == result["totals"]["rate_effect"] == result["totals"]["library_rate_effect"] == 440
    assert result["totals"]["override_rate_effect"] == result["totals"]["unattributed_rate_effect"] == 0
    row = result["items"][0]
    assert row["rate_bridge"] == 70
    assert row["rate_change_cause"] == "selected library update"
    assert row["after_rate_source"] == "Fictional example B"
    assert row["after_rate_effective_date"] == "2026-11-18"
    assert before == saved
    assert result["before_revision"] != result["after_revision"]


def test_actual_override_and_simultaneous_library_override_costs_reconcile(pricing_revisions):
    _, before, _, override, mixed, _ = pricing_revisions
    only = compare_revisions(before, override)
    assert only["library_changes"] == []
    assert {change["field"] for change in only["pricing_changes"]} == {"rate_overrides.FLOOR_FINISH", "rate_source"}
    assert only["totals"]["net_change"] == only["totals"]["override_rate_effect"] == 880
    assert only["totals"]["library_rate_effect"] == 0
    result = compare_revisions(before, mixed)
    row = result["items"][0]
    assert (row["rate_effect"], row["library_rate_effect"], row["override_rate_effect"], row["rate_bridge"]) == (880, 440, 440, 70)
    assert "selected library update" in row["rate_change_cause"] and "override" in row["rate_change_cause"]
    assert sum(Decimal(str(row[key])) for key in ("library_rate_effect", "override_rate_effect", "unattributed_rate_effect")) == Decimal(str(row["rate_effect"]))


def test_offsetting_library_and_new_override_remain_visible_at_zero_total_cost(pricing_revisions):
    _, before, _, _, _, offset = pricing_revisions
    result = compare_revisions(before, offset)
    assert result["has_changes"] and result["library_changes"] and result["pricing_changes"]
    assert result["totals"]["net_change"] == result["totals"]["rate_effect"] == 0
    assert result["totals"]["library_rate_effect"] == 440
    assert result["totals"]["override_rate_effect"] == -440
    assert result["items"][0]["rate_bridge"] == 70


def test_changed_library_under_existing_override_keeps_override_price(pricing_revisions, tmp_path):
    _, _, _, override, mixed, _ = pricing_revisions
    result = compare_revisions(override, mixed)
    assert result["library_changes"] and not result["design_changes"] and not result["pricing_changes"]
    assert result["totals"]["net_change"] == result["totals"]["rate_effect"] == 0
    assert result["items"][0]["rate_change_cause"] == "selected library updated; the explicit override kept the effective rate unchanged"
    files = export_change_report(result, tmp_path)
    pdf = (tmp_path / files["variation_pdf"]).read_bytes()
    assert b"RATE LIBRARY AND OVERRIDE AUDIT" in pdf
    assert override["rate_library"]["sha256"].encode() in pdf
    assert mixed["rate_library"]["sha256"].encode() in pdf
    assert b"Fictional example B" in pdf and b"2026-11-18" in pdf


def test_metadata_only_library_edit_is_a_zero_cost_audit_change(pricing_revisions, tmp_path):
    _, before, *_ = pricing_revisions
    library = edited_library(tmp_path, FLOOR_FINISH={"source": "Fictional metadata update", "effective_date": "2026-11-19"})
    after = build_house(HouseSettings(), tmp_path / "metadata-only", library)
    result = compare_revisions(before, after)
    assert result["has_changes"] and len(result["library_changes"]) == 2
    assert result["setting_changes"] == []
    assert result["totals"]["net_change"] == result["totals"]["rate_effect"] == 0
    assert result["items"][0]["code"] == "FLOOR_FINISH"
    assert result["items"][0]["after_rate_source"] == "Fictional metadata update"
    files = export_change_report(result, tmp_path / "report")
    pdf = (tmp_path / "report" / files["variation_pdf"]).read_bytes()
    assert b"Fictional metadata update" in pdf and b"2026-11-19" in pdf


def test_legacy_rates_migrate_without_false_price_input_or_provenance_changes(pricing_revisions, tmp_path):
    _, before, after, *_ = pricing_revisions
    legacy = deepcopy(before)
    legacy.pop("rate_library")
    legacy["settings"].pop("rate_overrides")
    prices = {row["code"]: row["rate"] for row in legacy["boq"]}
    legacy["settings"]["rates"] = {key: prices[code] for code, (_, key) in BOQ_CODES.items()}
    for row in legacy["boq"]:
        for field in ("rate_origin", "rate_source", "rate_effective_date", "library_rate", "library_currency"):
            row.pop(field)
    original = deepcopy(legacy)
    migrated = build_house(HouseSettings.model_validate(legacy["settings"]), tmp_path / "migrated",
                           edited_library(tmp_path, FLOOR_FINISH={"rate": after["boq"][6]["library_rate"]}))
    result = compare_revisions(legacy, migrated)
    assert result["setting_changes"] == result["library_changes"] == result["items"] == []
    assert not result["has_changes"]
    assert result["totals"]["net_change"] == 0
    assert result["before_rate_library"] == {}
    assert legacy == original


def test_foreign_reference_library_is_not_used_as_a_design_currency_bridge(pricing_revisions, tmp_path):
    _, before, *_ = pricing_revisions
    foreign = edited_library(tmp_path, **{code: {"currency": "GBP", "rate": 17} for code in BOQ_CODES})
    overrides = {row["code"]: row["rate"] for row in before["boq"]}
    overrides["FLOOR_FINISH"] = 75
    after = build_house(HouseSettings(rate_overrides=overrides), tmp_path / "foreign", foreign)
    result = compare_revisions(before, after)
    assert result["currency"] == "AUD"
    assert result["totals"]["net_change"] == result["totals"]["rate_effect"] == result["totals"]["unattributed_rate_effect"] == 880
    assert result["totals"]["library_rate_effect"] == result["totals"]["override_rate_effect"] == 0
    row = next(row for row in result["items"] if row["code"] == "FLOOR_FINISH")
    assert row["rate_bridge"] is None and "cross-currency" in row["rate_change_cause"]
    assert row["after_library_currency"] == "GBP"
    files = export_documents(after, tmp_path / "foreign")
    provenance = load_workbook(tmp_path / "foreign" / files["boq_xlsx"])["Rate provenance"]
    row = next(row for row in provenance.iter_rows(min_row=4, values_only=True) if row[0] == "FLOOR_FINISH")
    assert row[1] == 75 and row[2] == "AUD"
    assert row[4] == 17 and row[7] == "GBP"


def test_boq_and_spec_exports_save_selected_library_and_applied_override_basis(pricing_revisions, tmp_path, monkeypatch):
    _, _, _, _, mixed, _ = pricing_revisions
    monkeypatch.setattr(rl_config, "pageCompression", 0)
    files = export_documents(mixed, tmp_path)
    workbook = load_workbook(tmp_path / files["boq_xlsx"], data_only=False)
    provenance = workbook["Rate provenance"]
    row = next(row for row in provenance.iter_rows(min_row=4, values_only=True) if row[0] == "FLOOR_FINISH")
    assert row[1:10] == (75, "AUD", "override", 70, "Project assumption", "2026-11-17", "AUD", "Fictional example B", "2026-11-18")
    assert row[10] == mixed["rate_library"]["rows"]["FLOOR_FINISH"]["notes"]
    assert mixed["rate_library"]["sha256"] in provenance["B2"].value
    assert "Illustrative AUD rates, not a tender estimate" not in workbook.active["A3"].value
    with (tmp_path / files["boq_csv"]).open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.reader(stream))
    data = next(row for row in rows if row and row[0] == "FLOOR_FINISH")
    assert data[9:14] == ["override", "70.0", "Project assumption", "2026-11-17", mixed["rate_library"]["sha256"]]
    with ZipFile(tmp_path / files["specification_docx"]) as package:
        word = package.read("word/document.xml")
    pdf_text = b" ".join(re.findall(rb"\((.*?)\)\s*Tj", (tmp_path / files["specification_pdf"]).read_bytes(), flags=re.S))
    for expected in (mixed["rate_library"]["sha256"], "Project assumption", "Fictional example B", "2026-11-18", "origin override"):
        assert expected.encode() in word
        assert expected.encode() in pdf_text


def test_historical_exports_label_unknown_provenance_without_inventing_a_library(pricing_revisions, tmp_path):
    _, before, *_ = pricing_revisions
    historical = deepcopy(before)
    historical.pop("rate_library")
    historical["settings"].pop("rate_source")
    historical["settings"].pop("rate_date")
    for row in historical["boq"]:
        for key in ("rate_origin", "rate_source", "rate_effective_date", "library_rate", "library_currency"):
            row.pop(key)
    files = export_documents(historical, tmp_path)
    provenance = load_workbook(tmp_path / files["boq_xlsx"])["Rate provenance"]
    assert provenance["B2"].value == "Not recorded in this saved revision"
    assert provenance["D4"].value == provenance["F4"].value == provenance["G4"].value == "Not recorded"
    assert provenance["E4"].value is None
