"""Pure AUD variation quote arithmetic and an explicit client-safe projection.

Saved BOQ line amounts are the internal cost authority. Positive movements and
absolute credits are aggregated separately, including allowance value deltas.
The combined overhead/margin markup rounds HALF_UP once on each aggregate
addition/credit base; it is never compounded or applied to the fixed admin fee.
GST rounds HALF_UP on the signed ex-GST subtotal. No current rates or clock are
read here, and no saved snapshot or comparison is modified.

Admin policy: charge B's fixed fee only when a priced BOQ movement or stable-ID
allowance has a nonzero value movement after equivalent room regrouping. This includes credit-only and offsetting
addition/credit variations. Identical, description-only and allowance-kind-only
changes attract no admin fee. An explicitly entered fee may reduce a credit or
turn a small credit into an amount payable; no fee is inferred automatically.
"""

from __future__ import annotations

import re
from copy import deepcopy
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

from .schema import AllowanceSettings, BuilderPricing, OpeningSettings, default_openings

CENT = Decimal("0.01")
ZERO = Decimal(0)
MARKUP_LABEL = "Combined overhead and margin (markup on cost)"
ADMIN_POLICY = (
    "The fixed administration fee applies only when an item or allowance value changes, including credit-only "
    "and offsetting additions/credits. It is excluded from markup. No fee applies to identical or editorial changes "
    "or equivalent room regrouping alone."
)
ROOM_FINISH_FIELDS = {"FLOOR_FINISH": "floor", "CEILING": "ceiling", "SKIRTING": "skirting", "PAINT": "paint"}
ROOM_TRADES = {*ROOM_FINISH_FIELDS, "INTERNAL_PLASTER"}
REGROUPING_POLICY = (
    "Only unmatched room-finish rows across a room topology change are reconciled against the same saved trade, "
    "commercial trade and unit. Descriptive finish labels do not create a purchase. "
    "A homogeneous trade may change its effective rate without becoming a new purchase. "
    "Heterogeneous trade bases remain separated by effective rate. The shared paint row also participates when its scope changes "
    "between combined wall faces and exterior-only faces. Continuing room lines retain their individual signed "
    "movements. Saved BOQ amounts are never recalculated."
)


class QuoteUnavailableError(ValueError):
    """The selected saved revisions cannot produce this AUD-only client quote."""


def _decimal(value: Any, label: str, *, signed: bool = False) -> Decimal:
    if value is None or isinstance(value, bool):
        raise ValueError(f"{label} must be a finite monetary amount in whole cents")
    try:
        amount = Decimal(str(value))
        if not amount.is_finite() or (not signed and amount < 0) or amount != amount.quantize(CENT):
            raise ValueError(f"{label} must be a finite monetary amount in whole cents")
        return amount
    except (InvalidOperation, TypeError, OverflowError) as exc:
        raise ValueError(f"{label} must be a finite monetary amount in whole cents") from exc


def _round(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def _number(value: Decimal) -> float:
    return float(value) if value else 0.0


def _allowance_map(rows: list) -> dict[str, dict]:
    if not isinstance(rows, list) or len(rows) > 100:
        raise ValueError("Allowances must be a list of at most 100 records")
    result = {}
    for source in rows:
        row = AllowanceSettings.model_validate(source).model_dump(mode="json")
        if row["id"] in result:
            raise ValueError(f"Duplicate allowance ID {row['id']}")
        result[row["id"]] = row
    return result


def diff_allowances(before: list, after: list) -> list[dict]:
    """Compare stable allowance IDs without requiring AUD or quote generation.

    Full old/new records make classification and editorial changes auditable.
    Changing PC/PS kind at the same value has a zero priced movement. This
    helper is also safe for the ordinary same-currency comparison workflow.
    """
    old_rows, new_rows = _allowance_map(before), _allowance_map(after)
    changes = []
    for identity in sorted(old_rows.keys() | new_rows.keys(), key=lambda value: int(value[2:])):
        old, new = old_rows.get(identity), new_rows.get(identity)
        if old == new:
            continue
        current = new or old
        old_amount = _decimal(old["amount"], "Before allowance") if old else ZERO
        new_amount = _decimal(new["amount"], "After allowance") if new else ZERO
        changes.append({"id": identity, "kind": current["kind"], "description": current["description"], "note": current["note"],
                        "before": deepcopy(old), "after": deepcopy(new), "before_amount": _number(old_amount),
                        "after_amount": _number(new_amount), "cost_delta": _number(new_amount - old_amount),
                        "status": "added" if old is None else "removed" if new is None else "modified"})
    return changes


def _snapshot_rows(snapshot: dict, label: str) -> tuple[dict[str, dict], Decimal]:
    if not isinstance(snapshot, dict) or not isinstance(snapshot.get("settings"), dict):
        raise ValueError(f"{label} is missing saved settings")
    if not isinstance(snapshot.get("revision"), str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,95}", snapshot["revision"]):
        raise ValueError(f"{label} is missing a safe saved revision ID")
    totals = snapshot.get("totals", {})
    if not isinstance(totals, dict) or totals.get("currency") != snapshot["settings"].get("currency"):
        raise ValueError(f"{label} currency is missing or inconsistent")
    if totals["currency"] != "AUD":
        raise QuoteUnavailableError("Client variation quotes are available only for saved AUD revisions. No currency conversion is applied.")
    if not isinstance(snapshot.get("boq"), list):
        raise ValueError(f"{label} is missing its saved BOQ")
    rows = {}
    for row in snapshot["boq"]:
        if not isinstance(row, dict) or not isinstance(row.get("code"), str) or not row["code"]:
            raise ValueError(f"{label} has an invalid BOQ code")
        if row["code"] in rows:
            raise ValueError(f"{label} has duplicate BOQ code {row['code']}")
        _decimal(row.get("amount"), f"{label} {row['code']} amount")
        rows[row["code"]] = row
    subtotal = _decimal(totals.get("subtotal"), f"{label} subtotal")
    if sum((_decimal(row["amount"], "BOQ amount") for row in rows.values()), ZERO) != subtotal:
        raise ValueError(f"{label} saved BOQ does not reconcile to its subtotal")
    return rows, subtotal


def _boq_changes(before: dict, after: dict, comparison: dict) -> tuple[list[dict], Decimal]:
    old_rows, old_subtotal = _snapshot_rows(before, "Revision A")
    new_rows, new_subtotal = _snapshot_rows(after, "Revision B")
    if not isinstance(comparison, dict) or comparison.get("before_revision") != before["revision"] or comparison.get("after_revision") != after["revision"]:
        raise ValueError("The comparison does not match the selected saved revisions")
    if comparison.get("currency") != "AUD":
        raise QuoteUnavailableError("Client variation quotes require an AUD comparison")
    source = comparison.get("items")
    if not isinstance(source, list):
        raise ValueError("The saved comparison is missing its BOQ changes")
    indexed = {}
    for item in source:
        if not isinstance(item, dict) or not isinstance(item.get("code"), str) or item["code"] in indexed:
            raise ValueError("The comparison contains invalid or duplicate item codes")
        indexed[item["code"]] = item
    rows = []
    for code in dict.fromkeys([*old_rows, *new_rows, *indexed]):
        old, new = old_rows.get(code), new_rows.get(code)
        old_amount = _decimal(old["amount"], "Before BOQ amount") if old else ZERO
        new_amount = _decimal(new["amount"], "After BOQ amount") if new else ZERO
        delta = new_amount - old_amount
        item = indexed.get(code)
        if (delta and item is None) or (item is not None and _decimal(item.get("cost_delta"), "Compared item change", signed=True) != delta):
            raise ValueError(f"Comparison amount for {code} does not match the saved BOQ")
        if old is None and new is None:
            raise ValueError(f"Comparison item {code} is absent from both saved BOQs")
        if item is None:
            continue
        rows.append({**deepcopy(item), "code": code, "before": deepcopy(old), "after": deepcopy(new),
                     "before_amount": _number(old_amount), "after_amount": _number(new_amount), "cost_delta": _number(delta),
                     "classification": "addition" if delta > 0 else "omission" if delta < 0 else "editorial"})
    net = new_subtotal - old_subtotal
    totals = comparison.get("totals", {})
    if not isinstance(totals, dict) or _decimal(totals.get("net_change"), "Comparison net change", signed=True) != net:
        raise ValueError("Comparison total does not match the saved BOQ subtotals")
    return rows, net


def _room_rows(snapshot: dict) -> dict[str, dict]:
    return {row["id"]: row for row in snapshot.get("rooms", []) if isinstance(row, dict) and isinstance(row.get("id"), str)}


def _room_ids(snapshot: dict) -> set[str]:
    return set(_room_rows(snapshot)) or {"ROOM1"}


def _rate_code(row: dict) -> str:
    return row.get("rate_code", row["code"])


def _paint_finish(snapshot: dict) -> str:
    settings = snapshot["settings"]
    return f"{settings.get('paint_colour', 'Light blue')} ({settings.get('paint_hex', '#ADD8E6')})"


def _finish(snapshot: dict, row: dict, *, interior: bool = False) -> str | None:
    trade = _rate_code(row)
    field = ROOM_FINISH_FIELDS.get(trade)
    if trade == "INTERNAL_PLASTER":
        return "Internal plaster/render"
    if field is None:
        return None
    defaults = {"floor": "Floor finish allowance", "ceiling": "Ceiling allowance",
                "skirting": "Skirting allowance", "paint": _paint_finish(snapshot)}
    group = row.get("group_id")
    if interior or not snapshot["settings"].get("partitions"):
        group = "ROOM1"
    if trade == "PAINT" and group in {None, "SHELL"}:
        return defaults[field]
    room = _room_rows(snapshot).get(group)
    if room:
        value = room.get("finishes", {}).get(field)
        return value if isinstance(value, str) and value else defaults[field]
    metadata = next((room for room in snapshot["settings"].get("rooms", []) if room.get("id") == group), {})
    selected = metadata.get("finishes", {}).get(field)
    if selected:
        return selected if isinstance(selected, str) else None
    return defaults[field] if group in {None, "ROOM1"} else None


def _pricing_basis(row: dict, *, homogeneous: bool) -> tuple | None:
    """Evidence labels do not create scope; heterogeneous effective prices do."""
    if not isinstance(row.get("unit"), str) or isinstance(row.get("rate"), bool):
        return None
    try:
        rate = Decimal(str(row["rate"]))
        if not rate.is_finite() or rate < 0:
            return None
    except (InvalidOperation, KeyError, ValueError):
        return None
    return (_rate_code(row), row["unit"], "homogeneous" if homogeneous else str(rate.normalize()))


def _homogeneous_trades(before: dict, after: dict) -> set[str]:
    result = set()
    for trade in ROOM_TRADES:
        bases = []
        for snapshot in (before, after):
            values = {_pricing_basis(row, homogeneous=False) for row in snapshot["boq"] if _rate_code(row) == trade}
            bases.append(values)
        if all(len(values) == 1 and None not in values for values in bases):
            old, new = (next(iter(values)) for values in bases)
            if old[1] == new[1]:
                result.add(trade)
    return result


def _combined_paint(snapshot: dict, row: dict | None) -> bool:
    return bool(row and row["code"] == "PAINT" and not snapshot["settings"].get("partitions"))


def _saved_components(snapshot: dict, row: dict, *, homogeneous: bool) -> list[tuple[str, Decimal]] | None:
    """Allocate a legacy combined paint amount only when its finishes differ.

    This uses saved compiled face areas as weights against its saved amount;
    the residual allocation retains every cent. No unit rate is reapplied.
    """
    amount = _decimal(row["amount"], "Saved BOQ amount")
    selected = _finish(snapshot, row)
    if selected is None:
        return None
    if homogeneous:
        if _combined_paint(snapshot, row):
            inside = _finish(snapshot, row, interior=True)
            outside = _paint_finish(snapshot)
            selected = inside if inside == outside else f"Interior: {inside}; exterior: {outside}"
        return [(selected, amount)]
    if not _combined_paint(snapshot, row):
        return [(selected, amount)]
    interior = _finish(snapshot, row, interior=True)
    exterior = _paint_finish(snapshot)
    if interior == exterior:
        return [(exterior, amount)]
    try:
        quantities = snapshot["quantities"]
        inside = Decimal(str(quantities["internal_plaster_area_m2"]))
        outside = Decimal(str(quantities["external_render_area_m2"]))
        if not inside.is_finite() or not outside.is_finite() or min(inside, outside) < 0 or inside + outside <= 0:
            return None
    except (KeyError, InvalidOperation, TypeError):
        return None
    interior_amount = _round(amount * inside / (inside + outside))
    return [(interior, interior_amount), (exterior, amount - interior_amount)]


def _commercial_boq_movements(before: dict, after: dict, changes: list[dict]) -> tuple[list[dict], list[dict]]:
    """Remove presentation-only re-purchases while retaining stable line policy."""
    def original(change: dict) -> dict:
        return {"kind": "boq", "code": change["code"], "rate_code": _rate_code(change["after"] or change["before"]),
                "before_amount": change["before_amount"], "after_amount": change["after_amount"],
                "cost_delta": change["cost_delta"], "source_codes": [change["code"]], "regrouped": False}

    if _room_ids(before) == _room_ids(after):
        return [original(change) for change in changes], []
    retained = []
    buckets: dict[tuple, dict] = {}
    homogeneous = _homogeneous_trades(before, after)
    for change in changes:
        old, new = change["before"], change["after"]
        row = new or old
        unmatched = old is None or new is None
        paint_transition = (old is not None and new is not None and _rate_code(row) == "PAINT"
                            and _combined_paint(before, old) != _combined_paint(after, new))
        eligible = _rate_code(row) in ROOM_TRADES and (unmatched or paint_transition)
        if paint_transition and (_pricing_basis(old, homogeneous=_rate_code(old) in homogeneous)
                                 != _pricing_basis(new, homogeneous=_rate_code(new) in homogeneous)):
            eligible = False
        components = []
        if eligible:
            for side, snapshot, item in (("before", before, old), ("after", after, new)):
                if item is None:
                    continue
                basis = _pricing_basis(item, homogeneous=_rate_code(item) in homogeneous)
                parts = _saved_components(snapshot, item, homogeneous=_rate_code(item) in homogeneous)
                if basis is None or parts is None:
                    eligible = False
                    break
                components.extend((side, item, basis, finish, amount) for finish, amount in parts)
        if not eligible:
            retained.append(original(change))
            continue
        for side, item, basis, finish, amount in components:
            key = (*basis, None if basis[0] in homogeneous else finish)
            bucket = buckets.setdefault(key, {"rate_code": basis[0], "finish": finish,
                                               "finishes": [], "unit": basis[1], "basis_mode": basis[2], "source_lines": []})
            if finish not in bucket["finishes"]:
                bucket["finishes"].append(finish)
            if len(bucket["finishes"]) > 1:
                bucket["finish"] = "Descriptive finishes recorded per saved line; same commercial trade"
            bucket["source_lines"].append({"side": side, "code": item["code"], "group_id": item.get("group_id"),
                                            "group_name": item.get("group_name"), "amount": _number(amount),
                                            "finish": finish,
                                            "rate": item["rate"], "rate_origin": item.get("rate_origin"),
                                            "rate_source": item.get("rate_source"), "rate_effective_date": item.get("rate_effective_date")})
    reconciliations = []
    for bucket in buckets.values():
        old = [row for row in bucket["source_lines"] if row["side"] == "before"]
        new = [row for row in bucket["source_lines"] if row["side"] == "after"]
        old_amount = sum((_decimal(row["amount"], "Saved component") for row in old), ZERO)
        new_amount = sum((_decimal(row["amount"], "Saved component") for row in new), ZERO)
        codes = sorted({row["code"] for row in bucket["source_lines"]})
        regrouped = bool(old and new)
        movement = {"kind": "boq", "rate_code": bucket["rate_code"], "finish": bucket["finish"],
                    "before_amount": _number(old_amount), "after_amount": _number(new_amount),
                    "cost_delta": _number(new_amount - old_amount), "source_codes": codes, "regrouped": regrouped}
        retained.append(movement)
        if regrouped:
            reconciliations.append({**deepcopy(bucket), "before_amount": movement["before_amount"],
                                    "after_amount": movement["after_amount"], "cost_delta": movement["cost_delta"],
                                    "source_codes": codes,
                                    "reason": "Room topology regrouped equivalent saved commercial trade amounts; price the signed residual once."})
    if sum((_decimal(row["cost_delta"], "Pricing movement", signed=True) for row in retained), ZERO) != sum(
        (_decimal(row["cost_delta"], "Saved BOQ change", signed=True) for row in changes), ZERO
    ):
        raise ValueError("Room regrouping movements do not reconcile to the saved BOQ cost difference")
    return retained, reconciliations


def _public_allowances(changes: list[dict]) -> dict[str, list[dict]]:
    groups: dict[str, list[dict]] = {"prime_cost": [], "provisional_sum": []}

    def add(kind: str, row: dict, before: float, after: float, clarification: str = "") -> None:
        groups[kind].append({"id": row["id"], "description": row["description"], "before": before, "after": after,
                             "movement": _number(_decimal(after, "Allowance after") - _decimal(before, "Allowance before")),
                             "notes": " ".join(text for text in (row["note"], clarification) if text)})

    for change in changes:
        old, new = change["before"], change["after"]
        if old and new and old["kind"] != new["kind"]:
            transfer = "Reclassified between allowance categories; only the net change in the total allowance value is priced."
            add(old["kind"], old, old["amount"], 0.0, transfer)
            add(new["kind"], new, 0.0, new["amount"], transfer)
        else:
            row = new or old
            add(row["kind"], row, change["before_amount"], change["after_amount"])
    return {"pc_allowances": groups["prime_cost"], "ps_allowances": groups["provisional_sum"]}


def _fallback_scope(before: dict, after: dict, boq_rows: list[dict], allowance_rows: list[dict]) -> list[str]:
    """Describe selected construction changes without copying internal narratives."""
    defaults = {"length_mm": 10000, "width_mm": 8000, "height_mm": 3000, "wall_thickness_mm": 200,
                "paint_colour": "Light blue", "paint_hex": "#ADD8E6", "paint_coats": 2}
    old, new = ({**defaults, **snapshot["settings"]} for snapshot in (before, after))
    scope = []
    for key, label in (("length_mm", "internal length"), ("width_mm", "internal width"),
                       ("height_mm", "wall height"), ("wall_thickness_mm", "wall thickness")):
        a, b = old.get(key), new.get(key)
        if a != b and isinstance(a, (int, float)) and isinstance(b, (int, float)):
            scope.append(f"Change the house {label} from {a / 1000:g} m to {b / 1000:g} m.")
    if old.get("paint_colour") != new.get("paint_colour") or old.get("paint_hex") != new.get("paint_hex"):
        scope.append(f"Change the wall paint finish to {new.get('paint_colour') or 'the selected finish'}.")
    if old.get("paint_coats", 2) != new.get("paint_coats", 2):
        scope.append(f"Apply {new.get('paint_coats', 2)} paint coats to the selected wall finishes.")
    old_partitions = {row["id"]: row for row in old.get("partitions", [])}
    new_partitions = {row["id"]: row for row in new.get("partitions", [])}
    for identity in sorted(old_partitions.keys() | new_partitions.keys()):
        a, b = old_partitions.get(identity), new_partitions.get(identity)
        if a == b:
            continue
        if a is None or b is None:
            scope.append("Add an internal partition." if a is None else "Remove an internal partition.")
        elif a.get("start_mm") != b.get("start_mm") or a.get("end_mm") != b.get("end_mm"):
            scope.append("Reposition an internal partition to revise the room layout.")
        else:
            scope.append("Revise an internal partition's dimensions or construction.")
    old_rooms, new_rooms = _room_rows(before), _room_rows(after)
    if (set(old_rooms) or {"ROOM1"}) != (set(new_rooms) or {"ROOM1"}):
        names = [str(room.get("name") or "the selected room") for room in new_rooms.values()]
        scope.append("Update the room layout" + (" to provide " + ", ".join(names) if names else "") + ".")
    authored = {row["id"]: row for row in new.get("rooms", [])}
    for identity in new_rooms.keys() - old_rooms.keys():
        name = str(new_rooms[identity].get("name") or "the selected room")
        for field, value in authored.get(identity, {}).get("finishes", {}).items():
            if field in ROOM_FINISH_FIELDS.values() and isinstance(value, str) and value:
                scope.append(f"Specify {name} {field} finish as {value}.")
    for identity in old_rooms.keys() & new_rooms.keys():
        a, b = old_rooms[identity], new_rooms[identity]
        name = str(b.get("name") or a.get("name") or "the selected room")
        if a.get("name") != b.get("name"):
            scope.append(f"Rename {a.get('name') or 'the selected room'} to {name}.")
        for field in ("floor", "ceiling", "skirting", "paint"):
            finish_a, finish_b = a.get("finishes", {}).get(field), b.get("finishes", {}).get(field)
            if finish_a != finish_b and isinstance(finish_b, str) and finish_b:
                scope.append(f"Change {name} {field} finish to {finish_b}.")
    def opening_rows(settings: dict) -> dict:
        source = settings.get("openings", [row.model_dump(mode="json") for row in default_openings()])
        return {row.id: row.model_dump(mode="json") for row in (OpeningSettings.model_validate(value) for value in source)}

    old_openings, new_openings = opening_rows(old), opening_rows(new)
    for identity in sorted(old_openings.keys() | new_openings.keys(), key=str):
        a, b = old_openings.get(identity), new_openings.get(identity)
        if a == b:
            continue
        current = b or a
        kind = "door" if current.get("kind") == "door" else "window"
        scope.append(f"{'Add' if a is None else 'Remove' if b is None else 'Revise the position or size of'} {kind} {identity}.")
    if allowance_rows:
        scope.append("Update the separately identified prime-cost and provisional-sum allowances shown below.")
    if not scope:
        scope.append("Adjust the agreed pricing for the selected variation scope." if any(row["cost_delta"] for row in boq_rows)
                     else "No change to the selected construction quantities or allowance values.")
    return list(dict.fromkeys(scope))


def _known_illustrative_basis(boq_rows: list[dict], before: dict, after: dict) -> bool:
    """Inspect actual applied source labels without exposing their contents."""
    for change in boq_rows:
        for key, snapshot in (("before", before), ("after", after)):
            row = change[key]
            if not row:
                continue
            source = row.get("rate_source", snapshot["settings"].get("rate_source"))
            if source is None and row.get("rate_origin") == "library":
                library = snapshot.get("rate_library")
                library_rows = library.get("rows", {}) if isinstance(library, dict) else {}
                saved_row = library_rows.get(row.get("rate_code", row["code"]), {}) if isinstance(library_rows, dict) else {}
                source = saved_row.get("source") if isinstance(saved_row, dict) else None
            if isinstance(source, str) and re.search(r"\b(?:illustrative|demonstration)\b", source, flags=re.IGNORECASE):
                return True
    return False


def build_variation_quote(before: dict, after: dict, comparison: dict, issued_on: date | str) -> dict[str, dict]:
    """Price saved A-to-B movements and return separate public/internal payloads.

    Public deliberately contains only approved aggregate commercial figures,
    client-authored scope and allowance summaries. Saved item descriptions,
    rates, rate sources, library notes and comparison prose stay internal.
    """
    if isinstance(issued_on, datetime):
        raise ValueError("issued_on must be a calendar date, not a timestamp")
    if isinstance(issued_on, str):
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", issued_on):
            raise ValueError("issued_on must use YYYY-MM-DD")
        issued_on = date.fromisoformat(issued_on)
    if not isinstance(issued_on, date):
        raise ValueError("issued_on must be an explicit calendar date")
    boq_rows, boq_net = _boq_changes(before, after, comparison)
    pricing = BuilderPricing.model_validate(after["settings"].get("builder_pricing", {}))
    allowances = diff_allowances(before["settings"].get("allowances", []), after["settings"].get("allowances", []))
    pricing_movements, regrouping_reconciliations = _commercial_boq_movements(before, after, boq_rows)
    changes = [*pricing_movements, *allowances]
    additions = sum((_decimal(row["cost_delta"], "Change", signed=True) for row in changes if row["cost_delta"] > 0), ZERO)
    credits = -sum((_decimal(row["cost_delta"], "Change", signed=True) for row in changes if row["cost_delta"] < 0), ZERO)
    markup = Decimal(str(pricing.overhead_margin_percent)) / 100
    additions_markup = _round(additions * markup)
    credits_markup = _round(credits * markup) if pricing.credit_treatment == "including_margin" else ZERO
    additions_quote, credits_quote = additions + additions_markup, credits + credits_markup
    actionable = additions > 0 or credits > 0
    admin = _decimal(pricing.admin_fee_aud, "Administration fee") if actionable else ZERO
    subtotal = additions_quote - credits_quote + admin
    gst = _round(subtotal * Decimal("0.10"))
    totals = {"additions_at_cost": _number(additions), "credits_at_cost": _number(credits),
              "at_cost_delta": _number(additions - credits), "additions_markup": _number(additions_markup),
              "credits_markup": _number(credits_markup), "markup_amount": _number(additions_markup - credits_markup),
              "additions_quote": _number(additions_quote), "credits_quote": _number(credits_quote),
              "admin_fee": _number(admin), "subtotal_ex_gst": _number(subtotal), "gst_rate": 10,
              "gst_amount": _number(gst), "total_incl_gst": _number(subtotal + gst)}
    reference = f"VQ-{before['revision']}-{after['revision']}-{issued_on:%Y%m%d}"
    identity = {"quote_reference": reference, "before_revision": before["revision"], "after_revision": after["revision"],
                "currency": "AUD", "issued_on": issued_on.isoformat(),
                "valid_until": (issued_on + timedelta(days=pricing.validity_days)).isoformat(), "validity_days": pricing.validity_days}
    credit_policy = ("Credits are returned at the saved cost, without the added overhead and margin markup."
                     if pricing.credit_treatment == "at_cost" else "Credits include the same overhead and margin markup as additions.")
    notes = ["Draft variation quote, subject to review and written acceptance."]
    if _known_illustrative_basis(boq_rows, before, after):
        notes.append("This draft includes illustrative rates; confirm project pricing before acceptance.")
    if any(row["before"] and row["after"] and row["before"]["kind"] != row["after"]["kind"] for row in allowances):
        notes.append("Allowance category transfers are cost-neutral; only changes in allowance value are priced.")
    # This is a deliberate whitelist, not a filtered copy of an internal object.
    public = {**identity, "project_name": str(after["settings"].get("project_name", "House Sync project")),
              "builder_name": pricing.builder_name, "client_name": "", "status": "draft", "client_description": pricing.client_description,
              "scope": [pricing.client_description] if pricing.client_description else _fallback_scope(before, after, boq_rows, allowances),
              "credit_treatment": pricing.credit_treatment, "credit_policy": credit_policy, "markup_label": MARKUP_LABEL,
              "overhead_margin_percent": pricing.overhead_margin_percent, "admin_policy": ADMIN_POLICY,
              "totals": totals, **_public_allowances(allowances),
              "notes": notes}
    allowance_net = sum((_decimal(row["cost_delta"], "Allowance change", signed=True) for row in allowances), ZERO)
    internal = {**identity, "builder_pricing": pricing.model_dump(mode="json"), "boq_changes": boq_rows,
                "pricing_movements": pricing_movements, "regrouping_reconciliations": regrouping_reconciliations,
                "regrouping_policy": REGROUPING_POLICY,
                "allowance_changes": allowances, "summary": deepcopy(comparison.get("summary", [])),
                "totals": {**totals, "boq_cost_delta": _number(boq_net), "allowance_cost_delta": _number(allowance_net)},
                "requested_admin_fee_aud": pricing.admin_fee_aud, "admin_fee_applied": actionable and admin > 0,
                "has_priced_changes": actionable,
                "rounding_policy": "Aggregate additions and credits separately; round each markup half-up to cents. Apply fixed admin without markup; round signed 10% GST half-up to cents."}
    return {"public": public, "internal": internal}
