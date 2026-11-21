"""Compare immutable house revisions and export their estimated BOQ difference.

Saved snapshots remain the authority. Matching is by BOQ code, never row order,
and all monetary calculations use decimal half-up rounding to two places.
"""
from __future__ import annotations

import csv
import html
import json
import math
import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.workbook.properties import CalcProperties
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.platypus import KeepTogether, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from .quotes import diff_allowances
from .rates import BOQ_CODES
from .schema import ExteriorFinish, HouseSettings

CENT = Decimal("0.01")
ZERO = Decimal(0)
INK = "173042"
TEAL = "1B6D65"
PALE = "EAF3F7"

WALL_TREATMENTS = {"facebrick": "exposed facebrick", "rendered_paint": "render and paint",
                   "plaster_paint": "plaster and paint", "bare_brick": "bare brick"}

FIELD_LABELS = {
    "project_name": ("Project name", "metadata"),
    "length_mm": ("Internal length", "dimensions"),
    "width_mm": ("Internal width", "dimensions"),
    "height_mm": ("Wall height", "dimensions"),
    "wall_thickness_mm": ("Wall thickness", "dimensions"),
    "min_room_width_mm": ("Minimum room width", "geometry_rules"),
    "paint_colour": ("Interior paint finish", "finish"),
    "paint_hex": ("Interior paint colour reference", "finish"),
    "exterior_finish.kind": ("External brick leaf treatment", "finish"),
    "exterior_finish.name": ("External brick leaf finish", "finish"),
    "exterior_finish.hex": ("External brick leaf colour", "finish"),
    "inner_leaf_finish": ("Internal brick leaf treatment", "finish"),
    "inner_brick_hex": ("Bare internal brick colour", "finish"),
    "currency": ("Currency", "pricing"),
    "rates.floor_m2": ("Floor rate / m2", "rates"),
    "rates.wall_m3": ("Wall construction rate / m3", "rates"),
    "rates.roof_m2": ("Roof rate / m2", "rates"),
    "rates.paint_m2": ("Paint rate / m2", "rates"),
    "rates.opening_each": ("Door/window allowance / each", "rates"),
    "paint_coats": ("Paint coats", "finish"),
    "rate_source": ("Rate source", "pricing"),
    "rate_date": ("Rate date", "pricing"),
    "builder_pricing.overhead_margin_percent": ("Overhead and margin markup on cost (%)", "pricing"),
    "builder_pricing.admin_fee_aud": ("Variation administration fee (AUD)", "pricing"),
    "builder_pricing.credit_treatment": ("Margin treatment for credits", "pricing"),
    "builder_pricing.gst_percent": ("Quote GST (%)", "pricing"),
    "builder_pricing.validity_days": ("Quote validity (days)", "pricing"),
    "builder_pricing.builder_name": ("Builder name on the quote", "quote"),
    "builder_pricing.client_description": ("Client variation description", "quote"),
    "rates.floor_finish_m2": ("Floor finish rate / m2", "rates"),
    "rates.ceiling_m2": ("Ceiling rate / m2", "rates"),
    "rates.skirting_m": ("Skirting rate / m", "rates"),
    "rates.internal_plaster_m2": ("Internal plaster/render rate / m2", "rates"),
    "rates.external_render_m2": ("External render rate / m2", "rates"),
}

for _code, _label in (
    ("floor", "Floor slab"), ("wall", "Masonry"), ("roof", "Roof"), ("paint", "Paint"),
    ("door", "Door"), ("window", "Window"), ("floor_finish", "Floor finish"),
    ("ceiling", "Ceiling"), ("skirting", "Skirting"),
    ("internal_plaster", "Internal plaster/render"), ("external_render", "External render"),
):
    FIELD_LABELS[f"wastage.{_code}"] = (f"{_label} wastage %", "wastage")


def _decimal(value: Any, name: str, *, money: bool = False) -> Decimal:
    if isinstance(value, bool) or value is None:
        raise ValueError(f"{name} must be a finite, non-negative number")
    try:
        number = Decimal(str(value))
        if not number.is_finite() or number < 0:
            raise ValueError(f"{name} must be a finite, non-negative number")
        if money and number != number.quantize(CENT):
            raise ValueError(f"{name} must have no more than two decimal places")
        return number
    except (InvalidOperation, TypeError, OverflowError) as exc:
        raise ValueError(f"{name} must be a finite, non-negative number") from exc


def _rounded(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def _float(value: Decimal) -> float:
    return float(value) if value else 0.0


def _text(value: Any) -> str:
    if value is None:
        return "Not set"
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, (int, float, Decimal)):
        return f"{Decimal(str(value)):,.6f}".rstrip("0").rstrip(".")
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value)


def _flat(settings: dict, prefix: str = "") -> dict[str, Any]:
    flattened = {}
    for key, value in settings.items():
        field = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            flattened.update(_flat(value, field))
        else:
            flattened[field] = value
    return flattened


def _comparison_settings(settings: dict) -> dict:
    """Resolve newly optional inputs, without repricing historical BOQ snapshots.

    Missing fields and their explicit defaults have the same meaning. Currency,
    the original five rates and saved amounts remain exactly as recorded.
    """
    defaults = HouseSettings().model_dump(mode="json")
    normalised = {key: value for key, value in settings.items()
                  if key not in {"openings", "partitions", "rooms", "allowances", "rates", "rate_overrides"}}
    normalised["exterior_finish"] = ExteriorFinish.from_settings(settings).model_dump()
    for field in ("paint_coats", "rate_source", "rate_date", "inner_leaf_finish", "inner_brick_hex"):
        normalised.setdefault(field, defaults[field])
    normalised.setdefault("min_room_width_mm", defaults.get("min_room_width_mm", 900))
    normalised["wastage"] = {**defaults["wastage"], **settings.get("wastage", {})}
    normalised["builder_pricing"] = {**defaults["builder_pricing"], **settings.get("builder_pricing", {})}
    # Legacy rate inputs become explicit absolute overrides, not library rates.
    # Compare their canonical meaning rather than their historical JSON shape.
    overrides = (dict(settings["rate_overrides"]) if "rate_overrides" in settings else
                 {code: settings["rates"][key] for code, (_, key) in BOQ_CODES.items() if key in settings.get("rates", {})})
    normalised["rate_overrides"] = overrides
    return normalised


def _saved_library(result: dict) -> dict:
    library = result.get("rate_library")
    return library if isinstance(library, dict) else {}


def _library_changes(before: dict, after: dict) -> list[dict]:
    old, new = _saved_library(before), _saved_library(after)
    # Learning previously unrecorded provenance is not a changed rate library.
    if not old.get("sha256") or not new.get("sha256"):
        return []
    changes = []
    if old["sha256"] != new["sha256"]:
        changes.append({"field": "rate_library.sha256", "category": "rate_library", "before": old["sha256"], "after": new["sha256"],
                        "description": f"Selected rate library changed from SHA-256 {old['sha256']} to {new['sha256']}."})
    old_rows, new_rows = old.get("rows", {}), new.get("rows", {})
    for code in sorted(old_rows.keys() | new_rows.keys()):
        old_row, new_row = old_rows.get(code), new_rows.get(code)
        if old_row == new_row:
            continue
        parts = []
        for field in ("rate", "unit", "currency", "source", "effective_date", "description", "notes"):
            left, right = (row.get(field) if row else None for row in (old_row, new_row))
            if left != right:
                parts.append(f"{field.replace('_', ' ')} {_text(left)} to {_text(right)}")
        changes.append({"field": f"rate_library.rows.{code}", "code": code, "category": "rate_library", "before": old_row, "after": new_row,
                        "description": f"Rate library {code}: " + "; ".join(parts or ["entry changed"]) + "."})
    return changes


def _rate_facts(result: dict, row: dict | None) -> dict:
    if row is None:
        return {"origin": None, "source": None, "effective_date": None, "library_rate": None}
    rate_code = row.get("rate_code", row["code"])
    entry = _saved_library(result).get("rows", {}).get(rate_code, {})
    origin = row.get("rate_origin")
    if origin not in {"library", "override"}:
        overrides = _comparison_settings(result["settings"])["rate_overrides"]
        origin = "override" if row["code"] in overrides or rate_code in overrides else None
    return {"origin": origin, "source": row.get("rate_source", result["settings"].get("rate_source")),
            "effective_date": row.get("rate_effective_date", result["settings"].get("rate_date")),
            "library_rate": row.get("library_rate", entry.get("rate")),
            "library_currency": row.get("library_currency", entry.get("currency", _saved_library(result).get("currency"))),
            "library_source": entry.get("source"), "library_effective_date": entry.get("effective_date")}


def _rate_bridge(before: dict, after: dict, old: dict | None, new: dict | None,
                 quantity: Decimal, rate_effect: Decimal | None) -> dict:
    facts_a, facts_b = _rate_facts(before, old), _rate_facts(after, new)
    result = {"before_rate_origin": facts_a["origin"], "after_rate_origin": facts_b["origin"],
              "before_rate_source": facts_a["source"], "after_rate_source": facts_b["source"],
              "before_rate_effective_date": facts_a["effective_date"], "after_rate_effective_date": facts_b["effective_date"],
              "before_library_rate": facts_a["library_rate"], "after_library_rate": facts_b["library_rate"],
              "before_library_currency": facts_a.get("library_currency"), "after_library_currency": facts_b.get("library_currency"),
              "library_rate_effect": None, "override_rate_effect": None, "unattributed_rate_effect": None,
              "rate_bridge": None, "rate_change_cause": "No effective rate change"}
    if rate_effect is None:
        result["rate_change_cause"] = "Measurement unit changed; rate effects are not comparable"
        return result
    if old is None or new is None:
        result.update(library_rate_effect=0.0, override_rate_effect=0.0, unattributed_rate_effect=0.0)
        return result
    foreign_bridge = facts_a["origin"] == "library" and facts_b.get("library_currency") != after["totals"]["currency"]
    if facts_a["origin"] is None or facts_b["origin"] is None or facts_b["library_rate"] is None or foreign_bridge:
        result.update(unattributed_rate_effect=_float(rate_effect), library_rate_effect=0.0, override_rate_effect=0.0,
                      rate_change_cause=("Selected library currency differs from the design currency; no cross-currency rate bridge is inferred" if foreign_bridge else
                                         "Effective rate changed; historical library/override attribution was not recorded"))
        return result
    old_rate, new_rate = old["rate"], new["rate"]
    bridge = old_rate if facts_a["origin"] == "override" else _decimal(facts_b["library_rate"], "Saved library B rate")
    library_effect = _rounded(quantity * (bridge - old_rate))
    # Residual keeps the two-step bridge equal to the already rounded rate effect.
    override_effect = rate_effect - library_effect
    causes = []
    if bridge != old_rate:
        causes.append("selected library update")
    if new_rate != bridge:
        causes.append("explicit override edit or removal")
    if (not causes and facts_a["origin"] == "override" and facts_a.get("library_rate") is not None
            and facts_a.get("library_rate") != facts_b.get("library_rate")):
        causes.append("selected library updated; the explicit override kept the effective rate unchanged")
    elif not causes and facts_a["origin"] != facts_b["origin"]:
        causes.append("explicit override origin changed without changing the effective rate")
    result.update(library_rate_effect=_float(library_effect), override_rate_effect=_float(override_effect), unattributed_rate_effect=0.0,
                  rate_bridge=_float(bridge), rate_change_cause=" and ".join(causes) or "No effective rate change")
    return result


def _provenance_rows(before: dict, after: dict, old_rows: dict, new_rows: dict, items: list[dict]) -> list[dict]:
    affected = {item["code"]: item for item in items}
    result = []
    for code in dict.fromkeys([*old_rows, *new_rows]):
        item = new_rows.get(code) or old_rows[code]
        row = {"code": code, "rate_code": item.get("rate_code", code),
               "group_id": item.get("group_id"), "group_name": item.get("group_name")}
        for label, snapshot, source in (("before", before, old_rows), ("after", after, new_rows)):
            item = source.get(code)
            row[label] = ({**_rate_facts(snapshot, item), "rate": _float(item["rate"]), "unit": item["unit"],
                           "currency": snapshot["totals"]["currency"]} if item else None)
        row.update({key: affected.get(code, {}).get(key, 0.0 if key != "rate_change_cause" else "No effective rate change")
                    for key in ("library_rate_effect", "override_rate_effect", "unattributed_rate_effect", "rate_bridge", "rate_change_cause")})
        if code not in affected:
            row["rate_bridge"] = None
        result.append(row)
    return result


def _library_notes(comparison: dict) -> list[str]:
    notes = []
    for label, key in (("A", "before_rate_library"), ("B", "after_rate_library")):
        library = comparison.get(key) or {}
        if not library.get("sha256"):
            notes.append(f"Rate library {label}: not recorded in the historical snapshot; source and effective dates are unknown.")
            continue
        notes.append(f"Rate library {label} SHA-256: {library['sha256']} | Library currency: {library.get('currency', 'Not recorded')}.")
        groups: dict[tuple, list[str]] = {}
        for code, item in sorted(library.get("rows", {}).items()):
            groups.setdefault((item.get("source"), item.get("effective_date")), []).append(code)
        for (source, effective_date), codes in groups.items():
            notes.append(f"Library {label} source: {source or 'Not recorded'}; effective date: {effective_date or 'Not recorded'}; items: {', '.join(codes)}.")
    return notes


def _provenance_description(facts: dict | None) -> str:
    if not facts:
        return "No saved item."
    source, date = facts.get("source") or "Not recorded", facts.get("effective_date") or "Not recorded"
    library_rate = facts.get("library_rate")
    rate_text = _text(library_rate) if library_rate is not None else "Not recorded"
    if facts.get("origin") == "library":
        return f"Library rate {_text(facts['rate'])} {facts['currency']}/{facts['unit']}. Source: {source}; effective date: {date}."
    return (f"Applied {_text(facts['rate'])} {facts['currency']}/{facts['unit']}; origin {facts.get('origin') or 'Not recorded'}. "
            f"Applied source: {source}; effective date: {date}. Selected library rate: {rate_text} {facts.get('library_currency') or 'Not recorded'}. "
            f"Library source: {facts.get('library_source') or 'Not recorded'}; effective date: {facts.get('library_effective_date') or 'Not recorded'}.")


def _provenance_pdf(comparison: dict, paragraph: Any, width: float, *, title_style: str, body_style: str, header_style: str) -> list[Any]:
    if not comparison.get("rate_provenance"):
        return []
    content = [PageBreak(), paragraph("RATE LIBRARY AND OVERRIDE AUDIT", title_style)]
    content.extend(paragraph(note, body_style) for note in _library_notes(comparison))
    content.append(Spacer(1, 8))
    rows = [[paragraph(value, header_style) for value in ("Item", "Revision A pricing basis", "Revision B pricing basis and cause")]]
    for item in comparison["rate_provenance"]:
        after_text = _provenance_description(item["after"])
        if item["rate_change_cause"] != "No effective rate change":
            after_text += f" Cause: {item['rate_change_cause']}."
        if item.get("rate_bridge") is not None and any(item.get(key) for key in ("library_rate_effect", "override_rate_effect")):
            after_text += (f" Bridge rate {_text(item['rate_bridge'])} {comparison['currency']}; library effect {_signed(item['library_rate_effect'])}; "
                           f"override effect {_signed(item['override_rate_effect'])} {comparison['currency']}.")
        rows.append([paragraph(item["code"], body_style), paragraph(_provenance_description(item["before"]), body_style), paragraph(after_text, body_style)])
    table = Table(rows, colWidths=[90, (width - 90) / 2, (width - 90) / 2], repeatRows=1, hAlign="LEFT")
    table.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#" + INK)),
                              ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#" + PALE)]),
                              ("VALIGN", (0, 0), (-1, -1), "TOP"), ("TOPPADDING", (0, 0), (-1, -1), 5),
                              ("BOTTOMPADDING", (0, 0), (-1, -1), 5), ("LINEBELOW", (0, 0), (-1, -1), 0.35, colors.HexColor("#D5E0E4"))]))
    content.append(table)
    return content



LEGACY_OPENINGS = (
    {"id": "D1", "kind": "door", "host": "W1", "offset_mm": None, "width_mm": 1000, "height_mm": 2100, "sill_mm": 0},
    {"id": "N1", "kind": "window", "host": "W3", "offset_mm": None, "width_mm": 1500, "height_mm": 1200, "sill_mm": 900},
    {"id": "N2", "kind": "window", "host": "W2", "offset_mm": None, "width_mm": 1500, "height_mm": 1200, "sill_mm": 900},
)
WALL_LABELS = {"W1": "south, eastwards", "W2": "east, northwards", "W3": "north, westwards", "W4": "west, southwards"}


def _identity_order(identity: str) -> tuple[str, int, str]:
    match = re.fullmatch(r"([A-Z]+)([0-9]+)", identity)
    return (match[1], int(match[2]), "") if match else (identity, 0, identity)


def _point(value: Any, name: str) -> tuple[float, float]:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise ValueError(f"{name} must contain two coordinates")
    return tuple(float(_decimal(coordinate, name)) for coordinate in value)


def _point_text(value: Any) -> str:
    return f"({_text(value[0])}, {_text(value[1])}) mm"


ROOM_ID = re.compile(r"ROOM(?:[1-9][0-9]*|_[0-9A-F]{12})")


def _partitions(settings: dict) -> dict[str, dict]:
    source = settings.get("partitions", [])
    if not isinstance(source, (list, tuple)):
        raise ValueError("Saved partitions must be a list")
    result = {}
    for partition in source:
        if not isinstance(partition, dict) or not isinstance(partition.get("id"), str) or not re.fullmatch(r"P[1-9][0-9]*", partition["id"]):
            raise ValueError("Saved partitions require stable P element IDs")
        identity = partition["id"]
        if identity in result:
            raise ValueError(f"Saved partitions have duplicate element ID {identity}")
        start = _point(partition.get("start_mm"), f"Saved partition {identity} start")
        end = _point(partition.get("end_mm"), f"Saved partition {identity} end")
        if start == end:
            raise ValueError(f"Saved partition {identity} must have positive length")
        for field in ("thickness_mm", "height_mm"):
            if partition.get(field) is not None and _decimal(partition[field], f"Saved partition {identity} {field}") <= 0:
                raise ValueError(f"Saved partition {identity} must have positive {field}")
        result[identity] = {**partition, "start_mm": start, "end_mm": end}
    return result


def _partition_description(partition: dict) -> str:
    dimensions = ", ".join(f"{_text(partition[field])} mm {label}"
                           for field, label in (("thickness_mm", "thick"), ("height_mm", "high"))
                           if partition.get(field) is not None)
    kind = f"; wall type {partition['wall_type']}" if partition.get("wall_type") else ""
    return (f"Partition {partition['id']} from {_point_text(partition['start_mm'])} to {_point_text(partition['end_mm'])}"
            + (f"; {dimensions}" if dimensions else "") + kind)


def _partition_changes(before: dict, after: dict) -> list[dict]:
    old_rows, new_rows = _partitions(before), _partitions(after)
    changes = []
    for identity in sorted(old_rows.keys() | new_rows.keys(), key=_identity_order):
        old, new = old_rows.get(identity), new_rows.get(identity)
        common = {"category": "partitions", "element_id": identity}
        if old is None or new is None:
            added = old is None
            changes.append({**common, "field": f"partitions.{identity}", "label": f"Partition {identity}", "before": old, "after": new,
                            "description": f"{'Added' if added else 'Removed'} {_partition_description(new if added else old)}.",
                            "status": "added" if added else "removed"})
            continue
        for field, label in (("start_mm", "start"), ("end_mm", "end"), ("thickness_mm", "thickness"),
                             ("height_mm", "height"), ("wall_type", "wall type")):
            left, right = old.get(field), new.get(field)
            if left == right:
                continue
            old_text, new_text = (_point_text(value) for value in (left, right)) if field in {"start_mm", "end_mm"} else (
                _text(left) + (" mm" if field.endswith("_mm") else ""), _text(right) + (" mm" if field.endswith("_mm") else ""))
            changes.append({**common, "field": f"partitions.{identity}.{field}", "label": f"Partition {identity} {label}",
                            "before": left, "after": right, "description": f"Partition {identity} {label}: {old_text} to {new_text}.",
                            "status": "modified"})
    return changes


def _room_rows(result: dict) -> dict[str, dict]:
    """Read resolved saved rooms, retaining implicit ROOM1 for old snapshots."""
    settings = result["settings"]
    overrides = settings.get("rooms", [])
    if not isinstance(overrides, (list, tuple)):
        raise ValueError("Saved room overrides must be a list")
    explicit = {}
    for row in overrides:
        if not isinstance(row, dict) or not isinstance(row.get("id"), str) or not ROOM_ID.fullmatch(row["id"]):
            raise ValueError("Saved room overrides require stable ROOM element IDs")
        if row["id"] in explicit:
            raise ValueError(f"Saved room overrides have duplicate ID {row['id']}")
        explicit[row["id"]] = row
    source = result.get("rooms")
    if source is None:
        legacy_finishes = {"floor": "Floor finish allowance", "ceiling": "Ceiling allowance", "skirting": "Skirting allowance",
                           "paint": f"{settings.get('paint_colour', 'Light blue')} ({settings.get('paint_hex', '#ADD8E6')})"}
        source = [] if settings.get("partitions") else [{"id": "ROOM1", "name": "Room 1", "finishes": legacy_finishes}]
    if not isinstance(source, (list, tuple)):
        raise ValueError("Saved rooms must be a list")
    rows = {}
    for row in source:
        if not isinstance(row, dict) or not isinstance(row.get("id"), str) or not ROOM_ID.fullmatch(row["id"]):
            raise ValueError("Saved rooms require stable ROOM element IDs")
        identity = row["id"]
        if identity in rows:
            raise ValueError(f"Saved rooms have duplicate ID {identity}")
        override = explicit.get(identity, {})
        finishes = row.get("finishes") or {}
        if not isinstance(finishes, dict) or not isinstance(override.get("finishes", {}), dict):
            raise ValueError(f"Saved room {identity} finishes must be an object")
        rows[identity] = {**row, "name": override.get("name") or row.get("name") or f"Room {identity[4:]}",
                          "finishes": {**finishes, **{key: value for key, value in override.get("finishes", {}).items() if value is not None}},
                          "_explicit_finishes": override.get("finishes", {}), "_explicit_name": override.get("name"), "_exists": True}
    for identity, override in explicit.items():
        if identity not in rows:
            rows[identity] = {**override, "finishes": override.get("finishes", {}),
                              "_explicit_finishes": override.get("finishes", {}), "_explicit_name": override.get("name"), "_exists": False}
    return rows


def _room_changes(before: dict, after: dict) -> list[dict]:
    old_rows, new_rows = _room_rows(before), _room_rows(after)
    changes = []
    for identity in sorted(old_rows.keys() | new_rows.keys(), key=_identity_order):
        old, new = old_rows.get(identity), new_rows.get(identity)
        common = {"category": "rooms", "element_id": identity, "group_id": identity}
        if old is None or new is None or old["_exists"] != new["_exists"]:
            added = old is None or (new is not None and new["_exists"])
            row = new if added else old
            subject = "room" if row["_exists"] else "metadata for room"
            changes.append({**common, "field": f"rooms.{identity}", "label": f"Room {identity}", "before": old, "after": new,
                            "description": f"{'Added' if added else 'Removed'} {subject} {identity} ({row['name']}).",
                            "status": "added" if added else "removed"})
            continue
        for field in ("name",):
            # Default display numbers can shift when another room is added.
            # Only an authored metadata edit is a rename of this stable ID.
            if old.get(field) != new.get(field) and old["_explicit_name"] != new["_explicit_name"]:
                changes.append({**common, "field": f"rooms.{identity}.{field}", "label": f"Room {identity} {field}",
                                "before": old.get(field), "after": new.get(field),
                                "description": f"Room {identity} {field}: {_text(old.get(field))} to {_text(new.get(field))}.", "status": "modified"})
        for finish in sorted(old["finishes"].keys() | new["finishes"].keys()):
            left, right = old["finishes"].get(finish), new["finishes"].get(finish)
            # Recording inherited defaults for the first time is not a refinish.
            if left == right or (left is None and new["_explicit_finishes"].get(finish) is None) or (
                    right is None and old["_explicit_finishes"].get(finish) is None):
                continue
            changes.append({**common, "field": f"rooms.{identity}.finishes.{finish}", "label": f"Room {identity} {finish} finish",
                            "before": left, "after": right,
                            "description": f"Room {identity} {finish} finish: {_text(left) if left is not None else 'inherited'} to {_text(right) if right is not None else 'inherited'}.",
                            "status": "modified"})
        for field, label, unit in (("area_m2", "clear area", "m2"), ("perimeter_m", "internal perimeter", "m"),
                                   ("outline_mm", "boundary", ""), ("holes_mm", "boundary holes", "")):
            # Old single-room snapshots did not record room geometry separately.
            if field not in old or field not in new or old[field] == new[field]:
                continue
            changes.append({**common, "field": f"rooms.{identity}.{field}", "label": f"Room {identity} {label}",
                            "before": old[field], "after": new[field],
                            "description": f"Room {identity} {label}: {_text(old[field])}{(' ' + unit) if unit else ''} to {_text(new[field])}{(' ' + unit) if unit else ''}.",
                            "status": "modified"})
    return changes


def _host_label(settings: dict, host: str) -> str:
    if host in WALL_LABELS:
        return WALL_LABELS[host]
    partition = _partitions(settings).get(host)
    if partition is None:
        raise ValueError(f"Saved opening host {host} does not exist")
    return f"partition, authored start {_point_text(partition['start_mm'])} to end {_point_text(partition['end_mm'])}"


def _openings(settings: dict) -> dict[str, dict]:
    """Normalise historical implicit openings without changing saved snapshots."""
    source = settings.get("openings", LEGACY_OPENINGS)
    if not isinstance(source, (list, tuple)) or len(source) > 40:
        raise ValueError("Saved openings must be a list of at most 40 elements")
    result = {}
    for opening in source:
        if not isinstance(opening, dict) or not isinstance(opening.get("id"), str) or not re.fullmatch(r"[DN][1-9][0-9]{0,2}", opening["id"]):
            raise ValueError("Saved openings require valid stable D/N element IDs")
        identity = opening["id"]
        if identity in result:
            raise ValueError(f"Saved openings have duplicate element ID {identity}")
        if opening.get("kind") not in {"door", "window"} or opening.get("host") not in (WALL_LABELS.keys() | _partitions(settings).keys()):
            raise ValueError(f"Saved opening {identity} has an invalid kind or host")
        for field in ("width_mm", "height_mm", "sill_mm"):
            number = _decimal(opening.get(field), f"Saved opening {identity} {field}")
            if field != "sill_mm" and number <= 0:
                raise ValueError(f"Saved opening {identity} must have positive dimensions")
        if opening.get("offset_mm") is not None:
            _decimal(opening["offset_mm"], f"Saved opening {identity} offset_mm")
        result[identity] = {**opening, "offset_mm": opening.get("offset_mm")}
    return result


def _opening_start(settings: dict, opening: dict) -> float | None:
    if opening["offset_mm"] is not None:
        return opening["offset_mm"]
    if opening["host"].startswith("P"):
        partition = _partitions(settings)[opening["host"]]
        span = math.dist(partition["start_mm"], partition["end_mm"])
    else:
        span = settings.get("length_mm" if opening["host"] in {"W1", "W3"} else "width_mm")
    if span is None:
        return None
    return float((_decimal(span, "Saved wall span") - _decimal(opening["width_mm"], "Saved opening width")) / 2)


def _opening_position(settings: dict, opening: dict) -> str:
    start = _opening_start(settings, opening)
    if opening["offset_mm"] is None:
        return "centred" + (f" ({_text(start)} mm start)" if start is not None else "")
    return f"{_text(start)} mm start"


def _partition_opening_point(settings: dict, opening: dict) -> tuple[float, float] | None:
    if not opening["host"].startswith("P"):
        return None
    partition = _partitions(settings)[opening["host"]]
    start, end = partition["start_mm"], partition["end_mm"]
    offset = _opening_start(settings, opening)
    span = math.dist(start, end)
    return tuple(round(left + (right - left) * offset / span, 6) for left, right in zip(start, end, strict=True))


def _opening_description(settings: dict, opening: dict) -> str:
    reference = "authored partition start" if opening["host"].startswith("P") else "inner wall reference"
    return (f"{opening['kind'].capitalize()} {opening['id']} on {opening['host']} ({_host_label(settings, opening['host'])}), "
            f"{_text(opening['width_mm'])} x {_text(opening['height_mm'])} mm; sill {_text(opening['sill_mm'])} mm; "
            f"{_opening_position(settings, opening)} from the {reference}")


def _opening_changes(before: dict, after: dict) -> list[dict]:
    old_rows, new_rows = _openings(before), _openings(after)
    changes = []
    for identity in sorted(old_rows.keys() | new_rows.keys(), key=lambda value: (value[0], int(value[1:]))):
        old, new = old_rows.get(identity), new_rows.get(identity)
        if old is None or new is None:
            added = old is None
            opening = new if added else old
            settings = after if added else before
            changes.append({"field": f"openings.{identity}", "label": f"{opening['kind'].capitalize()} {identity}",
                            "before": old, "after": new, "description": f"{'Added' if added else 'Removed'} {_opening_description(settings, opening)}.",
                            "category": "openings", "element_id": identity, "status": "added" if added else "removed"})
            continue
        label = f"{new['kind'].capitalize()} {identity}"
        for field, field_label in (("kind", "type"), ("host", "host wall"), ("width_mm", "width"),
                                   ("height_mm", "height"), ("sill_mm", "sill height")):
            if old[field] == new[field]:
                continue
            old_text, new_text = _text(old[field]), _text(new[field])
            if field == "host":
                old_text, new_text = f"{old[field]} ({_host_label(before, old[field])})", f"{new[field]} ({_host_label(after, new[field])})"
            suffix = " mm" if field.endswith("_mm") else ""
            changes.append({"field": f"openings.{identity}.{field}", "label": f"{label} {field_label}",
                            "before": old[field], "after": new[field],
                            "description": f"{label} {field_label}: {old_text}{suffix} to {new_text}{suffix}.",
                            "category": "openings", "element_id": identity, "status": "modified"})
        old_start, new_start = _opening_start(before, old), _opening_start(after, new)
        old_point, new_point = _partition_opening_point(before, old), _partition_opening_point(after, new)
        moved_with_partition = old_point is not None and new_point is not None and old_point != new_point
        if old["offset_mm"] != new["offset_mm"] or old_start != new_start or moved_with_partition:
            movement = (f" Authored partition position: {_point_text(old_point)} to {_point_text(new_point)}."
                        if moved_with_partition else "")
            reference = "authored" if old["host"].startswith("P") or new["host"].startswith("P") else "inner"
            changes.append({"field": f"openings.{identity}.offset_mm", "label": f"{label} position",
                            "before": old["offset_mm"], "after": new["offset_mm"],
                            "before_start_mm": old_start, "after_start_mm": new_start,
                            "description": f"{label} position: {_opening_position(before, old)} to {_opening_position(after, new)}; offsets follow each host's {reference} reference direction." + movement,
                            "category": "openings", "element_id": identity, "status": "modified"})
    return changes


def _snapshot(result: dict, name: str) -> tuple[dict[str, dict], str, Decimal]:
    if not isinstance(result, dict) or not isinstance(result.get("settings"), dict):
        raise ValueError(f"{name} is missing its saved settings")
    if not isinstance(result.get("revision"), str) or not result["revision"]:
        raise ValueError(f"{name} is missing its revision identity")
    totals = result.get("totals", {})
    currency = totals.get("currency")
    if not isinstance(currency, str) or not currency or currency != result["settings"].get("currency"):
        raise ValueError(f"{name} currency is missing or inconsistent")
    if not isinstance(result.get("boq"), list):
        raise ValueError(f"{name} is missing its saved BOQ")
    rows: dict[str, dict] = {}
    for item in result["boq"]:
        if not isinstance(item, dict) or not isinstance(item.get("code"), str) or not item["code"].strip():
            raise ValueError(f"{name} has an invalid BOQ item code")
        code = item["code"]
        if code in rows:
            raise ValueError(f"{name} has duplicate BOQ code {code}")
        if "rate_code" in item and (not isinstance(item["rate_code"], str) or not item["rate_code"].strip()):
            raise ValueError(f"{name} item {code} has an invalid base rate code")
        for field in ("group_id", "group_name"):
            if item.get(field) is not None and not isinstance(item[field], str):
                raise ValueError(f"{name} item {code} has an invalid {field}")
        if not isinstance(item.get("unit"), str) or not item["unit"].strip():
            raise ValueError(f"{name} item {code} is missing its unit")
        quantity = _decimal(item.get("quantity"), f"{name} {code} quantity")
        net = _decimal(item.get("net_quantity", quantity), f"{name} {code} net quantity")
        gross = _decimal(item.get("gross_quantity", quantity), f"{name} {code} gross quantity")
        wastage = _decimal(item.get("wastage_percent", 0), f"{name} {code} wastage")
        if gross != quantity or wastage > 100:
            raise ValueError(f"{name} item {code} has inconsistent gross quantity or wastage")
        if "net_quantity" in item and abs(net * (1 + wastage / 100) - gross) > Decimal("0.000001"):
            raise ValueError(f"{name} item {code} gross quantity does not equal net plus wastage")
        rate = _decimal(item.get("rate"), f"{name} {code} rate")
        amount = _decimal(item.get("amount"), f"{name} {code} amount", money=True)
        if _rounded(quantity * rate) != amount:
            raise ValueError(f"{name} item {code} amount does not equal rounded quantity times rate")
        if not isinstance(item.get("description"), str):
            raise ValueError(f"{name} item {code} is missing its description")
        element_ids = item.get("element_ids", [])
        if not isinstance(element_ids, list) or any(not isinstance(value, str) for value in element_ids):
            raise ValueError(f"{name} item {code} has invalid element references")
        rows[code] = {**item, "quantity": quantity, "rate": rate, "amount": amount, "element_ids": sorted(set(element_ids))}
    subtotal = _decimal(totals.get("subtotal"), f"{name} subtotal", money=True)
    if sum((item["amount"] for item in rows.values()), ZERO) != subtotal:
        raise ValueError(f"{name} BOQ amounts do not reconcile to the saved subtotal")
    return rows, currency, subtotal


def compare_revisions(before: dict, after: dict) -> dict[str, Any]:
    """Return the factual description and estimated cost difference from A to B.

    Positive cost deltas are increases; negative deltas are credits. Currency
    changes are rejected because an exchange rate cannot be inferred.
    """
    old_rows, currency, old_total = _snapshot(before, "Revision A")
    new_rows, new_currency, new_total = _snapshot(after, "Revision B")
    if currency != new_currency:
        raise ValueError("Choose revisions with the same currency. No currency conversion is applied.")

    library_changes = _library_changes(before, after)
    old_settings, new_settings = (_flat(_comparison_settings(result["settings"]))
                                  for result in (before, after))
    ordered_fields = list(FIELD_LABELS) + sorted((old_settings.keys() | new_settings.keys()) - FIELD_LABELS.keys())
    setting_changes = []
    for field in ordered_fields:
        old, new = old_settings.get(field), new_settings.get(field)
        if old == new:
            continue
        label, category = FIELD_LABELS.get(field, (field.replace("_", " ").capitalize(), "settings"))
        if field.startswith("rate_overrides."):
            label, category = f"Explicit rate override {field.split('.')[-1]}", "rates"
        suffix = " mm" if field.endswith("_mm") else ""
        previous, proposed = _text(old), _text(new)
        if field in {"exterior_finish.kind", "inner_leaf_finish"}:
            previous, proposed = WALL_TREATMENTS.get(previous, previous), WALL_TREATMENTS.get(proposed, proposed)
        setting_changes.append({"field": field, "label": label, "before": old, "after": new,
                                "description": f"{label}: {previous}{suffix} to {proposed}{suffix}.", "category": category})
    setting_changes.extend(_opening_changes(before["settings"], after["settings"]))
    setting_changes.extend(_partition_changes(before["settings"], after["settings"]))
    setting_changes.extend(_room_changes(before, after))
    renamed_rooms = {item["element_id"] for item in setting_changes
                     if item["category"] == "rooms" and item["field"].endswith(".name")}
    for line in diff_allowances(before["settings"].get("allowances", []), after["settings"].get("allowances", [])):
        kind = "Prime cost item" if line["kind"] == "prime_cost" else "Provisional sum"
        label = f"{kind} {line['id']}"
        old_amount, new_amount = line["before_amount"], line["after_amount"]
        description = f"{label} ({line['description']}): AUD {old_amount:,.2f} to AUD {new_amount:,.2f}."
        if line["before"] and line["after"] and old_amount == new_amount:
            description = f"Updated {label.lower()} ({line['description']}) at the same allowance of AUD {new_amount:,.2f}."
        setting_changes.append({"field": f"allowances.{line['id']}", "label": label,
                                "before": line["before"], "after": line["after"], "description": description,
                                "category": "allowances"})

    items = []
    for code in dict.fromkeys([*old_rows, *new_rows]):
        old, new = old_rows.get(code), new_rows.get(code)
        if old == new:
            continue
        old_quantity, new_quantity = (row["quantity"] if row else ZERO for row in (old, new))
        old_rate, new_rate = (row["rate"] if row else ZERO for row in (old, new))
        old_amount, new_amount = (row["amount"] if row else ZERO for row in (old, new))
        old_description, new_description = (row["description"] if row else "" for row in (old, new))
        old_unit, new_unit = (row["unit"] if row else "" for row in (old, new))
        delta = new_amount - old_amount
        quantity_delta: Decimal | None = new_quantity - old_quantity
        comparable = not (old and new and old_unit != new_unit)
        if old is None:
            status = "added"
            description = f"Added {new_description}: {_text(new_quantity)} {new_unit}."
            quantity_effect, rate_effect, rounding_effect = delta, ZERO, ZERO
        elif new is None:
            status = "removed"
            description = f"Removed {old_description}: {_text(old_quantity)} {old_unit}."
            quantity_effect, rate_effect, rounding_effect = delta, ZERO, ZERO
        elif not comparable:
            status = "unit_changed"
            description = f"Measurement unit changed from {old_unit} to {new_unit}; quantity and rate effects are not directly comparable."
            quantity_delta = quantity_effect = rate_effect = rounding_effect = None
        else:
            parts = []
            if old_quantity != new_quantity:
                parts.append(f"Quantity {_text(old_quantity)} to {_text(new_quantity)} {new_unit}")
            if old_rate != new_rate:
                parts.append(f"unit rate {_text(old_rate)} to {_text(new_rate)} {currency}/{new_unit}")
            for field, label, suffix in (("net_quantity", "net quantity", f" {new_unit}"),
                                         ("wastage_percent", "wastage", "%")):
                # Newly added presentation fields alone are not a variation.
                if field in old and field in new and old[field] != new[field]:
                    parts.append(f"{label} {_text(old[field])}{suffix} to {_text(new[field])}{suffix}")
            if old_description != new_description:
                parts.append(f"description changed from '{old_description}' to '{new_description}'")
            if old["element_ids"] != new["element_ids"]:
                parts.append("linked model elements changed")
            for field, label in (("group_id", "room/group ID"), ("group_name", "room/group name"), ("rate_code", "base rate code")):
                if field == "group_name" and old.get("group_id") == new.get("group_id") and new.get("group_id") not in renamed_rooms:
                    continue
                if old.get(field) is not None and new.get(field) is not None and old[field] != new[field]:
                    parts.append(f"{label} {_text(old[field])} to {_text(new[field])}")
            old_facts, new_facts = _rate_facts(before, old), _rate_facts(after, new)
            for field, label in (("origin", "rate origin"), ("source", "rate source"), ("effective_date", "rate effective date"),
                                 ("library_rate", "selected library base rate")):
                left, right = old_facts[field], new_facts[field]
                # Additional provenance for a legacy row is not itself a change.
                if left is not None and right is not None and left != right:
                    parts.append(f"{label} {_text(left)} to {_text(right)}")
            if not parts:
                # Extra snapshot fields do not imply a design or price change.
                continue
            description = "; ".join(parts) + "."
            status = "description_changed" if old_quantity == new_quantity and old_rate == new_rate else "modified"
            quantity_effect = _rounded((new_quantity - old_quantity) * old_rate)
            rate_effect = _rounded(new_quantity * (new_rate - old_rate))
            rounding_effect = delta - quantity_effect - rate_effect
        items.append({
            "code": code, "description": description,
            "rate_code": (new or old).get("rate_code", code),
            "group_id": (new or old).get("group_id"), "group_name": (new or old).get("group_name"),
            "before_group_id": (old or {}).get("group_id"), "after_group_id": (new or {}).get("group_id"),
            "before_group_name": (old or {}).get("group_name"), "after_group_name": (new or {}).get("group_name"),
            "before_description": old_description, "after_description": new_description,
            "unit": new_unit or old_unit if comparable else f"{old_unit} to {new_unit}",
            "before_unit": old_unit, "after_unit": new_unit,
            "before_quantity": _float(old_quantity), "after_quantity": _float(new_quantity),
            "before_net_quantity": (old or {}).get("net_quantity", _float(old_quantity)),
            "after_net_quantity": (new or {}).get("net_quantity", _float(new_quantity)),
            "before_wastage_percent": (old or {}).get("wastage_percent", 0),
            "after_wastage_percent": (new or {}).get("wastage_percent", 0),
            "before_gross_quantity": _float(old_quantity), "after_gross_quantity": _float(new_quantity),
            "quantity_delta": _float(quantity_delta) if quantity_delta is not None else None,
            "before_rate": _float(old_rate), "after_rate": _float(new_rate),
            "before_amount": _float(old_amount), "after_amount": _float(new_amount), "cost_delta": _float(delta),
            "quantity_effect": _float(quantity_effect) if quantity_effect is not None else None,
            "rate_effect": _float(rate_effect) if rate_effect is not None else None,
            "rounding_effect": _float(rounding_effect) if rounding_effect is not None else None,
            **_rate_bridge(before, after, old, new, new_quantity, rate_effect),
            "status": status, "element_ids": sorted(set((old or {}).get("element_ids", []) + (new or {}).get("element_ids", []))),
        })

    net_change = new_total - old_total
    if sum((Decimal(str(item["cost_delta"])) for item in items), ZERO) != net_change:
        raise ValueError("Changed BOQ items do not reconcile to the project cost difference")
    comparable = all(item["quantity_effect"] is not None for item in items)
    totals = {
        "before": _float(old_total), "after": _float(new_total), "net_change": _float(net_change),
        "additions": _float(sum((Decimal(str(item["cost_delta"])) for item in items if item["cost_delta"] > 0), ZERO)),
        "omissions": _float(-sum((Decimal(str(item["cost_delta"])) for item in items if item["cost_delta"] < 0), ZERO)),
    }
    for key in ("quantity_effect", "rate_effect", "rounding_effect"):
        totals[key] = _float(sum((Decimal(str(item[key])) for item in items), ZERO)) if comparable else None
    for key in ("library_rate_effect", "override_rate_effect", "unattributed_rate_effect"):
        totals[key] = _float(sum((Decimal(str(item[key])) for item in items), ZERO)) if comparable else None
    notes = [
        "This is the estimated difference between saved, modelled BOQ snapshots. Unit rates are illustrative unless replaced with project rates.",
        "Cost increases and decreases include quantity, rate and scope changes. A colour or description change can have zero cost at the saved rates.",
        "For matching units: quantity effect = (quantity B - quantity A) x rate A; rate effect = quantity B x (rate B - rate A). A rounding adjustment reconciles to the saved line amounts.",
        "Priced quantities are gross quantities, including each saved item's wastage and paint coat multiplier. Wastage-only edits belong to the quantity effect, not the rate effect.",
        "Added and removed items are assigned to the quantity/scope effect. A changed measurement unit has no quantity/rate decomposition.",
        "Only affected BOQ items are listed; project totals include the complete saved BOQ. Omissions are positive absolute cost decreases or credits.",
        "Historical amounts, currencies and scope are preserved. Newly introduced trades are scope additions when absent from revision A; defaults alone do not change saved inputs.",
        "Rate attribution first applies library B while retaining override A, then applies override B. The library effect is rounded first; the override residual reconciles to the rounded total rate effect. Unknown historical provenance remains unattributed.",
        "Demolition, abortive work, rework, fees, preliminaries, taxes and programme/time effects are excluded unless separately modelled and priced. This report is not an approved contract variation.",
    ]
    return {
        "before_revision": before["revision"], "after_revision": after["revision"],
        "before_label": before["settings"].get("project_name", "Revision A"),
        "after_label": after["settings"].get("project_name", "Revision B"),
        "before_created_at": before.get("created_at"), "after_created_at": after.get("created_at"),
        "currency": currency, "setting_changes": setting_changes, "items": items, "totals": totals,
        "design_changes": [item for item in setting_changes if item["category"] not in {"rates", "pricing", "quote", "allowances"}],
        "pricing_changes": [item for item in setting_changes if item["category"] in {"rates", "pricing", "quote", "allowances"}],
        "library_changes": library_changes, "before_rate_library": _saved_library(before), "after_rate_library": _saved_library(after),
        "rate_provenance": _provenance_rows(before, after, old_rows, new_rows, items),
        "summary": [item["description"] for item in [*setting_changes, *library_changes]] or [item["description"] for item in items],
        "has_changes": bool(setting_changes or library_changes or items), "notes": notes,
    }


def _safe_cell(value: Any) -> Any:
    return "'" + value if isinstance(value, str) and value.startswith(("=", "+", "-", "@")) else value


def _signed(value: float | None) -> str:
    return "n/a" if value is None else f"{value:+,.2f}" if value else "0.00"


def _provenance_xlsx(workbook: Workbook, comparison: dict) -> None:
    sheet = workbook.create_sheet("Rate provenance")
    sheet.sheet_view.showGridLines = False
    sheet.append(["Saved rate library and override audit"])
    sheet.merge_cells("A1:C1")
    sheet.row_dimensions[1].height = 30
    for note in _library_notes(comparison):
        sheet.append([_safe_cell(note)])
        sheet.merge_cells(start_row=sheet.max_row, start_column=1, end_row=sheet.max_row, end_column=3)
        sheet.row_dimensions[sheet.max_row].height = max(30, 14 * (len(note) // 180 + 1))
    sheet.append(["Item", "Revision A pricing basis", "Revision B pricing basis and rate cause"])
    header = sheet.max_row
    for cell in sheet[header]:
        cell.fill = PatternFill("solid", fgColor=INK)
        cell.font = Font(name="Calibri", size=10, bold=True, color="FFFFFF")
    for item in comparison.get("rate_provenance", []):
        after_text = _provenance_description(item["after"]) + f" Cause: {item['rate_change_cause']}."
        if item.get("rate_bridge") is not None:
            after_text += (f" Bridge {_text(item['rate_bridge'])} {comparison['currency']}; library effect {_signed(item['library_rate_effect'])}; "
                           f"override effect {_signed(item['override_rate_effect'])}; unattributed effect {_signed(item['unattributed_rate_effect'])} {comparison['currency']}.")
        before_text = _provenance_description(item["before"])
        sheet.append([_safe_cell(item["code"]), _safe_cell(before_text), _safe_cell(after_text)])
        sheet.row_dimensions[sheet.max_row].height = min(409, max(48, 14 * (max(len(before_text), len(after_text)) // 87 + 1) + 10))
    for row in sheet:
        for cell in row:
            cell.alignment = Alignment(wrap_text=True, vertical="top")
            if cell.row != header:
                cell.font = Font(name="Calibri", size=14 if cell.row == 1 else 10, bold=cell.row == 1)
    for column, width in {"A": 23, "B": 95, "C": 95}.items():
        sheet.column_dimensions[column].width = width
    sheet.freeze_panes = f"B{header + 1}"
    sheet.page_setup.orientation = "landscape"
    sheet.page_setup.paperSize = sheet.PAPERSIZE_A3
    sheet.page_setup.fitToWidth = 1
    sheet.page_setup.fitToHeight = 0
    sheet.sheet_properties.pageSetUpPr.fitToPage = True
    sheet.print_area = f"A1:C{sheet.max_row}"
    sheet.print_title_rows = f"{header}:{header}"


def _xlsx(comparison: dict, directory: Path) -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Change report"
    sheet.sheet_view.showGridLines = False
    sheet.merge_cells("A1:M1")
    sheet["A1"] = "House change report"
    sheet["A1"].font = Font(name="Calibri", size=20, bold=True, color=INK)
    sheet.row_dimensions[1].height = 32
    for row, text in [(2, f"A {comparison['before_revision']} - {comparison['before_label']}"),
                      (3, f"B {comparison['after_revision']} - {comparison['after_label']}"),
                      (4, f"Currency {comparison['currency']}. Saved estimate snapshots; only affected BOQ items shown below.")]:
        sheet.merge_cells(start_row=row, start_column=1, end_row=row, end_column=13)
        sheet.cell(row, 1, _safe_cell(text))
        sheet.row_dimensions[row].height = 22
    labels = ["Estimate A", "Estimate B", "Cost increases", "Cost decreases / credits", "Net estimated change"]
    values = [comparison["totals"][key] for key in ("before", "after", "additions", "omissions", "net_change")]
    spans = [(1, 3), (4, 5), (6, 7), (8, 10), (11, 13)]
    for label, value, (first, last) in zip(labels, values, spans, strict=True):
        for row in (6, 7):
            sheet.merge_cells(start_row=row, start_column=first, end_row=row, end_column=last)
        sheet.cell(6, first, label).font = Font(name="Calibri", size=10, color=INK)
        cell = sheet.cell(7, first, value)
        cell.font = Font(name="Calibri", size=16, bold=True, color=TEAL)
        cell.alignment = Alignment(horizontal="left", vertical="center")
        cell.number_format = '#,##0.00;[Red](#,##0.00);"0.00"'
    sheet.row_dimensions[7].height = 30
    row = 10
    sheet.merge_cells(start_row=row, start_column=1, end_row=row, end_column=13)
    sheet.cell(row, 1, "Description of changes").font = Font(name="Calibri", bold=True, size=13, color=INK)
    row += 1
    for text in comparison["summary"] or ["No design, description or cost changes between these saved revisions."]:
        sheet.merge_cells(start_row=row, start_column=1, end_row=row, end_column=13)
        sheet.cell(row, 1, _safe_cell(text)).alignment = Alignment(wrap_text=True, vertical="center")
        sheet.row_dimensions[row].height = 28
        row += 1
    row += 2
    header_row = row
    headers = ["Item", "Description of change", "Unit", "Quantity A", "Quantity B", "Rate A", "Rate B", "Amount A", "Amount B",
               "Cost change", "Quantity/scope", "Rate effect", "Rounding adjustment"]
    for column, text in enumerate(headers, 1):
        cell = sheet.cell(row, column, text)
        cell.fill = PatternFill("solid", fgColor=INK)
        cell.font = Font(name="Calibri", bold=True, color="FFFFFF")
        cell.alignment = Alignment(wrap_text=True, vertical="center")
    sheet.row_dimensions[row].height = 34
    first_data_row = row + 1
    for item in comparison["items"]:
        row += 1
        values = [item[key] for key in ("code", "description", "unit", "before_quantity", "after_quantity", "before_rate", "after_rate", "before_amount", "after_amount")]
        values.append(f"=ROUND(I{row}-H{row},2)")
        if item["status"] == "unit_changed":
            values.extend(["n/a", "n/a", "n/a"])
        elif item["status"] in {"added", "removed"}:
            values.extend([f"=J{row}", 0, 0])
        else:
            values.extend([f"=ROUND((E{row}-D{row})*F{row},2)", f"=ROUND(E{row}*(G{row}-F{row}),2)", f"=ROUND(J{row}-K{row}-L{row},2)"])
        for column, value in enumerate(values, 1):
            cell = sheet.cell(row, column, value if column >= 10 else _safe_cell(value))
            cell.font = Font(name="Calibri", size=10, color="000000")
            cell.alignment = Alignment(wrap_text=True, vertical="center", horizontal="left" if column < 4 else "right")
            if row % 2:
                cell.fill = PatternFill("solid", fgColor=PALE)
            if column in (4, 5):
                cell.number_format = "#,##0.000"
            elif column >= 6:
                cell.number_format = '#,##0.00;[Red](#,##0.00);"0.00"'
        sheet.row_dimensions[row].height = min(120, max(48, 14 * (len(item["description"]) // 65 + 1)))
    last_data_row = row
    row += 2
    sheet.cell(row, 2, "Net estimated change (affected items)")
    sheet.cell(row, 10, f"=SUM(J{first_data_row}:J{last_data_row})" if comparison["items"] else 0)
    for key, column in (("quantity_effect", 11), ("rate_effect", 12), ("rounding_effect", 13)):
        sheet.cell(row, column, f"=SUM({chr(64 + column)}{first_data_row}:{chr(64 + column)}{last_data_row})"
                   if comparison["items"] and comparison["totals"][key] is not None else "n/a" if comparison["totals"][key] is None else 0)
    for cell in sheet[row]:
        cell.fill = PatternFill("solid", fgColor="D8EAE7")
        cell.font = Font(name="Calibri", bold=True, color=INK)
        cell.number_format = '#,##0.00;[Red](#,##0.00);"0.00"'
    row += 3
    for note in comparison["notes"]:
        sheet.merge_cells(start_row=row, start_column=1, end_row=row, end_column=13)
        sheet.cell(row, 1, note).alignment = Alignment(wrap_text=True, vertical="center")
        sheet.cell(row, 1).font = Font(name="Calibri", size=10, color="53636B")
        sheet.row_dimensions[row].height = 32 if len(note) < 220 else 44
        row += 1
    for column, width in {"A": 12, "B": 65, "C": 15, "D": 13, "E": 13, "F": 14, "G": 14, "H": 17, "I": 17, "J": 17, "K": 18, "L": 17, "M": 18}.items():
        sheet.column_dimensions[column].width = width
    sheet.freeze_panes = f"D{first_data_row}"
    sheet.auto_filter.ref = f"A{header_row}:M{max(first_data_row, last_data_row)}"
    sheet.page_setup.orientation = "landscape"
    sheet.page_setup.paperSize = sheet.PAPERSIZE_A3
    sheet.page_setup.fitToWidth = 1
    sheet.page_setup.fitToHeight = 0
    sheet.sheet_properties.pageSetUpPr.fitToPage = True
    sheet.print_area = f"A1:M{row - 1}"
    sheet.oddFooter.center.text = f"A {comparison['before_revision']} to B {comparison['after_revision']}"
    workbook.calculation = CalcProperties(fullCalcOnLoad=True)
    _provenance_xlsx(workbook, comparison)
    workbook.save(directory / "changes.xlsx")


def _csv(comparison: dict, directory: Path) -> None:
    with (directory / "changes.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["From revision A", comparison["before_revision"], "To revision B", comparison["after_revision"], "Currency", comparison["currency"]])
        writer.writerow(["Estimate A", comparison["totals"]["before"], "Estimate B", comparison["totals"]["after"], "Net estimated change", comparison["totals"]["net_change"]])
        writer.writerow(["Description of changes"])
        for description in comparison["summary"] or ["No design, description or cost changes."]:
            writer.writerow([_safe_cell(description)])
        for note in _library_notes(comparison):
            writer.writerow(["Rate library", _safe_cell(note)])
        keys = ["code", "description", "before_unit", "after_unit", "before_quantity", "after_quantity", "before_rate", "after_rate", "before_amount", "after_amount", "cost_delta", "quantity_effect", "rate_effect", "rounding_effect", "status", "element_ids",
                "before_rate_origin", "after_rate_origin", "before_rate_source", "after_rate_source", "before_rate_effective_date", "after_rate_effective_date",
                "before_library_rate", "after_library_rate", "before_library_currency", "after_library_currency", "rate_bridge", "rate_change_cause",
                "library_rate_effect", "override_rate_effect", "unattributed_rate_effect"]
        writer.writerow(keys)
        for item in comparison["items"]:
            writer.writerow([_safe_cell(", ".join(item[key]) if key == "element_ids" else item[key]) for key in keys])
        for note in comparison["notes"]:
            writer.writerow(["Note", note])
        writer.writerow(["All saved rate provenance", "Item", "Revision A", "Revision B", "Cause"])
        for item in comparison.get("rate_provenance", []):
            writer.writerow(["Rate provenance", _safe_cell(item["code"]), _safe_cell(_provenance_description(item["before"])),
                             _safe_cell(_provenance_description(item["after"])), _safe_cell(item["rate_change_cause"])])


def _pdf(comparison: dict, directory: Path) -> None:
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name="ChangeTitle", fontName="Helvetica-Bold", fontSize=20, leading=25, textColor=colors.HexColor("#" + INK), spaceAfter=10))
    styles.add(ParagraphStyle(name="ChangeBody", fontName="Helvetica", fontSize=9, leading=12, spaceAfter=5))
    styles.add(ParagraphStyle(name="ChangeSmall", fontName="Helvetica", fontSize=7.5, leading=10, spaceAfter=4))
    styles.add(ParagraphStyle(name="ChangeHeader", fontName="Helvetica-Bold", fontSize=7.5, leading=10, textColor=colors.white))
    paragraph = lambda value, style="ChangeBody": Paragraph(html.escape(str(value)), styles[style])
    story = [paragraph("House change report", "ChangeTitle"),
             paragraph(f"A {comparison['before_revision']} - {comparison['before_label']}"),
             paragraph(f"B {comparison['after_revision']} - {comparison['after_label']}"),
             paragraph(f"Saved estimate comparison in {comparison['currency']}."), Spacer(1, 8)]
    total_keys = ["before", "after", "additions", "omissions", "net_change"]
    total_labels = ["Estimate A", "Estimate B", "Cost increases", "Cost decreases / credits", "Net estimated change"]
    summary = Table([[paragraph(label, "ChangeSmall") for label in total_labels],
                     [paragraph(f"{comparison['totals'][key]:,.2f}") for key in total_keys]], colWidths=[152] * 5)
    summary.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#" + PALE)), ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#C9D9DE")),
                                ("LEFTPADDING", (0, 0), (-1, -1), 10), ("TOPPADDING", (0, 0), (-1, -1), 8), ("BOTTOMPADDING", (0, 0), (-1, -1), 8)]))
    story.extend([summary, Spacer(1, 12), paragraph("Description of changes", "Heading3")])
    for description in comparison["summary"] or ["No design, description or cost changes between these saved revisions."]:
        story.append(paragraph(description))
    story.extend([Spacer(1, 8), paragraph("Affected BOQ items", "Heading3")])
    headers = ["Item", "Description of change", "Qty A", "Qty B", "Unit", "Rate A", "Rate B", "Amount A", "Amount B", "Cost change"]
    table_rows = [[paragraph(header, "ChangeHeader") for header in headers]]
    for item in comparison["items"]:
        values = [item["code"], item["description"], _text(item["before_quantity"]), _text(item["after_quantity"]), item["unit"],
                  _text(item["before_rate"]), _text(item["after_rate"]), f"{item['before_amount']:,.2f}", f"{item['after_amount']:,.2f}", _signed(item["cost_delta"])]
        table_rows.append([paragraph(value, "ChangeSmall") for value in values])
    if not comparison["items"]:
        table_rows.append([paragraph("No affected BOQ items", "ChangeSmall")] + [""] * 9)
    # Keep stable BOQ codes readable when the expanded trades have long names.
    table = Table(table_rows, colWidths=[90, 161, 43, 43, 42, 50, 50, 61, 61, 65], repeatRows=1, hAlign="LEFT")
    table.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#" + INK)), ("VALIGN", (0, 0), (-1, -1), "TOP"),
                              ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#" + PALE)]),
                              ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                              ("LINEBELOW", (0, 0), (-1, -1), 0.35, colors.HexColor("#D5E0E4"))]))
    story.extend([table, Spacer(1, 10)])
    effects = comparison["totals"]
    story.append(KeepTogether([
        paragraph(f"Quantity/scope effect: {_signed(effects['quantity_effect'])}; rate effect: {_signed(effects['rate_effect'])}; rounding adjustment: {_signed(effects['rounding_effect'])} {comparison['currency']}.", "ChangeSmall"),
        paragraph(f"Rate effect attribution: library {_signed(effects.get('library_rate_effect'))}; overrides {_signed(effects.get('override_rate_effect'))}; unattributed {_signed(effects.get('unattributed_rate_effect'))} {comparison['currency']}.", "ChangeSmall"),
        *(paragraph(note, "ChangeSmall") for note in comparison["notes"]),
    ]))
    story.extend(_provenance_pdf(comparison, paragraph, landscape(A4)[0] - 72,
                                 title_style="ChangeTitle", body_style="ChangeSmall", header_style="ChangeHeader"))

    def footer(canvas, document):
        canvas.saveState()
        canvas.setFont("Helvetica", 7)
        canvas.setFillColor(colors.HexColor("#53636B"))
        canvas.drawString(30, 18, f"A {comparison['before_revision']} to B {comparison['after_revision']}")
        canvas.drawRightString(810, 18, f"Page {document.page}")
        canvas.restoreState()

    document = SimpleDocTemplate(str(directory / "changes.pdf"), pagesize=landscape(A4), rightMargin=30, leftMargin=30, topMargin=28, bottomMargin=34,
                                title="House change report", author="House Sync")
    document.build(story, onFirstPage=footer, onLaterPages=footer)



def _variation_pdf(comparison: dict, directory: Path) -> None:
    """Prepare an unsigned variation order with the saved A-to-B estimate evidence."""
    styles = getSampleStyleSheet()
    for name, size, leading, bold, colour in [
        ("VOTitle", 22, 27, True, INK),
        ("VOHeading", 11, 15, True, TEAL),
        ("VOBody", 9, 12, False, INK),
        ("VOSmall", 7.5, 10, False, INK),
        ("VOHeader", 7.5, 10, True, "FFFFFF"),
    ]:
        styles.add(ParagraphStyle(name=name, fontName="Helvetica-Bold" if bold else "Helvetica",
                                  fontSize=size, leading=leading, textColor=colors.HexColor("#" + colour), spaceAfter=4))
    paragraph = lambda value, style="VOBody": Paragraph(html.escape(str(value)), styles[style])
    reference = f"VO-{comparison['before_revision']}-{comparison['after_revision']}"
    width = landscape(A4)[0] - 72
    totals = comparison["totals"]
    currency = comparison["currency"]
    story = [paragraph("DRAFT VARIATION ORDER", "VOTitle"),
             paragraph("Pending contractual review and approval. All amounts are saved model estimates.", "VOSmall"), Spacer(1, 8)]
    metadata = [
        [f"Project: {comparison['after_label']}", f"Variation reference: {reference}"],
        ["Contract reference: ______________________________", "Instruction reference: ______________________________"],
        [f"Baseline A: {comparison['before_revision']} - {comparison['before_label']}",
         f"Proposed B: {comparison['after_revision']} - {comparison['after_label']}"],
        [f"Snapshot A: {comparison.get('before_created_at') or 'Not recorded'}",
         f"Snapshot B: {comparison.get('after_created_at') or 'Not recorded'}"],
    ]
    metadata_table = Table([[paragraph(value, "VOSmall") for value in row] for row in metadata], colWidths=[width / 2] * 2)
    metadata_table.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#" + PALE)),
                                        ("VALIGN", (0, 0), (-1, -1), "TOP"),
                                        ("LEFTPADDING", (0, 0), (-1, -1), 9), ("RIGHTPADDING", (0, 0), (-1, -1), 9),
                                        ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6)]))
    story.extend([metadata_table, Spacer(1, 10), paragraph(f"Estimated valuation - {currency}", "VOHeading")])
    labels = ["Baseline estimate A", "Additions", "Omissions / credits (deduct)", "Net estimated variation", "Revised estimate B"]
    keys = ["before", "additions", "omissions", "net_change", "after"]
    values = [f"{totals[key]:,.2f}" if key != "net_change" else _signed(totals[key]) for key in keys]
    valuation = Table([[paragraph(value, "VOSmall") for value in labels],
                       [paragraph(value) for value in values]], colWidths=[width / 5] * 5)
    valuation.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#E5F0ED")),
                                   ("VALIGN", (0, 0), (-1, -1), "TOP"),
                                   ("LEFTPADDING", (0, 0), (-1, -1), 9), ("RIGHTPADDING", (0, 0), (-1, -1), 9),
                                   ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6)]))
    story.extend([valuation, Spacer(1, 11), paragraph("Description of variation", "VOHeading")])
    for description in comparison["summary"] or ["No design, description or cost changes between the selected saved revisions."]:
        story.append(paragraph(description))
    story.extend([paragraph("Reason / instruction: __________________________________________________________________________________", "VOSmall"),
                  paragraph("Programme impact: Not assessed. Agreed extension of time (if any): __________________________________________", "VOSmall"),
                  Spacer(1, 10), paragraph("Review and authorisation", "VOHeading")])
    approval_rows = [
        ["Prepared by", "Contractor / valuation review", "Employer / authorised approval"],
        ["Name: __________________________", "Name: __________________________", "Name: __________________________"],
        ["Signature: _______________________", "Signature: _______________________", "Signature: _______________________"],
        ["Date: ___________________________", "Date: ___________________________", "Date: ___________________________"],
    ]
    approval = Table([[paragraph(value, "VOSmall") for value in row] for row in approval_rows], colWidths=[width / 3] * 3)
    approval.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#" + PALE)),
                                  ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#C9D9DE")),
                                  ("INNERGRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#D5E0E4")),
                                  ("VALIGN", (0, 0), (-1, -1), "TOP"),
                                  ("LEFTPADDING", (0, 0), (-1, -1), 9), ("RIGHTPADDING", (0, 0), (-1, -1), 9),
                                  ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6)]))
    story.append(KeepTogether([approval, Spacer(1, 8), paragraph(
        "Exporting this draft does not issue an instruction, certify payment or record approval. Complete the contract particulars, valuation and authorisation before issue.", "VOSmall")]))
    story.extend([PageBreak(), paragraph("VARIATION COST BREAKDOWN", "VOTitle"),
                  paragraph(f"{reference} | {currency} | Saved revision A to saved revision B", "VOSmall"),
                  paragraph("Affected BOQ items", "VOHeading")])
    headers = ["Item", "Description of change", "Qty A", "Qty B", "Unit", "Rate A", "Rate B", "Amount A", "Amount B", "Cost change"]
    rows = [[paragraph(value, "VOHeader") for value in headers]]
    for item in comparison["items"]:
        values = [item["code"], item["description"], _text(item["before_quantity"]), _text(item["after_quantity"]), item["unit"],
                  _text(item["before_rate"]), _text(item["after_rate"]), f"{item['before_amount']:,.2f}",
                  f"{item['after_amount']:,.2f}", _signed(item["cost_delta"])]
        rows.append([paragraph(value, "VOSmall") for value in values])
    if not comparison["items"]:
        rows.append([paragraph("No affected BOQ items", "VOSmall")] + [""] * 9)
    widths = [90, width - 505, 43, 43, 42, 50, 50, 61, 61, 65]
    table = Table(rows, colWidths=widths, repeatRows=1, hAlign="LEFT")
    table.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#" + INK)),
                              ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#" + PALE)]),
                              ("VALIGN", (0, 0), (-1, -1), "TOP"),
                              ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                              ("LINEBELOW", (0, 0), (-1, -1), 0.35, colors.HexColor("#D5E0E4"))]))
    story.extend([table, Spacer(1, 10), paragraph(
        f"Net of affected items: {_signed(totals['net_change'])} {currency}. Baseline estimate {totals['before']:,.2f} + additions {totals['additions']:,.2f} - omissions {totals['omissions']:,.2f} = revised estimate {totals['after']:,.2f} {currency}.", "VOBody")])
    story.append(KeepTogether([
        paragraph("Valuation basis and qualifications", "VOHeading"),
        paragraph(f"Rate effect attribution: library {_signed(totals.get('library_rate_effect'))}; overrides {_signed(totals.get('override_rate_effect'))}; unattributed {_signed(totals.get('unattributed_rate_effect'))} {currency}.", "VOSmall"),
        *(paragraph(note, "VOSmall") for note in comparison["notes"]),
    ]))
    story.extend(_provenance_pdf(comparison, paragraph, width,
                                 title_style="VOTitle", body_style="VOSmall", header_style="VOHeader"))

    def footer(canvas, document):
        canvas.saveState()
        canvas.setFont("Helvetica", 7)
        canvas.setFillColor(colors.HexColor("#53636B"))
        canvas.drawString(30, 18, f"{reference} | DRAFT - Pending approval")
        canvas.drawRightString(landscape(A4)[0] - 30, 18, f"Page {document.page}")
        canvas.restoreState()

    document = SimpleDocTemplate(str(directory / "variation-order.pdf"), pagesize=landscape(A4),
                                rightMargin=30, leftMargin=30, topMargin=28, bottomMargin=34,
                                title="Draft variation order", author="House Sync", pageCompression=0)
    document.build(story, onFirstPage=footer, onLaterPages=footer)


def export_change_report(comparison: dict, output_dir: Path) -> dict[str, str]:
    """Write the A-to-B evidence and an unsigned draft variation order."""
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    _xlsx(comparison, directory)
    _csv(comparison, directory)
    _pdf(comparison, directory)
    _variation_pdf(comparison, directory)
    (directory / "changes.json").write_text(json.dumps(comparison, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
    return {"changes_xlsx": "changes.xlsx", "changes_csv": "changes.csv", "changes_pdf": "changes.pdf", "changes_json": "changes.json", "variation_pdf": "variation-order.pdf"}
