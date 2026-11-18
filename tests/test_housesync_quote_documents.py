"""Client PDF projection isolation, pricing presentation and printable documents."""
from __future__ import annotations

import json
import re
from copy import deepcopy
from unittest.mock import patch

import pytest

from housesync.changes import compare_revisions
from housesync.quote_documents import client_variation_quote_pdf, export_quote_documents, internal_variation_breakdown_pdf
from housesync.quotes import build_variation_quote
from housesync.schema import HouseSettings


def _pdf_text(data: bytes) -> str:
    def unescape(value: bytes) -> str:
        def token(match):
            value = match.group(1)
            if re.fullmatch(rb"[0-7]{1,3}", value):
                return bytes([int(value, 8)])
            return {b"n": b"\n", b"r": b"\r", b"t": b"\t"}.get(value, value)
        return re.sub(rb"\\([0-7]{1,3}|.)", token, value, flags=re.S).decode("latin-1")

    blocks = re.findall(rb"BT(.*?)ET", data, flags=re.S)
    return " ".join("".join(unescape(value) for value in re.findall(rb"\(((?:\\.|[^\\()])*)\)\s*Tj", block)) for block in blocks)


@pytest.fixture
def quote_comparison():
    settings = HouseSettings().model_dump(mode="json")
    before = {"revision": "a"*12, "settings": settings,
              "boq": [{"code": "PRIVATE_TRADE", "description": "PRIVATE_SCOPE_SENTINEL", "quantity": 1, "unit": "m2",
                       "net_quantity": 1, "gross_quantity": 1, "wastage_percent": 0, "rate": 100, "amount": 100,
                       "rate_origin": "library", "rate_source": "illustrative", "rate_effective_date": "2026-11-17",
                       "library_rate": 100, "library_currency": "AUD"}],
              "totals": {"currency": "AUD", "subtotal": 100}}
    after = deepcopy(before)
    after["revision"] = "b"*12
    after["settings"].update(length_mm=12000, rate_source="PRIVATE_JUSTIFICATION_SENTINEL")
    after["settings"]["builder_pricing"].update(builder_name="Example Builder <A> & Sons", admin_fee_aud=150,
                                                client_description="Extend the living area.\nRetain existing finishes.")
    after["boq"][0].update(rate=8410, amount=8410, rate_origin="override", rate_source="PRIVATE_SOURCE_SENTINEL")
    after["totals"]["subtotal"] = 8410
    after["rate_library"] = {"sha256": "PRIVATE_LIBRARY_SENTINEL", "currency": "AUD", "rows": {
        "PRIVATE_TRADE": {"rate": 100, "unit": "m2", "currency": "AUD", "source": "PRIVATE_SUPPLIER_SENTINEL",
                          "effective_date": "2026-11-17", "notes": "PRIVATE_LIBRARY_NOTE_SENTINEL"}}}
    comparison = compare_revisions(before, after)
    comparison["quote"] = build_variation_quote(before, after, comparison, "2026-11-18")
    return before, after, comparison


def test_client_pdf_presents_aggregates_traceability_and_usable_acceptance(quote_comparison):
    comparison = quote_comparison[2]
    public = comparison["quote"]["public"]
    data = client_variation_quote_pdf(public)
    text = _pdf_text(data)
    for expected in ("Example Builder <A> & Sons", "LOGO", "VARIATION QUOTE", public["quote_reference"],
                     "Previous design: " + public["before_revision"], "Proposed design: " + public["after_revision"],
                     "2026-11-18", "2026-12-02", "AUD 8,310.00", "AUD 1,662.00", "AUD 150.00", "AUD 10,122.00",
                     "GST (10%)", "AUD 1,012.20", "AUD 11,134.20", "markup on cost", "(20%)", "Acceptance", "Signature"):
        assert expected in text
    assert text.count("Extend the living area.") == 1
    assert re.search(rb"/Count\s+1\b", data)
    assert b"/AcroForm" in data
    for field in ("accepted_by", "accepted_on", "acceptance_signature"):
        assert f"/T ({field})".encode() in data
    assert "illustrative rates" in text.lower()


def test_export_keeps_private_item_rates_notes_and_supplier_details_out_of_client(tmp_path, quote_comparison):
    comparison = quote_comparison[2]
    original = deepcopy(comparison)
    files = export_quote_documents(comparison, tmp_path)
    assert files == {"client_quote_pdf": "client-variation-quote.pdf", "internal_breakdown_pdf": "internal-variation-breakdown.pdf"}
    client = (tmp_path / files["client_quote_pdf"]).read_bytes()
    internal = (tmp_path / files["internal_breakdown_pdf"]).read_bytes()
    client_text, internal_text = _pdf_text(client), _pdf_text(internal)
    for marker in ("PRIVATE_SCOPE_SENTINEL", "PRIVATE_TRADE", "PRIVATE_JUSTIFICATION_SENTINEL", "PRIVATE_SOURCE_SENTINEL",
                   "PRIVATE_LIBRARY_SENTINEL", "PRIVATE_SUPPLIER_SENTINEL", "PRIVATE_LIBRARY_NOTE_SENTINEL"):
        assert marker not in client_text and marker.encode() not in client
        assert marker in internal_text
    assert "INTERNAL VARIATION BREAKDOWN" in internal_text
    assert "8,410" in internal_text and "8,410" not in client_text  # The private item's effective rate.
    assert "client-variation-quote.pdf" not in client_text and "internal-variation-breakdown.pdf" not in client_text
    assert comparison == original


def test_client_renderer_ignores_unknown_top_level_nested_and_allowance_private_keys(quote_comparison):
    public = deepcopy(quote_comparison[2]["quote"]["public"])
    sentinel = "UNKNOWN_PRIVATE_PAYLOAD_SENTINEL"
    public.update(internal={"item_costs": sentinel}, files={"internal_report": sentinel}, overrides=sentinel)
    public["totals"]["unit_rates"] = {"PRIVATE": sentinel}
    public["pc_allowances"] = [{"id": "AL1", "description": "Client tapware allowance", "before": 100, "after": 200,
                                "movement": 100, "notes": "Agreed selection allowance.", "supplier_source": sentinel, "internal_costs": sentinel}]
    data = client_variation_quote_pdf(public)
    assert sentinel.encode() not in data
    assert "Client tapware allowance" in _pdf_text(data)


def test_pc_ps_movements_notes_and_credit_treatment_are_separate(quote_comparison):
    before, after, _ = quote_comparison
    before["settings"]["allowances"] = [{"id": "AL1", "kind": "prime_cost", "description": "Tapware", "amount": 1000, "note": "Existing allowance"},
                                          {"id": "AL2", "kind": "provisional_sum", "description": "Groundworks", "amount": 5000, "note": "Existing allowance"}]
    after["settings"]["allowances"] = [{"id": "AL1", "kind": "prime_cost", "description": "Tapware selection", "amount": 1200, "note": "Chrome <standard> finish"},
                                         {"id": "AL2", "kind": "provisional_sum", "description": "Groundworks allowance", "amount": 4500, "note": "Revised work scope"}]
    comparison = compare_revisions(before, after)
    quote = build_variation_quote(before, after, comparison, "2026-11-18")
    data = client_variation_quote_pdf(quote["public"])
    text = _pdf_text(data)
    for value in ("Prime cost (PC) allowances", "Provisional sum (PS) allowances", "Tapware selection", "Groundworks allowance",
                  "Chrome <standard> finish", "Revised work scope", "AUD 200.00", "AUD -500.00", "AUD 8,010.00", "AUD 1,702.00", "AUD 10,848.20"):
        assert value in text
    assert "without the added overhead and margin markup" in text


def test_frozen_payload_renders_identical_bytes_at_different_clocks_and_after_json_roundtrip(quote_comparison):
    comparison = quote_comparison[2]
    public, internal = comparison["quote"]["public"], comparison["quote"]["internal"]
    with patch("reportlab.lib.utils.time.time", return_value=1_600_000_000):
        a = client_variation_quote_pdf(public)
        internal_a = internal_variation_breakdown_pdf(internal, comparison)
    frozen = json.loads(json.dumps(comparison, sort_keys=True))
    with patch("reportlab.lib.utils.time.time", return_value=2_000_000_000):
        b = client_variation_quote_pdf(frozen["quote"]["public"])
        internal_b = internal_variation_breakdown_pdf(frozen["quote"]["internal"], frozen)
    assert a == b
    assert internal_a == internal_b


def test_long_plain_text_and_allowance_notes_paginate_without_losing_final_totals(quote_comparison):
    public = deepcopy(quote_comparison[2]["quote"]["public"])
    public["builder_name"] = "W"*120
    public["client_description"] = ""
    public["scope"] = [("A long client instruction.\n"*80) + "END_SCOPE_SENTINEL"]
    public["pc_allowances"] = [{"id": "AL1", "description": "Product allowance", "before": 0, "after": 100, "movement": 100,
                                "notes": ("A detailed allowance note.\n"*40) + "END_ALLOWANCE_SENTINEL"}]
    data = client_variation_quote_pdf(public)
    text = _pdf_text(data)
    assert int(re.search(rb"/Count\s+(\d+)", data).group(1)) > 1
    assert "END_SCOPE_SENTINEL" in text and "END_ALLOWANCE_SENTINEL" in text
    assert "AUD 11,134.20" in text and "Acceptance" in text


def test_credit_only_pdf_labels_net_credit_and_signed_gst(quote_comparison):
    before, after, _ = quote_comparison
    comparison = compare_revisions(after, before)
    quote = build_variation_quote(after, before, comparison, "2026-11-18")
    text = _pdf_text(client_variation_quote_pdf(quote["public"]))
    assert "TOTAL INCLUDING GST (CREDIT)" in text
    assert "AUD -8,310.00" in text and "AUD -831.00" in text and "AUD -9,141.00" in text


@pytest.mark.parametrize("value", [{"secret": "PRIVATE"}, ["PRIVATE"], 123])
def test_client_renderer_rejects_structured_or_non_text_scope(value, quote_comparison):
    public = deepcopy(quote_comparison[2]["quote"]["public"])
    public["scope"] = [value]
    with pytest.raises(ValueError, match="plain strings"):
        client_variation_quote_pdf(public)
