"""Loopback-only development application. Run with ``python -m housesync.server``."""
from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
import os
import re
import shutil
import socket
import threading
import traceback
import uuid
import webbrowser
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlsplit

from pydantic import ValidationError

from .changes import compare_revisions, export_change_report
from .documents import export_documents
from .engine import _revision as revision_for
from .engine import build_house
from .schema import HouseSettings

ROOT = Path(__file__).resolve().parents[1]
STATIC = Path(__file__).parent / "static"
MAX_BODY = 32768
REVISION = re.compile(r"^[0-9a-f]{12}$")
COMPARISON = re.compile(r"^[0-9a-f]{12}_[0-9a-f]{12}_[0-9a-f]{12}$")


class LocalHTTPServer(ThreadingHTTPServer):
    """Keep one application in exclusive control of its local port on Windows."""

    allow_reuse_address = False

    def server_bind(self) -> None:
        if os.name == "nt" and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


def atomic_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(data, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
    os.replace(temporary, path)


class Application:
    def __init__(self, output_root: Path):
        self.output_root = output_root.resolve()
        self.output_root.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.latest: dict | None = None
        self.changes_root = self.output_root / "changes"

    def read_revision(self, revision: str) -> dict:
        if not isinstance(revision, str) or not REVISION.fullmatch(revision):
            raise ValueError("Choose valid saved revisions A and B.")
        path = self.output_root / revision / "result.json"
        if not path.is_file():
            raise FileNotFoundError(f"Saved revision {revision} was not found.")
        result = json.loads(path.read_text(encoding="utf-8"))
        if result.get("revision") != revision:
            raise ValueError("The saved revision record does not match its folder.")
        return result

    def _previous(self) -> dict | None:
        if self.latest:
            return self.latest
        pointer = self.output_root / "latest.json"
        if pointer.is_file():
            return self.read_revision(json.loads(pointer.read_text(encoding="utf-8"))["revision"])
        return None

    def compare(self, before_revision: str, after_revision: str) -> dict:
        """Compare immutable saved inputs; persist a source-versioned report."""
        with self.lock:
            before = self.read_revision(before_revision)
            after = self.read_revision(after_revision)
            source = Path(__file__).with_name("changes.py").read_bytes()
            version = hashlib.sha256(source).hexdigest()[:12]
            comparison_id = f"{before_revision}_{after_revision}_{version}"
            destination = self.changes_root / comparison_id
            if (destination / "comparison.json").is_file():
                return json.loads((destination / "comparison.json").read_text(encoding="utf-8"))
            comparison = compare_revisions(before, after)
            comparison["comparison_id"] = comparison_id
            comparison["created_at"] = datetime.now(UTC).isoformat()
            self.changes_root.mkdir(parents=True, exist_ok=True)
            temporary = self.changes_root / ("building-" + uuid.uuid4().hex)
            temporary.mkdir()
            try:
                files = export_change_report(comparison, temporary)
                comparison["files"] = {
                    key: f"/changes/{comparison_id}/" + quote(name, safe="/") for key, name in files.items()
                }
                atomic_json(temporary / "comparison.json", comparison)
                temporary.rename(destination)
            except Exception:
                if temporary.exists() and temporary.resolve().parent == self.changes_root:
                    shutil.rmtree(temporary)
                raise
            return comparison

    def _record_transition(self, before: dict | None, after: dict) -> None:
        if not before or before["revision"] == after["revision"]:
            return
        transition = {"before_revision": before["revision"], "after_revision": after["revision"]}
        try:
            comparison = self.compare(before["revision"], after["revision"])
            transition["comparison_id"] = comparison["comparison_id"]
        except Exception as exc:
            # A comparison/report issue must not misrepresent a completed house build.
            transition["error"] = f"Change report unavailable: {exc}"
        atomic_json(self.output_root / "last_transition.json", transition)

    def envelope(self, result: dict) -> dict:
        """Attach navigation metadata without changing an issued revision file."""
        enriched = {**result, "previous_revision": None, "last_change": None}
        pointer = self.output_root / "last_transition.json"
        if pointer.is_file():
            transition = json.loads(pointer.read_text(encoding="utf-8"))
            if transition.get("after_revision") == result["revision"]:
                enriched["previous_revision"] = transition["before_revision"]
                if transition.get("error"):
                    enriched["change_error"] = transition["error"]
                else:
                    enriched["last_change"] = self.compare(transition["before_revision"], result["revision"])
        return enriched

    def build(self, settings: HouseSettings) -> dict:
        with self.lock:
            previous = self._previous()
            revision = revision_for(settings)
            destination = self.output_root / revision
            if (destination / "result.json").is_file():
                self.latest = json.loads((destination / "result.json").read_text(encoding="utf-8"))
                atomic_json(self.output_root / "latest.json", {"revision": revision})
                self._record_transition(previous, self.latest)
                return self.latest
            temporary = self.output_root / ("building-" + uuid.uuid4().hex)
            temporary.mkdir()
            try:
                result = build_house(settings, temporary)
                result["files"].update(export_documents(result, temporary))
                result["files"]["settings_json"] = "settings.json"
                atomic_json(temporary / "settings.json", settings.model_dump(mode="json"))
                result["created_at"] = datetime.now(UTC).isoformat()
                revision = result["revision"]
                if not REVISION.fullmatch(revision):
                    raise ValueError("Engine returned an invalid revision")
                result["files"] = {
                    key: "/files/" + revision + "/" + quote(value.replace("\\", "/"), safe="/")
                    for key, value in result["files"].items()
                    if isinstance(value, str)
                }
                atomic_json(temporary / "result.json", result)
                destination = self.output_root / revision
                if destination.exists():
                    # Identical input revisions keep their original published snapshot.
                    published = json.loads((destination / "result.json").read_text(encoding="utf-8"))
                    if temporary.resolve().parent != self.output_root:
                        raise ValueError("Temporary build is outside the output folder")
                    shutil.rmtree(temporary)
                    result = published
                else:
                    temporary.rename(destination)
                atomic_json(self.output_root / "latest.json", {"revision": revision})
                self.latest = result
                self._record_transition(previous, result)
                return result
            except Exception:
                if temporary.exists() and temporary.parent == self.output_root:
                    shutil.rmtree(temporary)
                raise

    def state(self) -> dict:
        if self.latest:
            return self.latest
        latest_path = self.output_root / "latest.json"
        if latest_path.exists():
            try:
                revision = json.loads(latest_path.read_text(encoding="utf-8"))["revision"]
                if REVISION.fullmatch(revision):
                    saved = json.loads((self.output_root / revision / "result.json").read_text(encoding="utf-8"))
                    settings = HouseSettings.model_validate(saved["settings"])
                    if revision_for(settings) == revision:
                        self.latest = saved
                        return self.latest
                    return self.build(settings)
            except (OSError, ValueError, KeyError, TypeError):
                pass
        return self.build(HouseSettings())

    def revisions(self) -> list[dict]:
        records = []
        for directory in self.output_root.iterdir():
            if REVISION.fullmatch(directory.name) and (directory / "result.json").is_file():
                result = json.loads((directory / "result.json").read_text(encoding="utf-8"))
                records.append({
                    "revision": result["revision"], "created_at": result.get("created_at"), "settings": result["settings"],
                    "project_name": result["settings"].get("project_name", "House"),
                    "currency": result.get("totals", {}).get("currency", result["settings"].get("currency")),
                    "subtotal": result.get("totals", {}).get("subtotal"),
                })
        return sorted(records, key=lambda item: item.get("created_at") or "", reverse=True)


def handler_for(application: Application) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def _json(self, data: dict | list, status: int = 200) -> None:
            body = json.dumps(data, ensure_ascii=False, allow_nan=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def _allowed_host(self) -> bool:
            expected = {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}
            return self.headers.get("Host") in expected

        def _file(self, path: Path, root: Path, *, attachment: bool = False) -> None:
            path = path.resolve()
            if not path.is_relative_to(root.resolve()) or not path.is_file():
                self._json({"error": "File not found"}, 404)
                return
            content = path.read_bytes()
            mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            if path.suffix == ".js":
                mime = "text/javascript"
            self.send_response(200)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(content)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Cache-Control", "no-cache")
            if attachment:
                self.send_header("Content-Disposition", f'attachment; filename="{path.name}"')
            self.end_headers()
            self.wfile.write(content)

        def do_GET(self) -> None:
            if not self._allowed_host():
                self._json({"error": "This application accepts localhost requests only."}, 403)
                return
            path = unquote(urlsplit(self.path).path)
            try:
                if path == "/api/health":
                    self._json({"status": "ok", "application": "House Sync", "features": ["revision_comparison"]})
                elif path == "/api/state":
                    self._json(application.envelope(application.state()))
                elif path == "/api/revisions":
                    self._json(application.revisions())
                elif path == "/api/compare":
                    query = parse_qs(urlsplit(self.path).query, keep_blank_values=True)
                    if len(query.get("before", [])) != 1 or len(query.get("after", [])) != 1:
                        self._json({"error": "Choose one saved revision A and one saved revision B."}, 422)
                        return
                    self._json(application.compare(query["before"][0], query["after"][0]))
                elif path.startswith("/changes/"):
                    parts = path.split("/", 3)
                    if len(parts) != 4 or not COMPARISON.fullmatch(parts[2]):
                        self._json({"error": "Change report not found"}, 404)
                        return
                    directory = application.changes_root / parts[2]
                    self._file(directory / parts[3], directory, attachment=not path.endswith(".pdf"))
                elif path.startswith("/files/"):
                    parts = path.split("/", 3)
                    if len(parts) != 4 or not REVISION.fullmatch(parts[2]):
                        self._json({"error": "File not found"}, 404)
                        return
                    self._file(application.output_root / parts[2] / parts[3], application.output_root / parts[2],
                               attachment=not path.endswith((".svg", ".pdf")))
                elif path.startswith("/vendor/"):
                    vendor = ROOT / "node_modules" / "three"
                    names = {"three.module.js": vendor / "build" / "three.module.js",
                             "three.core.js": vendor / "build" / "three.core.js",
                             "OrbitControls.js": vendor / "examples" / "jsm" / "controls" / "OrbitControls.js"}
                    target = names.get(path.removeprefix("/vendor/"))
                    if target:
                        self._file(target, vendor)
                    else:
                        self._json({"error": "File not found"}, 404)
                else:
                    self._file(STATIC / ("index.html" if path == "/" else path.lstrip("/")), STATIC)
            except FileNotFoundError as exc:
                self._json({"error": str(exc)}, 404)
            except ValueError as exc:
                self._json({"error": str(exc)}, 422)
            except Exception as exc:
                traceback.print_exc()
                self._json({"error": f"Unable to load project: {exc}"}, 500)

        def do_POST(self) -> None:
            if not self._allowed_host():
                self._json({"error": "This application accepts localhost requests only."}, 403)
                return
            origin = self.headers.get("Origin")
            if origin and origin not in {f"http://127.0.0.1:{self.server.server_port}", f"http://localhost:{self.server.server_port}"}:
                self._json({"error": "Origin is not allowed"}, 403)
                return
            route = urlsplit(self.path).path
            if route not in {"/api/build", "/api/shutdown"}:
                self._json({"error": "Route not found"}, 404)
                return
            if self.headers.get_content_type() != "application/json":
                self._json({"error": "Send application/json"}, 415)
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length <= 0 or length > MAX_BODY:
                    self._json({"error": "Invalid request size"}, 413)
                    return
                body = self.rfile.read(length)
                if route == "/api/shutdown":
                    if json.loads(body) != {"application": "House Sync"}:
                        self._json({"error": "Identify the application to stop."}, 422)
                        return
                    self._json({"status": "stopping", "application": "House Sync"})
                    threading.Thread(target=self.server.shutdown, daemon=True).start()
                    return
                settings = HouseSettings.model_validate_json(body)
                self._json(application.envelope(application.build(settings)))
            except ValidationError as exc:
                messages = [".".join(map(str, item["loc"])) + ": " + item["msg"] for item in exc.errors()]
                self._json({"error": "; ".join(messages)}, 422)
            except (ValueError, json.JSONDecodeError) as exc:
                self._json({"error": str(exc)}, 400)
            except Exception as exc:
                traceback.print_exc()
                self._json({"error": f"Build failed: {exc}. Your previous revision is still available."}, 500)

    return Handler


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the House Sync editor on this PC.")
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--open", action="store_true", help="Open the editor in the default browser")
    parser.add_argument("--output", type=Path, default=ROOT / "out" / "house-sync")
    args = parser.parse_args()
    if not (ROOT / "node_modules" / "three" / "build" / "three.module.js").exists():
        parser.error("Browser dependencies are missing. Run npm ci in the project folder first.")
    application = Application(args.output)
    server = LocalHTTPServer(("127.0.0.1", args.port), handler_for(application))
    url = f"http://127.0.0.1:{args.port}"
    print(f"House Sync is running at {url}\nRevision files: {args.output}\nPress Ctrl+C to stop.", flush=True)
    if args.open:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nHouse Sync stopped.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
