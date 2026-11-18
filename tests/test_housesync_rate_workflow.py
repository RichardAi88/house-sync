"""Real compiler/export workflow checks for captured, user-maintained rates."""

from __future__ import annotations

import csv
import hashlib
import io
from copy import deepcopy
from pathlib import Path

from housesync import server
from housesync.changes import compare_revisions
from housesync.schema import HouseSettings


def _library_bytes() -> bytes:
    return (Path(__file__).resolve().parents[1] / "rates" / "rates_aud.csv").read_bytes()


def _with_rate(raw: bytes, code: str, rate: int) -> bytes:
    reader = csv.DictReader(io.StringIO(raw.decode("utf-8-sig"), newline=""))
    rows = list(reader)
    selected = next(row for row in rows if row["code"] == code)
    selected.update(rate=str(rate), source="User-maintained test allowance", effective_date="2026-11-18")
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=reader.fieldnames)
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue().encode("utf-8")


def _saved_files(directory: Path) -> dict[str, bytes]:
    return {path.relative_to(directory).as_posix(): path.read_bytes() for path in directory.rglob("*") if path.is_file()}


def _item(result: dict, code: str) -> dict:
    return next(row for row in result["boq"] if row["code"] == code)


def test_library_only_change_requires_explicit_rebuild_and_preserves_issued_revision_on_restart(tmp_path, monkeypatch):
    library_path = tmp_path / "rates.csv"
    original_library = _library_bytes()
    library_path.write_bytes(original_library)
    reads = []
    read_bytes = Path.read_bytes

    def counted_read(path):
        if path.resolve() == library_path.resolve():
            reads.append(path)
        return read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", counted_read)
    output = tmp_path / "issued"
    application = server.Application(output, rate_library_path=library_path)
    settings = HouseSettings()
    first = application.build(settings)
    assert len(reads) == 1  # Compiler, revision identity and exporters share one capture.
    assert first["rate_library"]["sha256"] == hashlib.sha256(original_library).hexdigest()
    assert _item(first, "DOOR")["rate"] == 650
    assert _item(first, "WINDOW")["rate"] == 650
    first_copy = deepcopy(first)
    first_files = _saved_files(output / first["revision"])
    assert {"boq.xlsx", "boq.csv", "specification.docx", "specification.pdf", "result.json", "settings.json"} <= first_files.keys()

    reads.clear()
    assert application.build(settings)["revision"] == first["revision"]
    assert len(reads) == 1  # A cache hit still captures the current library only once.
    assert _saved_files(output / first["revision"]) == first_files

    revised_library = _with_rate(original_library, "DOOR", 900)
    library_path.write_bytes(revised_library)
    assert application.state()["revision"] == first["revision"]
    assert _item(application.state(), "DOOR")["rate"] == 650
    restarted = server.Application(output, rate_library_path=library_path)
    reopened = restarted.state()
    assert reopened["revision"] == first["revision"]
    assert reopened["rate_library"] == first["rate_library"]
    assert _item(reopened, "DOOR")["rate"] == 650
    assert _saved_files(output / first["revision"]) == first_files

    reads.clear()
    second = restarted.build(HouseSettings.model_validate(reopened["settings"]))
    assert len(reads) == 1
    assert second["revision"] != first["revision"]
    assert second["rate_library"]["sha256"] == hashlib.sha256(revised_library).hexdigest()
    assert second["quantities"] == first["quantities"]
    assert _item(second, "DOOR")["rate"] == 900
    assert _item(second, "WINDOW")["rate"] == 650
    assert second["totals"]["subtotal"] == first["totals"]["subtotal"] + 250
    assert first == first_copy
    assert _saved_files(output / first["revision"]) == first_files
    comparison = compare_revisions(first, second)
    assert comparison["totals"]["net_change"] == comparison["totals"]["rate_effect"] == 250
    assert comparison["totals"]["quantity_effect"] == 0
    changed_costs = {row["code"]: row["cost_delta"] for row in comparison["items"] if row["cost_delta"]}
    assert changed_costs == {"DOOR": 250}


def test_edit_during_capture_cannot_mix_library_prices_and_revision_identity(tmp_path, monkeypatch):
    library_path = tmp_path / "rates.csv"
    original_library = _library_bytes()
    revised_library = _with_rate(original_library, "DOOR", 925)
    library_path.write_bytes(original_library)
    reads = []
    read_bytes = Path.read_bytes

    def change_after_capture(path):
        raw = read_bytes(path)
        if path.resolve() == library_path.resolve():
            reads.append(path)
            if len(reads) == 1:
                # The file changes after its bytes are captured but before the
                # loader, compiler, revision calculation and exports complete.
                library_path.write_bytes(revised_library)
        return raw

    monkeypatch.setattr(Path, "read_bytes", change_after_capture)
    application = server.Application(tmp_path / "issued", rate_library_path=library_path)
    first = application.build(HouseSettings())
    assert len(reads) == 1
    assert first["rate_library"]["sha256"] == hashlib.sha256(original_library).hexdigest()
    assert first["rate_library"]["rows"]["DOOR"]["rate"] == _item(first, "DOOR")["rate"] == 650
    reads.clear()
    second = application.build(HouseSettings())
    assert len(reads) == 1
    assert second["rate_library"]["sha256"] == hashlib.sha256(revised_library).hexdigest()
    assert second["rate_library"]["rows"]["DOOR"]["rate"] == _item(second, "DOOR")["rate"] == 925
    assert first["revision"] != second["revision"]
    assert first["quantities"] == second["quantities"]
