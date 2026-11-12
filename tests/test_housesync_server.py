"""Publication and HTTP boundary tests without repeating native CAD builds."""

from __future__ import annotations

import hashlib
import json
import threading
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer

import pytest

from housesync import server
from housesync.schema import HouseSettings


@pytest.fixture
def fake_exports(monkeypatch):
    state = {"source": "version-one", "builds": [], "exports": [], "fail_export": False}

    def revision(settings):
        encoded = settings.model_dump_json() + state["source"]
        return hashlib.sha256(encoded.encode()).hexdigest()[:12]

    def build(settings, output):
        state["builds"].append(settings.model_dump(mode="json"))
        (output / "house.ifc").write_text("IFC example: " + settings.paint_colour, encoding="utf-8")
        return {
            "settings": settings.model_dump(mode="json"), "revision": revision(settings),
            "files": {"ifc": "house.ifc"}, "mesh": {"objects": []},
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
