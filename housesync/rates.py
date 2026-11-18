"""Validated, versioned CSV unit-rate libraries, independent of house geometry.

The repository CSV is the editable source. Wheels carry the same bytes as
package data so an installed application does not depend on its working folder.
Loading never infers a supplier quotation or performs a currency conversion.
"""

from __future__ import annotations

import csv
import hashlib
import io
import math
import re
from copy import deepcopy
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

BOQ_CODES: dict[str, tuple[str, str]] = {
    "FLOOR": ("m2", "floor_m2"),
    "WALL": ("m3", "wall_m3"),
    "ROOF": ("m2", "roof_m2"),
    "PAINT": ("m2", "paint_m2"),
    "DOOR": ("each", "opening_each"),
    "WINDOW": ("each", "opening_each"),
    "FLOOR_FINISH": ("m2", "floor_finish_m2"),
    "CEILING": ("m2", "ceiling_m2"),
    "SKIRTING": ("m", "skirting_m"),
    "INTERNAL_PLASTER": ("m2", "internal_plaster_m2"),
    "EXTERNAL_RENDER": ("m2", "external_render_m2"),
}
CSV_COLUMNS = ("code", "description", "unit", "rate", "currency", "source", "effective_date", "notes")


def _default_path() -> Path:
    package = Path(__file__).resolve().parent
    packaged = package / "rate_data" / "rates_aud.csv"
    return packaged if packaged.is_file() else package.parent / "rates" / "rates_aud.csv"


DEFAULT_LIBRARY_PATH = _default_path()


class RateLibraryError(ValueError):
    """A CSV rate library cannot be used safely as a complete pricing basis."""


@dataclass(frozen=True)
class RateLibrary:
    currency: str
    sha256: str
    rows: dict[str, dict[str, Any]]

    def snapshot(self) -> dict[str, Any]:
        """Return a detached, JSON-safe record for an immutable house revision."""
        return {"sha256": self.sha256, "currency": self.currency, "rows": deepcopy(self.rows)}


def _failure(line: int, message: str) -> RateLibraryError:
    return RateLibraryError(f"Rates CSV line {line}: {message}")


def _text(value: str | None, field: str, line: int, *, required: bool = True) -> str:
    if value is None:
        raise _failure(line, f"missing {field} cell")
    cleaned = value.strip()
    if required and not cleaned:
        raise _failure(line, f"{field} must not be empty")
    if any(ord(character) < 32 for character in cleaned):
        raise _failure(line, f"{field} must be single-line text without control characters")
    return cleaned


def _rate(value: str, line: int) -> float:
    try:
        decimal = Decimal(value)
    except InvalidOperation as exc:
        raise _failure(line, "rate must be a finite positive number") from exc
    if not decimal.is_finite() or decimal <= 0 or decimal > 1_000_000:
        raise _failure(line, "rate must be finite, positive and no more than 1000000")
    number = float(decimal)
    if not math.isfinite(number) or number <= 0:
        raise _failure(line, "rate must be representable as a positive finite number")
    return number


def load_rate_library(path: str | Path | None = None) -> RateLibrary:
    """Load exactly one valid rate for every known BOQ code.

    Custom libraries may use any single, uppercase three-letter currency. The
    caller must check its design currency; this loader never converts prices.
    The SHA-256 identifies raw CSV bytes, including metadata and line endings.
    Invalid rows fail explicitly with their physical CSV line number.
    """
    raw = Path(path if path is not None else DEFAULT_LIBRARY_PATH).read_bytes()
    try:
        content = raw.decode("utf-8-sig")
    except UnicodeError as exc:
        raise _failure(1, "file must be UTF-8 text") from exc
    reader = csv.DictReader(io.StringIO(content, newline=""), strict=True)
    try:
        headers = reader.fieldnames
        if headers is None:
            raise _failure(1, "missing header")
        if len(headers) != len(set(headers)):
            raise _failure(1, "duplicate header columns")
        if set(headers) != set(CSV_COLUMNS):
            missing = sorted(set(CSV_COLUMNS) - set(headers))
            extra = sorted(set(headers) - set(CSV_COLUMNS))
            raise _failure(1, f"header columns must be {', '.join(CSV_COLUMNS)}; missing {missing}; unexpected {extra}")
        rows: dict[str, dict[str, Any]] = {}
        currency: str | None = None
        for source_row in reader:
            line = reader.line_num
            if None in source_row:
                raise _failure(line, "unexpected extra cells")
            row = {field: _text(source_row[field], field, line, required=field != "notes") for field in CSV_COLUMNS}
            code = row["code"]
            if code not in BOQ_CODES:
                raise _failure(line, f"unknown BOQ code {code!r}")
            if code in rows:
                raise _failure(line, f"duplicate BOQ code {code}")
            expected_unit = BOQ_CODES[code][0]
            if row["unit"] != expected_unit:
                raise _failure(line, f"{code} unit must be {expected_unit!r}, got {row['unit']!r}")
            if not re.fullmatch(r"[A-Z]{3}", row["currency"]):
                raise _failure(line, "currency must be an uppercase three-letter code")
            if currency is not None and currency != row["currency"]:
                raise _failure(line, f"mixed currencies: expected {currency}, got {row['currency']}")
            currency = row["currency"]
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", row["effective_date"]):
                raise _failure(line, "effective_date must use YYYY-MM-DD")
            try:
                date.fromisoformat(row["effective_date"])
            except ValueError as exc:
                raise _failure(line, "effective_date must be a valid calendar date") from exc
            rows[code] = {**row, "rate": _rate(row["rate"], line)}
        missing_codes = sorted(BOQ_CODES.keys() - rows.keys())
        if missing_codes:
            raise _failure(max(reader.line_num, 1), f"missing BOQ codes: {', '.join(missing_codes)}")
    except csv.Error as exc:
        # DictReader updates its own line_num only after a successful row. The
        # underlying reader still records the failing physical line on errors.
        line = max(reader.reader.line_num, reader.line_num, 1)
        raise _failure(line, f"invalid CSV: {exc}") from exc
    return RateLibrary(currency=currency or "", sha256=hashlib.sha256(raw).hexdigest(), rows=rows)
