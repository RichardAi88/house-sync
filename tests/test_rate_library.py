"""Rate files must be complete and auditable before they can price geometry."""

from __future__ import annotations

import csv
import hashlib
import json
from copy import deepcopy

import pytest

from housesync import rates
from housesync.rates import BOQ_CODES, CSV_COLUMNS, DEFAULT_LIBRARY_PATH, RateLibraryError, load_rate_library


@pytest.fixture
def library_rows():
    with DEFAULT_LIBRARY_PATH.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def write_library(path, rows, columns=CSV_COLUMNS):
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)
    return path


def test_default_library_retains_rates_units_and_raw_byte_identity():
    library = load_rate_library()
    assert library.currency == "AUD"
    assert set(library.rows) == set(BOQ_CODES)
    assert library.sha256 == hashlib.sha256(DEFAULT_LIBRARY_PATH.read_bytes()).hexdigest()
    expected = {"FLOOR": 95, "WALL": 380, "ROOF": 145, "PAINT": 9, "DOOR": 650, "WINDOW": 650,
                "FLOOR_FINISH": 65, "CEILING": 45, "SKIRTING": 18, "INTERNAL_PLASTER": 28, "EXTERNAL_RENDER": 40}
    for code, row in library.rows.items():
        assert row["code"] == code
        assert row["rate"] == expected[code]
        assert row["unit"] == BOQ_CODES[code][0]
        assert row["currency"] == "AUD"
        assert row["source"] == "illustrative"
        assert "not a tender estimate" in row["notes"].lower()
        assert row["effective_date"] == "2026-11-17"
        assert "no supplier" in row["notes"]


def test_default_library_is_independent_of_current_working_directory(monkeypatch, tmp_path):
    expected = load_rate_library().snapshot()
    monkeypatch.chdir(tmp_path)
    assert load_rate_library().snapshot() == expected


def test_installed_default_uses_packaged_csv(monkeypatch, tmp_path):
    package = tmp_path / "installed" / "housesync"
    data = package / "rate_data"
    data.mkdir(parents=True)
    packaged = data / "rates_aud.csv"
    packaged.write_bytes(DEFAULT_LIBRARY_PATH.read_bytes())
    monkeypatch.setattr(rates, "__file__", str(package / "rates.py"))
    assert rates._default_path() == packaged
    monkeypatch.setattr(rates, "DEFAULT_LIBRARY_PATH", rates._default_path())
    monkeypatch.chdir(tmp_path)
    assert load_rate_library().sha256 == hashlib.sha256(packaged.read_bytes()).hexdigest()


def test_snapshot_is_detached_and_json_safe():
    library = load_rate_library()
    snapshot = library.snapshot()
    assert json.loads(json.dumps(snapshot)) == snapshot
    snapshot["rows"]["FLOOR"]["rate"] = 999
    snapshot["rows"].pop("WALL")
    assert library.rows["FLOOR"]["rate"] == 95
    assert "WALL" in library.rows


def test_metadata_and_line_endings_are_part_of_raw_hash(tmp_path, library_rows):
    first = write_library(tmp_path / "first.csv", library_rows)
    changed = deepcopy(library_rows)
    changed[0]["notes"] += " Reviewed."
    second = write_library(tmp_path / "second.csv", changed)
    third = tmp_path / "third.csv"
    third.write_bytes(first.read_bytes().replace(b"\r\n", b"\n"))
    original = load_rate_library(first)
    assert original.sha256 != load_rate_library(second).sha256
    assert original.sha256 != load_rate_library(third).sha256
    assert original.rows == load_rate_library(third).rows


def test_bom_quoted_text_custom_currency_and_reordered_rows(tmp_path, library_rows):
    for row in library_rows:
        row["currency"] = "NZD"
    library_rows[0]["notes"] = 'Checked description, with "quoted" text'
    path = write_library(tmp_path / "custom.csv", list(reversed(library_rows)))
    path.write_bytes(b"\xef\xbb\xbf" + path.read_bytes())
    library = load_rate_library(path)
    assert library.currency == "NZD"
    assert library.rows["FLOOR"]["notes"] == library_rows[0]["notes"]
    assert library.sha256 == hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.mark.parametrize(("field", "value", "message"), [
    ("rate", "0", "positive"), ("rate", "-1", "positive"), ("rate", "NaN", "finite"),
    ("rate", "Infinity", "finite"), ("rate", "1000000.01", "1000000"),
    ("rate", "true", "number"), ("rate", "1e-10000", "positive"), ("rate", "", "empty"),
    ("code", "UNKNOWN", "unknown BOQ"), ("unit", "m", "unit must"),
    ("currency", "aud", "uppercase"), ("currency", "AU", "three-letter"),
    ("currency", "SGD", "mixed currencies"),
    ("source", " ", "empty"), ("source", "bad\nsource", "single-line"),
    ("description", "", "empty"), ("notes", "bad\ttext", "control"),
    ("effective_date", "2026-02-30", "valid calendar"),
    ("effective_date", "2026-1-2", "YYYY-MM-DD"),
    ("effective_date", "20261117", "YYYY-MM-DD"),
])
def test_invalid_row_reports_csv_line_and_reason(tmp_path, library_rows, field, value, message):
    # Mutate the second data row so mixed-currency validation has a prior value.
    library_rows[1][field] = value
    path = write_library(tmp_path / "invalid.csv", library_rows)
    with pytest.raises(RateLibraryError, match=message) as error:
        load_rate_library(path)
    assert "Rates CSV line " in str(error.value)
    if "\n" not in value:
        assert "line 3:" in str(error.value)


def test_missing_or_duplicate_codes_are_rejected(tmp_path, library_rows):
    with pytest.raises(RateLibraryError, match="missing BOQ codes: EXTERNAL_RENDER"):
        load_rate_library(write_library(tmp_path / "missing.csv", library_rows[:-1]))
    with pytest.raises(RateLibraryError, match="line 13: duplicate BOQ code FLOOR"):
        load_rate_library(write_library(tmp_path / "duplicate.csv", [*library_rows, library_rows[0]]))


@pytest.mark.parametrize("header", [
    "", "code,rate", ",".join([*CSV_COLUMNS, "unexpected"]), ",".join([*CSV_COLUMNS, "rate"]),
])
def test_missing_extra_or_duplicate_header_columns_are_rejected(tmp_path, header):
    path = tmp_path / "header.csv"
    path.write_text(header, encoding="utf-8")
    with pytest.raises(RateLibraryError, match="line 1:"):
        load_rate_library(path)


def test_extra_missing_cells_and_malformed_csv_are_rejected(tmp_path, library_rows):
    normal = write_library(tmp_path / "valid.csv", library_rows).read_text(encoding="utf-8")
    lines = normal.splitlines()
    for name, row, message in [
        ("extra", lines[1] + ",extra", "unexpected extra cells"),
        ("missing", "FLOOR,description,m2", "missing rate cell"),
        ("quoted", 'FLOOR,"unterminated', "invalid CSV"),
    ]:
        path = tmp_path / f"{name}.csv"
        path.write_text(lines[0] + "\n" + row, encoding="utf-8")
        with pytest.raises(RateLibraryError, match=message):
            load_rate_library(path)


def test_blank_notes_and_maximum_rate_are_valid(tmp_path, library_rows):
    library_rows[0].update(notes="", rate="1000000")
    library = load_rate_library(write_library(tmp_path / "limit.csv", library_rows))
    assert library.rows["FLOOR"]["rate"] == 1_000_000
    assert library.rows["FLOOR"]["notes"] == ""


def test_invalid_encoding_is_rejected(tmp_path):
    path = tmp_path / "encoding.csv"
    path.write_bytes(b"\xff\xfe")
    with pytest.raises(RateLibraryError, match="line 1: file must be UTF-8"):
        load_rate_library(path)


@pytest.mark.parametrize(("malformed", "line"), [('FLOOR,"unterminated', 2), ('FLOOR,"unterminated\ncontinued', 3)])
def test_malformed_csv_reports_actual_failing_physical_line(tmp_path, malformed, line):
    path = tmp_path / "malformed.csv"
    path.write_text(",".join(CSV_COLUMNS) + "\n" + malformed, encoding="utf-8")
    with pytest.raises(RateLibraryError, match=f"line {line}: invalid CSV"):
        load_rate_library(path)
