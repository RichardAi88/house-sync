"""Price shared construction once and room finishes against the same rate library."""

from decimal import ROUND_HALF_UP, Decimal


def price_quantity(measured, rate, wastage, coats=1):
    net = Decimal(str(measured)) * coats
    gross = (net * (1 + Decimal(str(wastage)) / 100)).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)
    amount = (gross * Decimal(str(rate))).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return {"measured_quantity": measured, "net_quantity": float(net), "wastage_percent": wastage,
            "gross_quantity": float(gross), "quantity": float(gross), "amount": float(amount)}


ROOM_TRADES = (
    ("FLOOR_FINISH", "area_m2", "floor"), ("CEILING", "ceiling_area_m2", "ceiling"),
    ("SKIRTING", "skirting_length_m", "skirting"), ("INTERNAL_PLASTER", "internal_plaster_area_m2", None),
    ("PAINT", "paint_area_m2", "paint"),
)


SHARED_GROUP = "Shell and shared items"


def legacy_scope(code, room_name="Room 1"):
    if any(code == trade for trade, _, _ in ROOM_TRADES) and code != "PAINT":
        return "ROOM1", room_name
    return "SHELL", SHARED_GROUP


def build_room_boq(settings, quantities, rooms, partitions, base_rows):
    """Keep legacy line identities and rounding. A partitioned house has local and shared scope."""
    templates = {row["code"]: row for row in base_rows}
    rows = []
    groups = {}

    def add(row, group_id, group_name):
        rows.append({**row, "group_id": group_id, "group_name": group_name})
        group = groups.setdefault(group_id, {"id": group_id, "name": group_name, "subtotal": Decimal(0)})
        group["subtotal"] += Decimal(str(row["amount"]))

    def line(code, item, measured, description, element_ids):
        template = templates[code]
        priced = price_quantity(measured, template["rate"], template["wastage_percent"], template.get("paint_coats", 1))
        return {**template, **priced, "rate_code": code, "code": item, "description": description, "element_ids": element_ids}

    if not partitions:
        room = rooms[0]
        trades = {code: finish for code, _, finish in ROOM_TRADES}
        overrides = next((r.finishes for r in settings.rooms if r.id == room["id"]), None)
        for row in base_rows:
            code = row["code"]
            if code in {"EXTERNAL_RENDER", "INTERNAL_PLASTER", "PAINT"} and not row["measured_quantity"]:
                continue
            finish = getattr(overrides, trades.get(code) or "", None)
            if code == "PAINT" and settings.inner_leaf_finish == "bare_brick":
                finish = None
            if finish:
                row = {**row, "description": f"{row['description']}; {room['name']} interior: {finish}"}
            group = legacy_scope(code, room["name"])
            if code == "PAINT" and settings.exterior_finish.kind == "facebrick":
                group = (room["id"], room["name"])
            add({**row, "rate_code": code}, *group)
    else:
        for code in ("FLOOR", "WALL", "ROOF", "DOOR", "WINDOW", "EXTERNAL_RENDER", "PAINT"):
            row = templates[code]
            if code in {"EXTERNAL_RENDER", "PAINT"} and not quantities["external_render_area_m2"]:
                continue
            if code == "FLOOR":
                row = {**row, "description": "150 mm floor slab across the clear shell footprint"}
            elif code == "WALL":
                row = line(code, code, quantities["shell_wall_volume_m3"], row["description"], row["element_ids"])
            elif code == "PAINT":
                area = quantities["external_render_area_m2"]
                desc = f"Exterior wall paint - {settings.exterior_finish.name} ({settings.exterior_finish.hex}); measured net area {area:g} m2 x {settings.paint_coats} coats"
                row = line(code, code, area, desc, row["element_ids"])
            add({**row, "rate_code": code}, "SHELL", SHARED_GROUP)
        for wall in partitions:
            wall_id = wall["id"]
            description = f"Internal partition {wall_id} - {wall['wall_type']}, {wall['thickness_mm']:g} mm; net of openings and junctions"
            add(line("WALL", f"{wall_id}_WALL", wall["volume_m3"], description, [wall_id]), "SHELL", SHARED_GROUP)
        for room in rooms:
            rid = room["id"]
            hosts = room["wall_element_ids"]
            for code, key, finish in ROOM_TRADES:
                area = room[key]
                if code in {"INTERNAL_PLASTER", "PAINT"} and not area:
                    continue
                description = templates[code]["description"]
                if code == "PAINT":
                    description = f"Interior wall paint; measured net area {area:g} m2 x {settings.paint_coats} coats"
                if finish:
                    description += f" - {room['finishes'][finish]}"
                if code == "FLOOR_FINISH":
                    elements = [rid, "S0"]
                elif code == "CEILING":
                    elements = [rid, "R0"]
                else:
                    openings = room["door_ids"] if code == "SKIRTING" else room["opening_ids"]
                    elements = [rid, *hosts, *openings]
                add(line(code, f"{rid}_{code}", area, description, elements), rid, room["name"])
    return rows, [{**group, "subtotal": float(group["subtotal"])} for group in groups.values()]
