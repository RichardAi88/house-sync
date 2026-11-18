"""Immutable quote publication, historical caches and the localhost boundary."""
from __future__ import annotations

import hashlib
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import date
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from housesync import server
from housesync.rates import load_rate_library
from housesync.schema import HouseSettings

QUOTE_FILES = {"client_quote_pdf", "internal_breakdown_pdf"}


def saved_pair(root, currency="AUD"):
    settings = HouseSettings(currency=currency).model_dump(mode="json")
    library = load_rate_library().snapshot()
    records = []
    for revision, quantity in (("a" * 12, 80), ("b" * 12, 96)):
        record = {
            "revision": revision, "settings": {**settings, "length_mm": 10000 if quantity == 80 else 12000},
            "rate_library": deepcopy(library), "created_at": "2026-11-17T12:00:00+00:00",
            "boq": [{"code": "FLOOR", "description": "Floor slab", "quantity": quantity, "unit": "m2", "rate": 95,
                     "amount": quantity * 95, "element_ids": ["S0"], "rate_origin": "library",
                     "rate_source": "illustrative", "rate_effective_date": "2026-11-17"}],
            "totals": {"currency": currency, "subtotal": quantity * 95}, "files": {},
        }
        server.atomic_json(root / revision / "result.json", record)
        records.append(record)
    return records


def published_bytes(application, comparison):
    directory = application.changes_root / comparison["comparison_id"]
    return {path.name: path.read_bytes() for path in directory.iterdir() if path.is_file()}


def test_first_quote_preparation_freezes_date_and_all_saved_artefacts_across_restart(tmp_path, monkeypatch):
    before, after = saved_pair(tmp_path)
    monkeypatch.setattr(server, "_quote_issue_date", lambda: date(2026, 11, 18))
    application = server.Application(tmp_path)
    before_bytes = (tmp_path / before["revision"] / "result.json").read_bytes()
    after_bytes = (tmp_path / after["revision"] / "result.json").read_bytes()
    comparison = application.compare(before["revision"], after["revision"])
    public = comparison["quote"]["public"]
    assert public["issued_on"] == "2026-11-18" and public["valid_until"] == "2026-12-02"
    assert comparison["quote"]["internal"]["issued_on"] == public["issued_on"]
    assert comparison["quote_date_basis"] == "UTC calendar date of first preparation"
    assert comparison["files"].keys() >= QUOTE_FILES
    assert comparison["report_contract"] == server.COMPARISON_CONTRACT
    assert len(comparison["source_fingerprint"]) == 64
    originals = published_bytes(application, comparison)
    assert originals["client-variation-quote.pdf"].startswith(b"%PDF-")
    assert originals["internal-variation-breakdown.pdf"].startswith(b"%PDF-")

    # Current design settings and today's CSV do not participate in a saved
    # A-to-B quote. The quote must be loaded, rather than rendered again.
    current = deepcopy(after)
    current["revision"] = "c" * 12
    current["settings"]["builder_pricing"]["overhead_margin_percent"] = 99
    server.atomic_json(tmp_path / current["revision"] / "result.json", current)
    server.atomic_json(tmp_path / "latest.json", {"revision": current["revision"]})
    monkeypatch.setattr(server, "_quote_issue_date", lambda: date(2030, 1, 1))

    def must_not_recalculate(*_args, **_kwargs):
        raise AssertionError("A published quote must not be reissued or repriced")

    monkeypatch.setattr(server, "build_variation_quote", must_not_recalculate)
    monkeypatch.setattr(server, "export_quote_documents", must_not_recalculate)
    monkeypatch.setattr(server, "load_rate_library", must_not_recalculate)
    restarted = server.Application(tmp_path, rate_library_path=tmp_path / "missing-current-rates.csv")
    assert restarted.compare(before["revision"], after["revision"]) == comparison
    assert published_bytes(restarted, comparison) == originals
    assert (tmp_path / before["revision"] / "result.json").read_bytes() == before_bytes
    assert (tmp_path / after["revision"] / "result.json").read_bytes() == after_bytes
    assert not list(application.changes_root.glob("building-*"))


def test_old_unquoted_cache_is_preserved_and_upgraded_into_another_publication(tmp_path, monkeypatch):
    before, after = saved_pair(tmp_path)
    application = server.Application(tmp_path)
    # Simulate both an actual pre-quotes namespace and an old-format report
    # occupying the new key: neither may be silently overwritten in place.
    old_source = b"\0".join(Path(server.__file__).with_name(name).read_bytes() for name in ("changes.py", "schema.py", "rates.py"))
    versions = [hashlib.sha256(old_source).hexdigest()[:12], server._comparison_source_fingerprint()[:12]]
    original_records = {}
    for version in versions:
        identity = f"{before['revision']}_{after['revision']}_{version}"
        directory = application.changes_root / identity
        legacy = server.compare_revisions(before, after)
        legacy.update(comparison_id=identity, files={"variation_pdf": f"/changes/{identity}/variation-order.pdf"})
        server.atomic_json(directory / "comparison.json", legacy)
        (directory / "variation-order.pdf").write_bytes(b"Original historical variation")
        original_records[identity] = {path.name: path.read_bytes() for path in directory.iterdir()}
    monkeypatch.setattr(server, "_quote_issue_date", lambda: date(2026, 11, 18))
    upgraded = application.compare(before["revision"], after["revision"])
    assert upgraded["comparison_id"] not in original_records
    assert upgraded["quote"]["public"]["issued_on"] == "2026-11-18"
    for identity, originals in original_records.items():
        assert {path.name: path.read_bytes() for path in (application.changes_root / identity).iterdir()} == originals
    assert server.Application(tmp_path).compare(before["revision"], after["revision"]) == upgraded


@pytest.mark.parametrize("source_name", ["quotes.py", "quote_documents.py", "schema.py"])
def test_report_source_changes_publish_a_new_version_without_editing_issued_quotes(tmp_path, monkeypatch, source_name):
    before, after = saved_pair(tmp_path)
    application = server.Application(tmp_path)
    old = application.compare(before["revision"], after["revision"])
    originals = published_bytes(application, old)
    read_bytes = Path.read_bytes
    source_path = Path(server.__file__).with_name(source_name)

    def altered_source(path):
        content = read_bytes(path)
        return content + b"\n# New source contract\n" if path == source_path else content

    monkeypatch.setattr(Path, "read_bytes", altered_source)
    revised = application.compare(before["revision"], after["revision"])
    assert revised["comparison_id"] != old["comparison_id"]
    assert revised["source_fingerprint"] != old["source_fingerprint"]
    assert revised["totals"] == old["totals"]
    assert published_bytes(application, old) == originals


def test_quote_export_failure_cleans_only_the_unpublished_temporary_and_can_retry(tmp_path, monkeypatch):
    before, after = saved_pair(tmp_path)
    application = server.Application(tmp_path)
    revisions = {record["revision"]: (tmp_path / record["revision"] / "result.json").read_bytes() for record in (before, after)}
    original_export = server.export_quote_documents

    def interrupted(_comparison, output):
        (output / "client-variation-quote.pdf").write_bytes(b"incomplete quote")
        raise RuntimeError("Quote rendering interrupted")

    monkeypatch.setattr(server, "export_quote_documents", interrupted)
    with pytest.raises(RuntimeError, match="Quote rendering interrupted"):
        application.compare(before["revision"], after["revision"])
    assert not list(application.changes_root.glob("building-*"))
    assert not list(application.changes_root.glob("*/comparison.json"))
    assert all((tmp_path / revision / "result.json").read_bytes() == content for revision, content in revisions.items())
    monkeypatch.setattr(server, "export_quote_documents", original_export)
    monkeypatch.setattr(server, "_quote_issue_date", lambda: date(2026, 11, 20))
    prepared = application.compare(before["revision"], after["revision"])
    assert prepared["quote"]["public"]["issued_on"] == "2026-11-20"


def test_concurrent_preparations_share_one_quote_issue_under_the_application_lock(tmp_path, monkeypatch):
    before, after = saved_pair(tmp_path)
    application = server.Application(tmp_path)
    issued = []
    original_builder = server.build_variation_quote

    def record_issue(*args):
        issued.append(args[-1])
        return original_builder(*args)

    monkeypatch.setattr(server, "build_variation_quote", record_issue)
    with ThreadPoolExecutor(max_workers=4) as executor:
        reports = list(executor.map(lambda _index: application.compare(before["revision"], after["revision"]), range(8)))
    assert len(issued) == 1
    assert all(report == reports[0] for report in reports)
    assert len(list(application.changes_root.glob("*/comparison.json"))) == 1
    assert not list(application.changes_root.glob("building-*"))


def test_same_currency_foreign_reports_work_without_an_aud_quote(tmp_path, monkeypatch):
    before, after = saved_pair(tmp_path, "SGD")

    def aud_only(*_args):
        raise AssertionError("The AUD quote path must not run for a foreign comparison")

    monkeypatch.setattr(server, "build_variation_quote", aud_only)
    monkeypatch.setattr(server, "export_quote_documents", aud_only)
    application = server.Application(tmp_path)
    comparison = application.compare(before["revision"], after["revision"])
    assert comparison["currency"] == "SGD" and comparison["totals"]["net_change"] == 1520
    assert comparison["quote"] is None
    assert not QUOTE_FILES.intersection(comparison["files"])
    assert "quote_date_basis" not in comparison
    assert server.Application(tmp_path).compare(before["revision"], after["revision"]) == comparison
    altered = deepcopy(after)
    altered["revision"] = "c" * 12
    altered["settings"]["currency"] = altered["totals"]["currency"] = "AUD"
    server.atomic_json(tmp_path / altered["revision"] / "result.json", altered)
    with pytest.raises(ValueError, match="same currency"):
        application.compare(before["revision"], altered["revision"])


@pytest.fixture
def quote_http(tmp_path):
    application = server.Application(tmp_path / "out")
    before, after = saved_pair(application.output_root)
    service = ThreadingHTTPServer(("127.0.0.1", 0), server.handler_for(application))
    worker = threading.Thread(target=service.serve_forever, daemon=True)
    worker.start()

    def request(method, path, payload=None, headers=None):
        body = None if payload is None else json.dumps(payload)
        connection = HTTPConnection("127.0.0.1", service.server_port, timeout=15)
        try:
            connection.request(method, path, body=body, headers={"Content-Type": "application/json", **(headers or {})})
            response = connection.getresponse()
            return response.status, response.read(), dict(response.getheaders())
        finally:
            connection.close()

    yield application, before, after, request
    service.shutdown()
    service.server_close()
    worker.join(timeout=5)


def test_builder_listing_and_separate_client_pdf_are_fixed_downloads_with_local_security(quote_http):
    application, before, after, request = quote_http
    target = f"/api/compare?before={before['revision']}&after={after['revision']}"
    status, body, _ = request("GET", target)
    assert status == 200
    comparison = json.loads(body)
    assert {"public", "internal"} <= comparison["quote"].keys()
    downloads = {}
    for key in QUOTE_FILES:
        status, content, headers = request("GET", comparison["files"][key])
        assert status == 200 and headers["Content-Type"] == "application/pdf"
        assert content.startswith(b"%PDF-")
        downloads[key] = content
        assert content == (application.changes_root / comparison["comparison_id"] / comparison["files"][key].rsplit("/", 1)[-1]).read_bytes()
    assert downloads["client_quote_pdf"] != downloads["internal_breakdown_pdf"]
    assert request("GET", comparison["files"]["client_quote_pdf"], headers={"Host": "untrusted.example"})[0] == 403
    assert request("GET", f"/changes/{comparison['comparison_id']}/%2e%2e/%2e%2e/latest.json")[0] == 404
    assert request("GET", target + "&before=" + before["revision"])[0] == 422


def test_maximum_legal_escaped_unicode_allowances_fit_the_bounded_http_body(quote_http, monkeypatch):
    application, _, _, request = quote_http
    payload = HouseSettings().model_dump(mode="json")
    payload["allowances"] = [{"id": f"AL{index}", "kind": "prime_cost", "description": "\U0001f3e0" * 240,
                              "amount": 1, "note": "\U0001f3e0" * 1000} for index in range(1, 101)]
    payload["builder_pricing"]["client_description"] = "\U0001f3e0" * 2000
    payload["builder_pricing"]["builder_name"] = "\U0001f3e0" * 120
    length = len(json.dumps(payload).encode("utf-8"))
    assert 1_048_576 < length < server.MAX_BODY <= 4_194_304
    seen = []

    def accept(settings):
        seen.append(settings)
        return {"accepted": True}

    monkeypatch.setattr(application, "build", accept)
    monkeypatch.setattr(application, "envelope", lambda value: value)
    status, body, _ = request("POST", "/api/build", payload)
    assert status == 200 and json.loads(body) == {"accepted": True}
    assert len(seen[0].allowances) == 100
    status, _, _ = request("POST", "/api/build", payload, {"Origin": "https://untrusted.example"})
    assert status == 403 and len(seen) == 1
