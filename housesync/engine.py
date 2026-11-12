"""Build a small house with homespec and derive quantities from its final IR.

The browser supplies validated data, never Python source. The existing
homespec compiler owns geometry and its IFC, plan, and schedule exports.
"""

from __future__ import annotations

import hashlib
import json
import threading
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

from homespec import Assembly, Door, Grid, House, Layer, Level, Material, Render, Roof, Slab, Space, Wall, Window
from homespec.derived import OpeningGeometry, SpaceGeometry, WallGeometry
from homespec.export import export_ifc, export_plan, export_schedules
from homespec.geometry import read_obj
from homespec.ir import IRDocument

from .schema import HouseSettings

ENGINE_VERSION = "house-sync-1"
SLAB_THICKNESS_MM = 150.0
ROOF_THICKNESS_MM = 200.0
ROOF_OVERHANG_MM = 300.0
_BUILD_LOCK = threading.RLock()

MEASUREMENT_RULE = (
    "Clear internal floor area; exact final wall volume after all modelled opening cuts. "
    "Walls form a closed, uniform-thickness rectangular shell with butt-jointed corner returns counted once. "
    "Equivalent net wall area is wall volume divided by thickness. Painting includes both internal and external "
    "net vertical wall faces, with every modelled door/window opening deducted on both faces; for this shell "
    "it equals twice the equivalent net wall area. Reveals, sills, soffits, floor/roof paint, waste and coat "
    "multipliers are excluded. Roof area is the flat roof's compiled top surface including its 300 mm overhang. "
    "Door/window quantities count parent products only, excluding their emitted frame, leaf and glazing parts. "
    "All rates are illustrative allowances, with a shared allowance per door or window; taxes, preliminaries "
    "and other unmodelled construction are excluded."
)


def _money(value: Decimal) -> float:
    return float(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _revision(settings: HouseSettings) -> str:
    package = Path(__file__).resolve().parent
    repository = package.parent
    sources = {
        path.relative_to(repository).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for directory in (package, repository / "homespec")
        for path in sorted(directory.rglob("*"))
        if path.is_file() and "__pycache__" not in path.parts
        and (path.suffix == ".py" or "static" in path.relative_to(repository).parts)
    }
    for name in ("pyproject.toml", "uv.lock"):
        path = repository / name
        if path.is_file():
            sources[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    payload = {"engine": ENGINE_VERSION, "settings": settings.model_dump(mode="json"), "sources": sources}
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12]


def _house(settings: HouseSettings) -> House:
    length, width, height, thickness = (
        settings.length_mm, settings.width_mm, settings.height_mm, settings.wall_thickness_mm
    )
    colour = tuple(int(settings.paint_hex[index:index + 2], 16) / 255 for index in (1, 3, 5))
    with House(settings.project_name) as house:
        ground = Level("L0", elevation=0, height=height)
        Grid("setting_out", x={"A": 0, "B": length}, y={"1": 0, "2": width})
        paint = Material(
            "wall_paint", product=f"Wall paint - {settings.paint_colour} ({settings.paint_hex})",
            finish=f"{settings.paint_colour} {settings.paint_hex}", supplier="To be selected",
            notes="Interior/exterior product suitability and coating system to be specified for the actual project.",
            render=Render(color=colour, rough=0.8),
        )
        Material("masonry", product="Illustrative uniform masonry wall", render=Render(color=(0.7, 0.7, 0.68)))
        floor = Material("floor_concrete", product="150 mm demonstration floor slab", render=Render(color=(0.72, 0.72, 0.70)))
        roof_material = Material("roof_concrete", product="200 mm flat demonstration roof", render=Render(color=(0.38, 0.43, 0.48)))
        frame = Material("steel_black", product="Opening frame allowance", render=Render(color=(0.13, 0.16, 0.19)))
        glass = Material("glass_double", product="Glazed window allowance", render=Render(color=(0.58, 0.82, 0.91), transmission=0.45))
        leaf = Material("door_leaf", product="Solid door allowance", render=Render(color=(0.48, 0.30, 0.17)))
        assembly = Assembly(
            "external_wall", layers=[Layer(material="masonry", thickness=thickness)],
            finish_in=paint, finish_out=paint,
        )
        # South/north walls own the four corner returns. East/west walls butt
        # against them, so wall volumes neither overlap nor leave corner gaps.
        south = Wall("W1", (-thickness, 0), (length + thickness, 0), assembly=assembly, level=ground)
        east = Wall("W2", (length, 0), (length, width), assembly=assembly, level=ground)
        north = Wall("W3", (length + thickness, width), (-thickness, width), assembly=assembly, level=ground)
        west = Wall("W4", (0, width), (0, 0), assembly=assembly, level=ground)
        Door("D1", host=south, width=1000, height=2100, at="center", frame=frame, leaf=leaf, glazing=glass)
        Window("N1", host=north, width=1500, height=1200, sill=900, at="center", frame=frame, glazing=glass)
        Window("N2", host=east, width=1500, height=1200, sill=900, at="center", frame=frame, glazing=glass)
        room_outline = [(0, 0), (length, 0), (length, width), (0, width)]
        Slab("S0", outline=room_outline, thickness=SLAB_THICKNESS_MM, level=ground, material=floor)
        Roof(
            "R0", outline=[(-thickness, -thickness), (length + thickness, -thickness),
                           (length + thickness, width + thickness), (-thickness, width + thickness)],
            shape="flat", overhang=ROOF_OVERHANG_MM, thickness=ROOF_THICKNESS_MM,
            eave=height + ROOF_THICKNESS_MM, level=ground, material=roof_material,
        )
        Space("ROOM1", outline=room_outline, use="demonstration_room", level=ground, bounded_by=[south, east, north, west])
    return house


def _quantities(ir: IRDocument) -> dict[str, float | int]:
    walls = ir.of_kind("wall")
    wall_volume = sum(wall.geometry.volume_mm3 for wall in walls if wall.geometry)
    equivalent_wall_area = sum(
        wall.geometry.volume_mm3 / wall.derived_as(WallGeometry).thickness
        for wall in walls if wall.geometry
    )
    return {
        "floor_area_m2": round(ir.entity("ROOM1").derived_as(SpaceGeometry).area_mm2 / 1e6, 6),
        "wall_net_area_m2": round(equivalent_wall_area / 1e6, 6),
        "paint_area_m2": round(2 * equivalent_wall_area / 1e6, 6),
        "wall_volume_m3": round(wall_volume / 1e9, 6),
        "roof_area_m2": round(ir.entity("R0").derived["surface_area_mm2"] / 1e6, 6),
        "door_count": len(ir.of_kind("door")),
        "window_count": len(ir.of_kind("window")),
    }


def _boq(settings: HouseSettings, quantities: dict[str, Any]) -> list[dict[str, Any]]:
    definitions = [
        ("FLOOR", "150 mm floor slab within clear internal room footprint", "floor_area_m2", "m2", "floor_m2", ["S0"]),
        ("WALL", f"{settings.wall_thickness_mm:g} mm uniform masonry walls, net of openings", "wall_volume_m3", "m3", "wall_m3", ["W1", "W2", "W3", "W4"]),
        ("ROOF", "200 mm flat roof including 300 mm overhang", "roof_area_m2", "m2", "roof_m2", ["R0"]),
        ("PAINT", f"Wall paint - {settings.paint_colour} ({settings.paint_hex}); internal and external net faces", "paint_area_m2", "m2", "paint_m2", ["W1", "W2", "W3", "W4"]),
        ("DOOR", "1000 x 2100 mm door; shared illustrative opening allowance", "door_count", "each", "opening_each", ["D1"]),
        ("WINDOW", "1500 x 1200 mm windows; shared illustrative opening allowance", "window_count", "each", "opening_each", ["N1", "N2"]),
    ]
    rows = []
    for code, description, quantity_key, unit, rate_key, element_ids in definitions:
        quantity = quantities[quantity_key]
        rate = getattr(settings.rates, rate_key)
        rows.append({
            "code": code, "description": description, "quantity": quantity, "unit": unit, "rate": rate,
            "amount": _money(Decimal(str(quantity)) * Decimal(str(rate))), "element_ids": element_ids,
        })
    return rows


def _mesh(ir: IRDocument) -> dict[str, Any]:
    objects = []
    for entity in ir.entities:
        if not entity.physical or entity.geometry is None:
            continue
        vertices, faces = read_obj(ir.path(entity.geometry.obj), scale_to=1.0)
        colour = "#A8ADB3"
        if entity.material and entity.material in ir.materials:
            rgb = ir.materials[entity.material].render.get("color")
            if rgb:
                colour = "#" + "".join(f"{round(channel * 255):02X}" for channel in rgb)
        objects.append({"id": entity.id, "kind": entity.kind, "vertices": vertices, "faces": faces, "colour": colour})
    return {"units": "m", "up_axis": "Z", "objects": objects}


def _drawing(ir: IRDocument) -> dict[str, Any]:
    outline = ir.entity("ROOM1").params["outline"]
    length = max(point[0] for point in outline) - min(point[0] for point in outline)
    width = max(point[1] for point in outline) - min(point[1] for point in outline)
    wall = ir.entity("W1").derived_as(WallGeometry)
    roof = ir.entity("R0")
    openings = []
    for entity in ir.tagged("opening"):
        geometry = entity.derived_as(OpeningGeometry)
        openings.append({
            "id": entity.id, "host": geometry.host,
            "start_mm": geometry.from_start - (wall.thickness if geometry.host in {"W1", "W3"} else 0),
            "width_mm": geometry.width, "height_mm": geometry.height, "sill_mm": geometry.sill,
        })
    return {
        "length_mm": length, "width_mm": width, "height_mm": wall.height, "wall_thickness_mm": wall.thickness,
        "slab_thickness_mm": ir.entity("S0").params["thickness"],
        "roof_thickness_mm": roof.params["thickness"], "roof_overhang_mm": roof.params["overhang"],
        "roof_top_mm": roof.geometry.bbox.max[2] if roof.geometry else wall.height + roof.params["thickness"], "openings": openings,
        "reference": "Clear internal room: W1 south eastwards, W2 east northwards, W3 north westwards, W4 west southwards. Opening starts use these internal reference lines.",
    }


def build_house(settings: HouseSettings, output_dir: Path) -> dict[str, Any]:
    """Compile a validated house and write exports into an isolated directory.

    The caller owns generation publication and document export. A lock
    serialises native CAD work because it is also used by a local web server.
    No Blender executable or downloaded texture assets are needed.
    """
    settings = HouseSettings.model_validate(settings.model_dump(mode="json"))
    output_dir = Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    with _BUILD_LOCK:
        house = _house(settings)
        compiled = house.compile()
        ir = compiled.write(str(output_dir))
        files: dict[str, Any] = {"ir": "ir.json", "settings": "settings.json"}
        files["ifc"] = str(Path(export_ifc(ir, str(output_dir / "house.ifc"))).relative_to(output_dir)).replace("\\", "/")
        files["upstream_plans"] = [
            str(Path(path).relative_to(output_dir)).replace("\\", "/")
            for path in export_plan(ir, "L0", str(output_dir / "upstream_drawings"))
        ]
        files["upstream_schedules"] = [
            str(Path(path).relative_to(output_dir)).replace("\\", "/")
            for path in export_schedules(ir, str(output_dir / "upstream_schedules"))
        ]
        quantities = _quantities(ir)
        boq = _boq(settings, quantities)
        result = {
            "settings": settings.model_dump(mode="json"), "revision": _revision(settings),
            "quantities": quantities, "boq": boq,
            "totals": {"currency": settings.currency, "subtotal": _money(sum((Decimal(str(row["amount"])) for row in boq), Decimal(0)))},
            "files": files, "mesh": _mesh(ir), "drawing": _drawing(ir),
            "measurement_rule": MEASUREMENT_RULE,
            "checks": {
                "status": "not_run", "note": "Homespec's building guideline, IDS and regulatory checks have not been run for this demonstration.",
                "wall_clashes": [{"a": clash.a, "b": clash.b, "volume_mm3": clash.volume_mm3}
                                 for clash in ir.clashes if clash.a in {"W1", "W2", "W3", "W4"} and clash.b in {"W1", "W2", "W3", "W4"}],
            },
        }
        (output_dir / "settings.json").write_text(json.dumps(result["settings"], indent=2, ensure_ascii=False), encoding="utf-8")
        return result
