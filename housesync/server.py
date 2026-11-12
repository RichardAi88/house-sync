"""Loopback-only development application. Run with ``python -m housesync.server``."""
from __future__ import annotations

import argparse
import json
import mimetypes
import os
import re
import shutil
import threading
import traceback
import uuid
import webbrowser
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit

from pydantic import ValidationError

from .documents import export_documents
from .engine import _revision as revision_for
from .engine import build_house
from .schema import HouseSettings

ROOT = Path(__file__).resolve().parents[1]
STATIC = Path(__file__).parent / "static"
MAX_BODY = 32768
REVISION = re.compile(r"^[0-9a-f]{12}$")


def atomic_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(data, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
    os.replace(temporary, path)


class Application:
    def __init__(self, output_root: Path):
        self.output_root = output_root.resolve()
        self.output_root.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()
        self.latest: dict | None = None

    def build(self, settings: HouseSettings) -> dict:
        with self.lock:
            revision = revision_for(settings)
            destination = self.output_root / revision
            if (destination / "result.json").is_file():
                self.latest = json.loads((destination / "result.json").read_text(encoding="utf-8"))
                atomic_json(self.output_root / "latest.json", {"revision": revision})
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
                    previous = json.loads((destination / "result.json").read_text(encoding="utf-8"))
                    if temporary.resolve().parent != self.output_root:
                        raise ValueError("Temporary build is outside the output folder")
                    shutil.rmtree(temporary)
                    result = previous
                else:
                    temporary.rename(destination)
                atomic_json(self.output_root / "latest.json", {"revision": revision})
                self.latest = result
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
                records.append({"revision": result["revision"], "created_at": result.get("created_at"), "settings": result["settings"]})
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
                    self._json({"status": "ok", "application": "House Sync"})
                elif path == "/api/state":
                    self._json(application.state())
                elif path == "/api/revisions":
                    self._json(application.revisions())
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
            if urlsplit(self.path).path != "/api/build":
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
                settings = HouseSettings.model_validate_json(self.rfile.read(length))
                self._json(application.build(settings))
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
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--open", action="store_true", help="Open the editor in the default browser")
    parser.add_argument("--output", type=Path, default=ROOT / "out" / "house-sync")
    args = parser.parse_args()
    if not (ROOT / "node_modules" / "three" / "build" / "three.module.js").exists():
        parser.error("Browser dependencies are missing. Run npm ci in the project folder first.")
    application = Application(args.output)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), handler_for(application))
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
