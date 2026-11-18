"""Publication and HTTP boundary tests without repeating native CAD builds."""

from __future__ import annotations

import hashlib
import json
import threading
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

import pytest

from housesync import server
from housesync.rates import BOQ_CODES, DEFAULT_LIBRARY_PATH
from housesync.schema import HouseSettings


@pytest.fixture
def fake_exports(monkeypatch):
    state = {"source": "version-one", "builds": [], "exports": [], "fail_export": False}

    def revision(settings, library):
        encoded = settings.model_dump_json() + state["source"] + library.sha256
        return hashlib.sha256(encoded.encode()).hexdigest()[:12]

    def build(settings, output, library):
        state["builds"].append(settings.model_dump(mode="json"))
        (output / "house.ifc").write_text("IFC example: " + settings.paint_colour, encoding="utf-8")
        area = settings.length_mm * settings.width_mm / 1e6
        rows = []
        for code, quantity, description, element in [
            ("FLOOR", area, "Floor slab", "S0"),
            ("PAINT", 200, f"Paint {settings.paint_colour} {settings.paint_hex}", "W1"),
        ]:
            rate = settings.rate_overrides.get(code, library.rows[code]["rate"])
            rows.append({"code": code, "description": description, "quantity": quantity, "unit": "m2",
                         "rate": rate, "amount": round(quantity * rate, 2), "element_ids": [element]})
        return {
            "settings": settings.model_dump(mode="json"), "revision": revision(settings, library), "rate_library": library.snapshot(),
            "files": {"ifc": "house.ifc"}, "mesh": {"objects": []},
            "boq": rows, "totals": {"currency": settings.currency, "subtotal": sum(row["amount"] for row in rows)},
        }

    def documents(result, output):
        state["exports"].append(result["revision"])
        (output / "specification.pdf").write_bytes(b"partial output")
        if state["fail_export"]:
            raise RuntimeError("Simulated export failure")
        (output / "specification.pdf").write_text("Specification " + result["revision"], encoding="utf-8")
        return {"specification_pdf": "specification.pdf"}

    monkeypatch.setattr(server, "revision_for", revision)
    monkeypatch.setattr(server, "build_house", build)
    monkeypatch.setattr(server, "export_documents", documents)
    return state


def test_publication_and_cache_keep_an_immutable_snapshot(tmp_path, fake_exports):
    application = server.Application(tmp_path)
    first = application.build(HouseSettings())
    published = tmp_path / first["revision"]
    original = {path.name: path.read_bytes() for path in published.iterdir()}
    repeated = application.build(HouseSettings())
    assert repeated == first
    assert len(fake_exports["builds"]) == len(fake_exports["exports"]) == 1
    assert {path.name: path.read_bytes() for path in published.iterdir()} == original
    assert json.loads((tmp_path / "latest.json").read_text()) == {"revision": first["revision"]}
    assert first["files"]["ifc"] == f"/files/{first['revision']}/house.ifc"
    assert not list(tmp_path.glob("building-*"))


def test_changed_design_preserves_previous_revision_and_updates_latest(tmp_path, fake_exports):
    application = server.Application(tmp_path)
    first = application.build(HouseSettings())
    old_directory = tmp_path / first["revision"]
    old_files = {path.name: path.read_bytes() for path in old_directory.iterdir()}
    second = application.build(HouseSettings(paint_colour="Warm white", paint_hex="#FAF9F6"))
    assert first["revision"] != second["revision"]
    assert {path.name: path.read_bytes() for path in old_directory.iterdir()} == old_files
    assert application.state() == second
    assert json.loads((tmp_path / "latest.json").read_text())["revision"] == second["revision"]
    assert {row["revision"] for row in application.revisions()} == {first["revision"], second["revision"]}


def test_restart_reuses_current_sources_and_rebuilds_changed_sources(tmp_path, fake_exports):
    settings = HouseSettings(length_mm=12000)
    original = server.Application(tmp_path).build(settings)
    assert server.Application(tmp_path).state() == original
    assert len(fake_exports["builds"]) == 1
    fake_exports["source"] = "version-two"
    refreshed = server.Application(tmp_path).state()
    assert refreshed["revision"] != original["revision"]
    assert refreshed["settings"] == settings.model_dump(mode="json")
    assert len(fake_exports["builds"]) == 2
    assert (tmp_path / original["revision"] / "result.json").is_file()


def test_schema_default_changes_invalidate_comparison_cache_without_rewriting_reports(tmp_path, fake_exports, monkeypatch):
    application = server.Application(tmp_path)
    first = application.build(HouseSettings())
    second = application.build(HouseSettings(length_mm=12000))
    report = application.compare(first["revision"], second["revision"])
    old_report = application.changes_root / report["comparison_id"] / "comparison.json"
    old_bytes = old_report.read_bytes()
    read_bytes = Path.read_bytes
    schema_path = Path(server.__file__).with_name("schema.py")

    def changed_defaults(path):
        original = read_bytes(path)
        return original + b"\n# Changed validated defaults\n" if path == schema_path else original

    monkeypatch.setattr(Path, "read_bytes", changed_defaults)
    refreshed = application.compare(first["revision"], second["revision"])
    assert refreshed["comparison_id"] != report["comparison_id"]
    assert refreshed["totals"] == report["totals"]
    assert old_report.read_bytes() == old_bytes
    assert len(list(application.changes_root.glob("*/comparison.json"))) == 2


def test_failed_export_preserves_last_successful_revision(tmp_path, fake_exports):
    application = server.Application(tmp_path)
    original = application.build(HouseSettings())
    latest_bytes = (tmp_path / "latest.json").read_bytes()
    result_bytes = (tmp_path / original["revision"] / "result.json").read_bytes()
    fake_exports["fail_export"] = True
    with pytest.raises(RuntimeError, match="Simulated export failure"):
        application.build(HouseSettings(length_mm=12000))
    assert (tmp_path / "latest.json").read_bytes() == latest_bytes
    assert (tmp_path / original["revision"] / "result.json").read_bytes() == result_bytes
    assert application.state() == original
    assert not list(tmp_path.glob("building-*"))
    assert len(application.revisions()) == 1


@pytest.fixture
def http_application(tmp_path, monkeypatch, fake_exports):
    static = tmp_path / "static"
    static.mkdir()
    (static / "index.html").write_text("<!doctype html><title>House Sync test</title>", encoding="utf-8")
    (tmp_path / "outside.txt").write_text("outside the served roots", encoding="utf-8")
    monkeypatch.setattr(server, "STATIC", static)
    application = server.Application(tmp_path / "out")
    service = ThreadingHTTPServer(("127.0.0.1", 0), server.handler_for(application))
    worker = threading.Thread(target=service.serve_forever, daemon=True)
    worker.start()

    def request(method, path, payload=None, headers=None):
        connection = HTTPConnection("127.0.0.1", service.server_port, timeout=5)
        body = None if payload is None else json.dumps(payload)
        request_headers = {"Content-Type": "application/json", **(headers or {})}
        try:
            connection.request(method, path, body=body, headers=request_headers)
            response = connection.getresponse()
            return response.status, response.read(), dict(response.getheaders())
        finally:
            connection.close()

    yield application, request, service.server_port
    service.shutdown()
    service.server_close()
    worker.join(timeout=5)


def test_http_build_and_download_share_published_revision(http_application):
    application, request, _ = http_application
    status, body, _ = request("POST", "/api/build", HouseSettings().model_dump(mode="json"))
    assert status == 200
    result = json.loads(body)
    status, downloaded, headers = request("GET", result["files"]["ifc"])
    assert status == 200
    assert downloaded == (application.output_root / result["revision"] / "house.ifc").read_bytes()
    assert "attachment" in headers["Content-Disposition"]
    status, body, _ = request("GET", "/api/state")
    assert status == 200
    assert json.loads(body)["revision"] == result["revision"]


def test_maximum_room_metadata_and_allowances_fit_the_combined_import_boundary(http_application):
    _, request, _ = http_application
    emoji = "\U0001f3e0"
    settings = HouseSettings().model_dump(mode="json")
    # Unmatched room metadata is deliberately retained when topology changes.
    # All 200 records and all 100 quote allowances may coexist in a valid file.
    settings["rooms"] = [{"id": f"ROOM{index}", "name": emoji * 120,
                          "finishes": dict.fromkeys(("floor", "ceiling", "skirting", "paint"), emoji * 120)}
                         for index in range(1, 201)]
    settings["allowances"] = [{"id": f"AL{index}", "kind": "prime_cost", "description": emoji * 240,
                               "amount": 1, "note": emoji * 1000} for index in range(1, 101)]
    settings["builder_pricing"]["builder_name"] = emoji * 120
    settings["builder_pricing"]["client_description"] = emoji * 2000
    encoded_size = len(json.dumps(settings).encode("utf-8"))
    assert 2_097_152 < encoded_size < server.MAX_BODY <= 4_194_304
    status, body, _ = request("POST", "/api/build", settings)
    assert status == 200
    saved = json.loads(body)
    assert len(saved["settings"]["rooms"]) == 200
    assert len(saved["settings"]["allowances"]) == 100


def test_http_purchase_orders_use_the_selected_saved_revision(http_application, tmp_path, fake_exports):
    application, request, _ = http_application
    _, body, _ = request("POST", "/api/build", {"rate_overrides": {"FLOOR": 120}})
    revision = json.loads(body)["revision"]
    request("POST", "/api/build", {"length_mm": 12000})
    original = application.read_revision(revision)
    application.rate_library_path = tmp_path / "unavailable.csv"
    prefix = f"/purchase-orders/{revision}"
    status, pdf, headers = request("GET", prefix + "/FLOOR.pdf")
    assert status == 200 and pdf.startswith(b"%PDF-")
    assert headers["Content-Type"] == "application/pdf"
    assert headers["Content-Disposition"] == f'attachment; filename="PO-{revision}-FLOOR.pdf"'
    status, bundle, headers = request("GET", prefix + "/all.zip")
    assert status == 200 and headers["Content-Type"] == "application/zip"
    with ZipFile(BytesIO(bundle)) as archive:
        assert set(archive.namelist()) == {f"PO-{revision}-{item['code']}.pdf" for item in original["boq"]}
    assert application.read_revision(revision) == original
    assert len(fake_exports["builds"]) == 2
    for path in (prefix + "/ABSENT.pdf", prefix + "/%2e%2e/latest.json", prefix + "/%2F.pdf",
                 "/purchase-orders/bad/FLOOR.pdf", "/purchase-orders/aaaaaaaaaaaa/all.zip"):
        status, _, _ = request("GET", path)
        assert status == 404


def test_http_rates_and_invalid_library_leave_issued_revision_available(http_application, tmp_path):
    application, request, _ = http_application
    status, body, _ = request("POST", "/api/build", {})
    assert status == 200
    issued = json.loads(body)
    status, body, headers = request("GET", "/api/rates")
    assert status == 200
    assert json.loads(body) == issued["rate_library"]
    assert headers["Cache-Control"] == "no-store"
    path = tmp_path / "rates.csv"
    path.write_bytes(DEFAULT_LIBRARY_PATH.read_bytes().replace(b"code,description,", b"code,missing,"))
    application.rate_library_path = path
    for method, route, payload in [("GET", "/api/rates", None), ("POST", "/api/build", {})]:
        status, body, _ = request(method, route, payload)
        assert status == 422
        assert "line 1" in json.loads(body)["error"].lower()
    restarted = server.Application(application.output_root, rate_library_path=path)
    assert restarted.state()["revision"] == issued["revision"]
    assert len(restarted.revisions()) == 1


def test_http_rejects_invalid_settings_without_creating_a_revision(http_application, fake_exports):
    application, request, _ = http_application
    status, body, _ = request("POST", "/api/build", {"length_mm": 1, "paint_hex": "invalid"})
    assert status == 422
    assert "length_mm" in json.loads(body)["error"]
    assert fake_exports["builds"] == []
    assert application.revisions() == []


@pytest.mark.parametrize("header", [{"Origin": "https://unrelated.example"}, {"Host": "unrelated.example"}])
def test_http_rejects_untrusted_origin_or_host(http_application, fake_exports, header):
    _, request, _ = http_application
    status, _, _ = request("POST", "/api/build", HouseSettings().model_dump(mode="json"), header)
    assert status == 403
    assert fake_exports["builds"] == []


def test_http_allows_loopback_origin(http_application):
    _, request, port = http_application
    status, _, _ = request("POST", "/api/build", {}, {"Origin": f"http://localhost:{port}"})
    assert status == 200


@pytest.mark.parametrize("path", ["/%2e%2e/outside.txt", "/..%5coutside.txt", "/vendor/%2e%2e/outside.txt"])
def test_http_static_traversal_cannot_read_outside_served_roots(http_application, path):
    _, request, _ = http_application
    status, body, _ = request("GET", path)
    assert status == 404
    assert b"outside the served roots" not in body


def test_http_revision_traversal_cannot_read_another_revision_or_parent(http_application):
    _, request, _ = http_application
    status, body, _ = request("POST", "/api/build", {})
    assert status == 200
    revision = json.loads(body)["revision"]
    status, body, _ = request("GET", f"/files/{revision}/%2e%2e/latest.json")
    assert status == 404
    assert json.loads(body)["error"] == "File not found"


def test_build_captures_a_to_b_cost_and_description_without_rewriting_snapshots(tmp_path, fake_exports):
    application = server.Application(tmp_path)
    before = application.build(HouseSettings())
    before_bytes = (tmp_path / before["revision"] / "result.json").read_bytes()
    after = application.build(HouseSettings(length_mm=12000, paint_colour="Warm white", paint_hex="#FAF9F6"))
    envelope = application.envelope(after)
    comparison = envelope["last_change"]
    assert envelope["previous_revision"] == before["revision"]
    assert comparison["before_revision"] == before["revision"]
    assert comparison["after_revision"] == after["revision"]
    assert comparison["totals"]["net_change"] == 1520
    assert any("Warm white" in item["description"] for item in comparison["setting_changes"])
    paint = next(item for item in comparison["items"] if item["code"] == "PAINT")
    assert paint["cost_delta"] == 0
    assert (tmp_path / before["revision"] / "result.json").read_bytes() == before_bytes
    assert "last_change" not in json.loads((tmp_path / after["revision"] / "result.json").read_text())
    restarted = server.Application(tmp_path)
    assert restarted.envelope(restarted.state())["last_change"] == comparison


def test_switching_to_a_saved_revision_records_a_credit(tmp_path, fake_exports):
    application = server.Application(tmp_path)
    application.build(HouseSettings())
    application.build(HouseSettings(length_mm=12000))
    restored = application.build(HouseSettings())
    report = application.envelope(restored)["last_change"]
    assert len(fake_exports["builds"]) == 2
    assert report["totals"]["net_change"] == -1520
    assert report["totals"]["omissions"] == 1520
    assert report["totals"]["additions"] == 0


def test_comparison_export_failure_preserves_house_and_reports_the_problem(tmp_path, fake_exports, monkeypatch):
    application = server.Application(tmp_path)
    before = application.build(HouseSettings())

    def fail(comparison, destination):
        (destination / "partial.pdf").write_bytes(b"incomplete")
        raise RuntimeError("Report export interrupted")

    monkeypatch.setattr(server, "export_change_report", fail)
    after = application.build(HouseSettings(length_mm=12000))
    state = application.envelope(after)
    assert state["revision"] == after["revision"]
    assert state["previous_revision"] == before["revision"]
    assert state["last_change"] is None
    assert "Report export interrupted" in state["change_error"]
    assert not list(application.changes_root.glob("building-*"))


def test_http_compare_and_report_downloads_reconcile(http_application):
    application, request, _ = http_application
    _, body, _ = request("POST", "/api/build", {})
    before = json.loads(body)
    _, body, _ = request("POST", "/api/build", {"length_mm": 12000})
    after = json.loads(body)
    status, body, _ = request("GET", f"/api/compare?before={before['revision']}&after={after['revision']}")
    assert status == 200
    report = json.loads(body)
    assert report["totals"]["after"] - report["totals"]["before"] == report["totals"]["net_change"] == 1520
    assert {"changes_xlsx", "changes_csv", "changes_pdf", "changes_json", "variation_pdf",
            "client_quote_pdf", "internal_breakdown_pdf"} == set(report["files"])
    for url in report["files"].values():
        status, content, _ = request("GET", url)
        assert status == 200
        assert len(content) > 100
    status, content, headers = request("GET", report["files"]["variation_pdf"])
    assert status == 200
    assert headers["Content-Type"] == "application/pdf"
    assert content.startswith(b"%PDF-")
    assert report["files"]["variation_pdf"].endswith("/variation-order.pdf")
    status, body, _ = request("GET", "/api/revisions")
    assert status == 200
    revisions = json.loads(body)
    assert all(item["currency"] == "AUD" and item["subtotal"] > 0 for item in revisions)
    assert len(list(application.changes_root.glob("*/comparison.json"))) == 1


@pytest.mark.parametrize("query,expected", [
    ("", 422), ("?before=../../latest&after=aaaaaaaaaaaa", 422),
    ("?before=aaaaaaaaaaaa&after=bbbbbbbbbbbb", 404),
    ("?before=aaaaaaaaaaaa&before=cccccccccccc&after=bbbbbbbbbbbb", 422),
])
def test_http_compare_rejects_invalid_or_unknown_revision_selection(http_application, query, expected):
    _, request, _ = http_application
    status, _, _ = request("GET", "/api/compare" + query)
    assert status == expected


def test_currency_change_keeps_new_design_but_does_not_invent_an_exchange_rate(http_application):
    _, request, _ = http_application
    _, body, _ = request("POST", "/api/build", {})
    before = json.loads(body)
    status, body, _ = request("POST", "/api/build", {"currency": "MYR", "rate_overrides": dict.fromkeys(BOQ_CODES, 100)})
    after = json.loads(body)
    assert status == 200
    assert after["last_change"] is None
    assert "currenc" in after["change_error"].lower()
    status, body, _ = request("GET", f"/api/compare?before={before['revision']}&after={after['revision']}")
    assert status == 422
    assert "currenc" in json.loads(body)["error"].lower()


def test_comparison_file_cannot_escape_its_report_directory(http_application):
    _, request, _ = http_application
    request("POST", "/api/build", {})
    _, body, _ = request("POST", "/api/build", {"length_mm": 12000})
    comparison = json.loads(body)["last_change"]
    status, _, _ = request("GET", f"/changes/{comparison['comparison_id']}/%2e%2e/%2e%2e/latest.json")
    assert status == 404


def test_local_server_cannot_share_a_live_port():
    service = server.LocalHTTPServer(("127.0.0.1", 0), server.handler_for(None))
    try:
        with pytest.raises(OSError):
            server.LocalHTTPServer(service.server_address, server.handler_for(None))
    finally:
        service.server_close()


def test_shutdown_requires_an_explicit_trusted_request(http_application):
    _, request, _ = http_application
    status, _, _ = request("POST", "/api/shutdown", {}, {"Origin": "https://unrelated.example"})
    assert status == 403
    status, _, _ = request("POST", "/api/shutdown", {})
    assert status == 422
    status, body, _ = request("POST", "/api/shutdown", {"application": "House Sync"})
    assert status == 200
    assert json.loads(body)["status"] == "stopping"


def test_partition_preview_returns_rooms_before_access_is_resolved_without_publication(http_application, fake_exports):
    application, request, _ = http_application
    draft = HouseSettings().model_dump(mode="json")
    draft["partitions"] = [{"id": "P1", "start_mm": [4000, 0], "end_mm": [4000, 8000],
                            "height_mm": 3000, "thickness_mm": 100, "wall_type": "partition"}]
    status, body, _ = request("POST", "/api/layout", draft)
    preview = json.loads(body)
    assert status == 200
    assert sorted(room["area_m2"] for room in preview["rooms"]) == [31.6, 47.6]
    assert any("unreachable" in error for error in preview["checks"]["errors"])
    assert not fake_exports["builds"] and application.revisions() == []
