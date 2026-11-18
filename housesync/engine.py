"""Build a small house with homespec and derive quantities from its final IR.

The browser supplies validated data, never Python source. The existing
homespec compiler owns geometry and its IFC, plan, and schedule exports.
"""

from __future__ import annotations

import hashlib
import json
import math
import threading
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

from shapely.geometry import Polygon, box

from homespec import Assembly, Door, Grid, House, Layer, Level, Material, Render, Roof, Slab, Space, Wall, Window
from homespec.derived import OpeningGeometry, SpaceGeometry, WallGeometry
from homespec.export import export_ifc, export_plan, export_schedules
from homespec.geometry import planar_face_meshes, read_obj, read_step
from homespec.ir import IRDocument

from .rates import BOQ_CODES, RateLibrary, RateLibraryError, load_rate_library
from .schema import HouseSettings

ENGINE_VERSION = "house-sync-1"
SLAB_THICKNESS_MM = 150.0
ROOF_THICKNESS_MM = 200.0
ROOF_OVERHANG_MM = 300.0
_BUILD_LOCK = threading.RLock()

MEASUREMENT_RULE = (
    "Clear internal floor area; exact final wall volume after all modelled opening cuts. "
    "Walls form a closed, uniform-thickness rectangular shell with butt-jointed corner returns counted once. "
    "Floor finish and ceiling use the compiled clear internal room area. Skirting uses its internal perimeter "
    "less compiled door opening widths. Internal plaster/render and external render use final wall faces at "
    "the room and shell boundaries respectively, deducting opening voids and excluding buried corner faces. "
    "Painting includes their combined net vertical face area multiplied by the selected coat count. Measured "
    "net area remains visible separately from the coat-adjusted paint quantity. Reveals, sills, soffits and "
    "floor/roof paint are excluded. Each item's gross priced quantity is its net quantity times (1 + wastage % / 100), "
    "rounded to six decimals. Roof area is the flat roof's compiled top surface including its 300 mm overhang. "
    "Door/window quantities count parent products only, excluding their emitted frame, leaf and glazing parts. "
    "Default prices are illustrative AUD rates, not a tender estimate, and are not supplier quotes. Rates have an "
    "editable source and date, with a shared allowance per door or window; taxes, preliminaries "
    "and other unmodelled construction are excluded."
)


def _money(value: Decimal) -> float:
    return float(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _pricing_library(settings: HouseSettings, rate_library: RateLibrary | None = None) -> RateLibrary:
    library = rate_library if rate_library is not None else load_rate_library()
    if settings.currency != library.currency:
        missing = BOQ_CODES.keys() - settings.rate_overrides.keys()
        if missing:
            raise RateLibraryError(
                f"The rate library uses {library.currency}, but the design uses {settings.currency}. "
                "No currency conversion is applied. Supply explicit rates in the design currency for: "
                + ", ".join(sorted(missing))
            )
    return library


def _revision(settings: HouseSettings, rate_library: RateLibrary | None = None) -> str:
    library = _pricing_library(settings, rate_library)
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
    payload = {"engine": ENGINE_VERSION, "settings": settings.model_dump(mode="json"),
               "rate_library": library.snapshot(), "sources": sources}
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12]


revision_for = _revision


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
        hosts = {"W1": south, "W2": east, "W3": north, "W4": west}
        for opening in settings.openings:
            span = length if opening.host in {"W1", "W3"} else width
            start = (span - opening.width_mm) / 2 if opening.offset_mm is None else opening.offset_mm
            # The north/south compiler paths own corner returns. Browser inputs
            # measure from the inner room corner, so translate by one thickness.
            position = start + (thickness if opening.host in {"W1", "W3"} else 0)
            params = dict(host=hosts[opening.host], width=opening.width_mm, height=opening.height_mm,
                          sill=opening.sill_mm, at=position, frame=frame, glazing=glass)
            if opening.kind == "door":
                Door(opening.id, **params, leaf=leaf)
            else:
                Window(opening.id, **params)
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


def _net_wall_faces(ir: IRDocument) -> tuple[float, float]:
    """Measure actual final faces, including exposed returns but no buried tabs.

    Main wall surface roles alone are insufficient: the north/south walls own
    corner returns, so part of their inside faces is buried, while their end
    caps contribute to the external faces. Clip exact STEP face triangles to
    the compiled room/shell boundary planes. This rectangular-shell measurement
    also retains the compiler's opening voids without calculating cuts again.
    Viewer OBJ files round coordinates and cannot be used for this measurement.
    """
    room = ir.entity("ROOM1")
    outline = room.params["outline"]
    inner = (min(p[0] for p in outline), min(p[1] for p in outline),
             max(p[0] for p in outline), max(p[1] for p in outline))
    walls = ir.of_kind("wall")
    outer = (min(w.geometry.bbox.min[0] for w in walls if w.geometry),
             min(w.geometry.bbox.min[1] for w in walls if w.geometry),
             max(w.geometry.bbox.max[0] for w in walls if w.geometry),
             max(w.geometry.bbox.max[1] for w in walls if w.geometry))
    z0 = min(w.geometry.bbox.min[2] for w in walls if w.geometry)
    z1 = max(w.geometry.bbox.max[2] for w in walls if w.geometry)
    totals = [0.0, 0.0]
    boundaries = []
    for bounds in (inner, outer):
        x0, y0, x1, y1 = bounds
        boundaries.append(((0, x0, y0, y1), (0, x1, y0, y1),
                           (1, y0, x0, x1), (1, y1, x0, x1)))
    for wall in walls:
        if wall.geometry is None:
            continue
        solid = read_step(ir.path(wall.geometry.step))
        for mesh in planar_face_meshes(solid, lambda normal: abs(normal[2]) < 1e-7):
            for face in mesh.triangles:
                points = [mesh.vertices[index] for index in face]
                for side, planes in enumerate(boundaries):
                    for axis, coordinate, start, end in planes:
                        if all(math.isclose(point[axis], coordinate, abs_tol=1e-5) for point in points):
                            projected = Polygon([(point[1 - axis], point[2]) for point in points])
                            if projected.area:
                                totals[side] += projected.intersection(box(start, z0, end, z1)).area
    return totals[0] / 1e6, totals[1] / 1e6


def _quantities(ir: IRDocument) -> dict[str, float | int]:
    walls = ir.of_kind("wall")
    wall_volume = sum(wall.geometry.volume_mm3 for wall in walls if wall.geometry)
    equivalent_wall_area = sum(
        wall.geometry.volume_mm3 / wall.derived_as(WallGeometry).thickness
        for wall in walls if wall.geometry
    )
    room = ir.entity("ROOM1")
    floor_area = room.derived_as(SpaceGeometry).area_mm2 / 1e6
    outline = room.params["outline"]
    perimeter = sum(math.dist(a, b) for a, b in zip(outline, [*outline[1:], outline[0]], strict=True))
    door_widths = sum(door.derived_as(OpeningGeometry).width for door in ir.of_kind("door"))
    internal_faces, external_faces = _net_wall_faces(ir)
    return {
        "floor_area_m2": round(floor_area, 6),
        "floor_finish_area_m2": round(floor_area, 6),
        "ceiling_area_m2": round(floor_area, 6),
        "skirting_length_m": round((perimeter - door_widths) / 1000, 6),
        "internal_plaster_area_m2": round(internal_faces, 6),
        "external_render_area_m2": round(external_faces, 6),
        "wall_net_area_m2": round(equivalent_wall_area / 1e6, 6),
        "paint_area_m2": round(internal_faces + external_faces, 6),
        "wall_volume_m3": round(wall_volume / 1e9, 6),
        "roof_area_m2": round(ir.entity("R0").derived["surface_area_mm2"] / 1e6, 6),
        "door_count": len(ir.of_kind("door")),
        "window_count": len(ir.of_kind("window")),
    }


def _boq(settings: HouseSettings, quantities: dict[str, Any], rate_library: RateLibrary | None = None) -> list[dict[str, Any]]:
    library = _pricing_library(settings, rate_library)
    def opening_description(kind: str) -> tuple[str, list[str]]:
        openings = sorted((opening for opening in settings.openings if opening.kind == kind), key=lambda opening: opening.id)
        default_size = (1000, 2100) if kind == "door" else (1500, 1200)
        # Keep earlier snapshots comparable: changing the exporter vocabulary
        # must not manufacture a zero-cost variation for an unchanged design.
        if openings and all((opening.width_mm, opening.height_mm) == default_size for opening in openings):
            legacy = ("1000 x 2100 mm door; shared illustrative opening allowance" if kind == "door"
                      else "1500 x 1200 mm windows; shared illustrative opening allowance")
            return legacy, [opening.id for opening in openings]
        details = "; ".join(f"{opening.id}: {opening.width_mm:g} x {opening.height_mm:g} mm" for opening in openings)
        if len(openings) > 4:
            details = f"{len(openings)} {kind}s; individual sizes, hosts and offsets are in the coordinated opening schedule"
        return f"{details or ('No ' + kind + 's modelled')}; shared illustrative opening allowance", [opening.id for opening in openings]

    door_description, door_ids = opening_description("door")
    window_description, window_ids = opening_description("window")
    definitions = [
        ("FLOOR", "150 mm floor slab within clear internal room footprint", "floor_area_m2", ["S0"]),
        ("WALL", f"{settings.wall_thickness_mm:g} mm uniform masonry walls, net of openings", "wall_volume_m3", ["W1", "W2", "W3", "W4"]),
        ("ROOF", "200 mm flat roof including 300 mm overhang", "roof_area_m2", ["R0"]),
        ("PAINT", f"Wall paint - {settings.paint_colour} ({settings.paint_hex}); internal and external net faces; "
         f"measured net area {quantities['paint_area_m2']:g} m2 x {settings.paint_coats} coats",
         "paint_area_m2", ["W1", "W2", "W3", "W4"]),
        ("DOOR", door_description, "door_count", door_ids),
        ("WINDOW", window_description, "window_count", window_ids),
        ("FLOOR_FINISH", "Floor finish to clear internal floor area", "floor_finish_area_m2", ["ROOM1", "S0"]),
        ("CEILING", "Ceiling to clear internal room area", "ceiling_area_m2", ["ROOM1", "R0"]),
        ("SKIRTING", "Skirting to internal perimeter less door opening widths", "skirting_length_m", ["ROOM1", "W1", "W2", "W3", "W4", *door_ids]),
        ("INTERNAL_PLASTER", "Internal plaster/render to net internal wall faces, deducting opening voids", "internal_plaster_area_m2", ["W1", "W2", "W3", "W4"]),
        ("EXTERNAL_RENDER", "External render to net external wall faces, deducting opening voids", "external_render_area_m2", ["W1", "W2", "W3", "W4"]),
    ]
    rows = []
    for code, description, quantity_key, element_ids in definitions:
        unit = BOQ_CODES[code][0]
        measured = quantities[quantity_key]
        net = Decimal(str(measured)) * (settings.paint_coats if code == "PAINT" else 1)
        wastage = getattr(settings.wastage, code.lower())
        quantity = float((net * (1 + Decimal(str(wastage)) / 100)).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP))
        library_row = library.rows[code]
        override = code in settings.rate_overrides
        rate = settings.rate_overrides[code] if override else library_row["rate"]
        rows.append({
            "code": code, "description": description, "measured_quantity": measured,
            "net_quantity": float(net), "wastage_percent": wastage, "gross_quantity": quantity,
            "quantity": quantity, "unit": unit, "rate": rate,
            "rate_origin": "override" if override else "library",
            "rate_source": settings.rate_source if override else library_row["source"],
            "rate_effective_date": settings.rate_date.isoformat() if override else library_row["effective_date"],
            "library_rate": library_row["rate"], "library_currency": library.currency,
            "amount": _money(Decimal(str(quantity)) * Decimal(str(rate))), "element_ids": element_ids,
        })
        if code == "PAINT":
            rows[-1]["paint_coats"] = settings.paint_coats
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
            "id": entity.id, "kind": entity.kind, "host": geometry.host,
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


def build_house(settings: HouseSettings, output_dir: Path, rate_library: RateLibrary | None = None) -> dict[str, Any]:
    """Compile a validated house and write exports into an isolated directory.

    The caller owns generation publication and document export. A lock
    serialises native CAD work because it is also used by a local web server.
    No Blender executable or downloaded texture assets are needed.
    """
    settings = HouseSettings.model_validate(settings.model_dump(mode="json"))
    library = _pricing_library(settings, rate_library)
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
        boq = _boq(settings, quantities, library)
        result = {
            "settings": settings.model_dump(mode="json"), "revision": _revision(settings, library),
            "rate_library": library.snapshot(),
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
