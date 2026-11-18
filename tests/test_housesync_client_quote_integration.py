"""Independent compiled-house checks of saved, client-facing variation quotes."""

from __future__ import annotations

import csv
import json
import re
from copy import deepcopy
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from housesync import server
from housesync.schema import HouseSettings


def _pdf_text(data):
    """Read ReportLab's uncompressed text streams without a test-only dependency."""
    def unescape(match):
        token = match.group(1)
        if re.fullmatch(rb"[0-7]{1,3}", token):
            return bytes([int(token, 8)])
        return {b"n": b"\n", b"r": b"\r", b"t": b"\t", b"b": b"\b", b"f": b"\f"}.get(token, token)

    strings = re.findall(rb"\(((?:\\.|[^\\()])*)\)\s*Tj", data)
    return " ".join(re.sub(rb"\\([0-7]{1,3}|.)", unescape, value, flags=re.DOTALL).decode("latin-1") for value in strings)


def _pdfs(application, comparison):
    directory = application.changes_root / comparison["comparison_id"]
    files = {}
    for key in ("client_quote_pdf", "internal_breakdown_pdf"):
        assert comparison["files"][key].startswith(f"/changes/{comparison['comparison_id']}/")
        path = directory / Path(comparison["files"][key]).name
        files[key] = path.read_bytes()
        assert files[key].startswith(b"%PDF-")
    return files


def _totals(comparison, **expected):
    actual = comparison["quote"]["public"]["totals"]
    for key, value in expected.items():
        assert Decimal(str(actual[key])) == Decimal(str(value)), (key, actual[key], value)
    return actual


@pytest.fixture(scope="module")
def compiled_quote_pair(tmp_path_factory):
    root = tmp_path_factory.mktemp("compiled-client-quotes")
    library = root / "rates.csv"
    library.write_bytes((Path(__file__).resolve().parents[1] / "rates" / "rates_aud.csv").read_bytes())
    application = server.Application(root / "issued", rate_library_path=library)
    before = application.build(HouseSettings())
    after = application.build(HouseSettings(length_mm=12000))
    return application, before, after, library


def test_real_ten_to_twelve_metre_house_quotes_reconcile_cost_markup_and_gst(compiled_quote_pair):
    application, before, after, _ = compiled_quote_pair
    assert before["quantities"]["floor_area_m2"] == 80
    assert after["quantities"]["floor_area_m2"] == 96
    assert before["totals"] == {"currency": "AUD", "subtotal": 53330.22}
    assert after["totals"] == {"currency": "AUD", "subtotal": 61640.22}
    comparison = application.compare(before["revision"], after["revision"])
    assert comparison["totals"]["net_change"] == 8310
    _totals(comparison, additions_at_cost=8310, credits_at_cost=0, at_cost_delta=8310,
            additions_markup=1662, credits_markup=0, markup_amount=1662, admin_fee=0,
            subtotal_ex_gst=9972, gst_rate=10, gst_amount=997.20, total_incl_gst=10969.20)
    public = comparison["quote"]["public"]
    assert public["before_revision"] == before["revision"]
    assert public["after_revision"] == after["revision"]
    assert date.fromisoformat(public["valid_until"]) - date.fromisoformat(public["issued_on"]) == timedelta(days=14)
    for pdf in _pdfs(application, comparison).values():
        text = _pdf_text(pdf)
        assert public["quote_reference"] in text
        assert before["revision"] in text and after["revision"] in text
        for amount in ("8,310.00", "1,662.00", "9,972.00", "997.20", "10,969.20"):
            assert amount in text


def test_saved_admin_fee_is_added_once_without_markup_and_before_gst(compiled_quote_pair):
    application, before, after, _ = compiled_quote_pair
    inputs = deepcopy(after["settings"])
    inputs["builder_pricing"]["admin_fee_aud"] = 150
    revised = application.build(HouseSettings.model_validate(inputs))
    assert revised["quantities"] == after["quantities"]
    assert revised["totals"] == after["totals"]  # Builder pricing is outside the measured BOQ.
    comparison = application.compare(before["revision"], revised["revision"])
    _totals(comparison, at_cost_delta=8310, markup_amount=1662, admin_fee=150,
            subtotal_ex_gst=10122, gst_amount=1012.20, total_incl_gst=11134.20)
    client = _pdf_text(_pdfs(application, comparison)["client_quote_pdf"])
    assert all(amount in client for amount in ("150.00", "10,122.00", "1,012.20", "11,134.20"))


def test_client_pdf_omits_private_rate_provenance_and_override_justification(compiled_quote_pair, tmp_path):
    _, _, after, original_library = compiled_quote_pair
    secret = "PRIVATE_OVERRIDE_JUSTIFICATION_7D3C"
    private_library_note = "PRIVATE_SUPPLIER_NOTE_712E"
    with original_library.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        headers, rows = reader.fieldnames, list(reader)
    next(row for row in rows if row["code"] == "WALL")["notes"] = private_library_note
    library = tmp_path / "private-rates.csv"
    with library.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=headers)
        writer.writeheader()
        writer.writerows(rows)
    application = server.Application(tmp_path / "private-issued", rate_library_path=library)
    before = application.build(HouseSettings())
    inputs = deepcopy(after["settings"])
    inputs["rate_overrides"] = {"FLOOR": 95}  # Same default rate; only its provenance differs.
    inputs["rate_source"] = secret
    inputs["builder_pricing"].update(builder_name="Demonstration Builder", client_description="Extend the house from 10 m to 12 m.")
    revised = application.build(HouseSettings.model_validate(inputs))
    comparison = application.compare(before["revision"], revised["revision"])
    _totals(comparison, at_cost_delta=8310, total_incl_gst=10969.20)
    assert any(secret in str(row) for row in comparison["rate_provenance"])
    public_json = json.dumps(comparison["quote"]["public"], sort_keys=True)
    assert secret not in public_json and private_library_note not in public_json
    documents = _pdfs(application, comparison)
    client, internal = (_pdf_text(documents[key]) for key in ("client_quote_pdf", "internal_breakdown_pdf"))
    assert "Demonstration Builder" in client
    assert "Extend the house from 10 m to 12 m." in client
    assert secret in internal
    for private_value in (secret, private_library_note, revised["rate_library"]["sha256"]):
        assert private_value not in client and private_value.encode() not in documents["client_quote_pdf"]
    assert "FLOOR" not in client


def test_prime_cost_and_provisional_sum_changes_add_to_quote_without_changing_boq(compiled_quote_pair):
    application, before, after, _ = compiled_quote_pair
    inputs = deepcopy(after["settings"])
    inputs["allowances"] = [
        {"id": "AL1", "kind": "prime_cost", "description": "Client-selected fixtures", "amount": 500,
         "note": "Final selection remains to be confirmed."},
        {"id": "AL2", "kind": "provisional_sum", "description": "Unmeasured site work", "amount": 1000,
         "note": "Scope and actual cost remain to be confirmed."},
    ]
    revised = application.build(HouseSettings.model_validate(inputs))
    assert revised["boq"] == after["boq"]
    assert revised["quantities"] == after["quantities"]
    comparison = application.compare(before["revision"], revised["revision"])
    assert comparison["totals"]["net_change"] == 8310
    _totals(comparison, at_cost_delta=9810, markup_amount=1962, admin_fee=0,
            subtotal_ex_gst=11772, gst_amount=1177.20, total_incl_gst=12949.20)
    public = comparison["quote"]["public"]
    assert [(row["id"], row["before"], row["after"], row["movement"]) for row in public["pc_allowances"]] == [("AL1", 0, 500, 500)]
    assert [(row["id"], row["before"], row["after"], row["movement"]) for row in public["ps_allowances"]] == [("AL2", 0, 1000, 1000)]
    client = _pdf_text(_pdfs(application, comparison)["client_quote_pdf"])
    assert "Client-selected fixtures" in client and "Unmeasured site work" in client
    assert "12,949.20" in client


def test_credit_quotes_apply_the_saved_credit_policy_to_real_measured_omissions(compiled_quote_pair):
    application, before, after, _ = compiled_quote_pair
    at_cost = application.compare(after["revision"], before["revision"])
    _totals(at_cost, additions_at_cost=0, credits_at_cost=8310, at_cost_delta=-8310,
            markup_amount=0, admin_fee=0, subtotal_ex_gst=-8310, gst_amount=-831, total_incl_gst=-9141)
    inputs = deepcopy(before["settings"])
    inputs["builder_pricing"]["credit_treatment"] = "including_margin"
    credited = application.build(HouseSettings.model_validate(inputs))
    including_margin = application.compare(after["revision"], credited["revision"])
    _totals(including_margin, at_cost_delta=-8310, additions_markup=0, credits_markup=1662,
            markup_amount=-1662, subtotal_ex_gst=-9972, gst_amount=-997.20, total_incl_gst=-10969.20)
    assert "10,969.20" in _pdf_text(_pdfs(application, including_margin)["client_quote_pdf"])


def test_old_quote_dates_and_pdf_bytes_survive_unrelated_model_library_and_clock_changes(compiled_quote_pair, monkeypatch):
    application, before, after, library = compiled_quote_pair
    comparison = application.compare(before["revision"], after["revision"])
    original_quote = deepcopy(comparison["quote"])
    original_pdfs = _pdfs(application, comparison)
    original_files = {identity: (application.output_root / identity / "result.json").read_bytes()
                      for identity in (before["revision"], after["revision"])}
    future = date.fromisoformat(original_quote["public"]["issued_on"]) + timedelta(days=45)
    monkeypatch.setattr(server, "_quote_issue_date", lambda: future)
    with library.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        headers, rows = reader.fieldnames, list(reader)
    rows[0]["notes"] = "PRIVATE_LATER_LIBRARY_EDIT_8821"
    with library.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=headers)
        writer.writeheader()
        writer.writerows(rows)
    latest = application.build(HouseSettings(length_mm=11000, paint_colour="Warm white", paint_hex="#FAF9F6",
                                            builder_pricing={"builder_name": "Later Builder", "overhead_margin_percent": 91,
                                                             "admin_fee_aud": 999, "client_description": "Unrelated later scope"}))
    assert latest["revision"] not in original_files
    fresh = application.compare(before["revision"], latest["revision"])
    assert fresh["quote"]["public"]["issued_on"] == future.isoformat()
    reopened = server.Application(application.output_root, rate_library_path=library)
    restored = reopened.compare(before["revision"], after["revision"])
    assert restored["comparison_id"] == comparison["comparison_id"]
    assert restored["quote"] == original_quote
    assert _pdfs(reopened, restored) == original_pdfs
    for identity, content in original_files.items():
        assert (application.output_root / identity / "result.json").read_bytes() == content


def test_same_foreign_currency_legacy_pair_keeps_estimate_comparison_without_an_aud_quote(compiled_quote_pair, tmp_path):
    _, before, after, _ = compiled_quote_pair
    application = server.Application(tmp_path / "legacy-issued")
    historical = []
    for saved, identity in ((before, "a" * 12), (after, "b" * 12)):
        snapshot = deepcopy(saved)
        snapshot["revision"] = identity
        snapshot["settings"]["currency"] = snapshot["totals"]["currency"] = "SGD"
        for field in ("builder_pricing", "allowances"):
            snapshot["settings"].pop(field, None)
        # Historical snapshots, rather than the current library, are authoritative.
        snapshot.pop("rate_library", None)
        for row in snapshot["boq"]:
            for field in ("library_currency", "library_rate", "rate_origin", "rate_source", "rate_effective_date"):
                row.pop(field, None)
        server.atomic_json(application.output_root / identity / "result.json", snapshot)
        historical.append(snapshot)
    original = [application.read_revision(snapshot["revision"]) for snapshot in historical]
    comparison = application.compare(historical[0]["revision"], historical[1]["revision"])
    assert comparison["currency"] == "SGD" and comparison["totals"]["net_change"] == 8310
    assert comparison.get("quote") is None
    assert not {"client_quote_pdf", "internal_breakdown_pdf"} & comparison["files"].keys()
    assert [application.read_revision(snapshot["revision"]) for snapshot in historical] == original
