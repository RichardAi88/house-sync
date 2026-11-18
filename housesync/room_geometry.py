"""Exact compiled room take-off, including partition junctions and room holes."""

from __future__ import annotations

import math
from dataclasses import field

from shapely.geometry import Point, Polygon, box
from shapely.ops import unary_union

from homespec import Space, Wall
from homespec import geometry as G
from homespec.derived import OpeningGeometry, SpaceGeometry, WallGeometry
from homespec.ir import IRDocument
from homespec.model import Context, Realized, element


@element
class PartitionWall(Wall):
    """Retain the specified construction type in the IR and IFC properties."""

    wall_type: str = "partition"


@element
class LayoutSpace(Space):
    """A named IfcSpace whose exact solid and area retain interior rings."""

    name: str = "Room"
    holes_mm: list[list[tuple[float, float]]] = field(default_factory=list)
    floor_finish: str = ""
    ceiling_finish: str = ""
    skirting_finish: str = ""
    paint_finish: str = ""

    def realize(self, ctx: Context) -> Realized:
        result = super().realize(ctx)
        level = ctx.level(self)
        for hole in self.holes_mm:
            result.solid = result.solid - G.prism(hole, level.elevation, level.height)
        result.derived = SpaceGeometry(area_mm2=G.volume(result.solid) / level.height, height=level.height).model_dump()
        return result


def _segments(outline: list, holes: list) -> list[tuple[int, float, float, float]]:
    segments = []
    for ring in [outline, *holes]:
        points = ring[:-1] if ring and ring[0] == ring[-1] else ring
        for a, b in zip(points, [*points[1:], points[0]], strict=True):
            axis = 0 if abs(a[0] - b[0]) < 1e-5 else 1
            segments.append((axis, float(a[axis]), min(a[1 - axis], b[1 - axis]), max(a[1 - axis], b[1 - axis])))
    return segments


def _strips(segments: list, axis: int, coordinate: float, height: float):
    strips = [box(start, 0, end, height) for segment_axis, value, start, end in segments
              if segment_axis == axis and math.isclose(value, coordinate, abs_tol=1e-5)]
    return unary_union(strips) if strips else Polygon()


def _main_planes(ir: IRDocument) -> list[tuple[int, float, float, float, float]]:
    """Wall main faces and original partition end caps, excluding opening reveals."""
    planes = []
    for wall in ir.of_kind("wall"):
        geometry = wall.derived_as(WallGeometry)
        for side in (0, 1):
            a = geometry.body.point(0, side * geometry.thickness)
            b = geometry.body.point(geometry.length, side * geometry.thickness)
            axis = 0 if abs(a[0] - b[0]) < 1e-5 else 1
            planes.append((axis, a[axis], min(a[1 - axis], b[1 - axis]), max(a[1 - axis], b[1 - axis]), geometry.height))
        if not wall.has("external"):
            for at in (0, geometry.length):
                a, b = geometry.body.point(at, 0), geometry.body.point(at, geometry.thickness)
                axis = 0 if abs(a[0] - b[0]) < 1e-5 else 1
                planes.append((axis, a[axis], min(a[1 - axis], b[1 - axis]), max(a[1 - axis], b[1 - axis]), geometry.height))
    return planes


def _allocate_exposed_band(face, axis: int, coordinate: float, normal: tuple, rooms: list[dict], polygons: dict) -> dict[str, float]:
    """Assign above-junction exposed bands without dropping their exact area.

    The room plan omits every partition footprint even when a wall is lower.
    Consequently some exposed upper wall bands have no ground-plan boundary.
    Split their exact surface at projected room vertices and gap midpoints,
    then use the nearest room in plan on the exposed side. Stable IDs break
    distance ties. This affects room attribution, never the measured total.
    """
    low, _z0, high, _z1 = face.bounds
    coordinates = {low, high}
    for room in rooms:
        for ring in [room["outline_mm"], *room.get("holes_mm", [])]:
            coordinates.update(point[1 - axis] for point in ring if low < point[1 - axis] < high)
    ordered = sorted(coordinates)
    cuts = sorted(set(ordered + [(a + b) / 2 for a, b in zip(ordered, ordered[1:], strict=False)]))
    result = {room["id"]: 0.0 for room in rooms}
    for start, end in zip(cuts, cuts[1:], strict=False):
        piece = face.intersection(box(start, -1, end, 100000))
        if piece.area <= 1e-6:
            continue
        along = (start + end) / 2
        point = [0.0, 0.0]
        point[axis] = coordinate + normal[axis] * .01
        point[1 - axis] = along
        room_id = min(polygons, key=lambda identity: (round(polygons[identity].distance(Point(point)), 8), identity))
        result[room_id] += piece.area
    return result


def measure_rooms(ir: IRDocument, layout: dict) -> tuple[list[dict], list[dict], dict[str, float]]:
    """Read clear spaces and all exposed finished wall faces from exact STEP."""
    rooms = []
    for declaration in layout["rooms"]:
        entity = ir.entity(declaration["id"])
        room = dict(declaration)
        room["area_m2"] = round(entity.derived_as(SpaceGeometry).area_mm2 / 1e6, 6)
        room["floor_finish_area_m2"] = room["ceiling_area_m2"] = room["area_m2"]
        room["internal_plaster_area_m2"] = room["paint_area_m2"] = 0.0
        room["junction_exposed_area_m2"] = 0.0
        room["wall_element_ids"] = sorted({boundary["wall_id"] for boundary in declaration.get("boundaries", [])})
        room["door_ids"], room["opening_ids"] = [], []
        rooms.append(room)
    polygons = {room["id"]: Polygon(room["outline_mm"], room.get("holes_mm", [])) for room in rooms}
    segments = {room["id"]: _segments(room["outline_mm"], room.get("holes_mm", [])) for room in rooms}
    door_widths = {room["id"]: 0.0 for room in rooms}
    by_id = {room["id"]: room for room in rooms}
    for opening in ir.tagged("opening"):
        for contact in opening.derived_as(OpeningGeometry).rooms:
            if contact.room in door_widths:
                by_id[contact.room]["opening_ids"].append(opening.id)
                if opening.kind == "door":
                    by_id[contact.room]["door_ids"].append(opening.id)
                    door_widths[contact.room] += sum(end - start for start, end in contact.intervals)
    for room in rooms:
        perimeter = polygons[room["id"]].length
        room["perimeter_m"] = round(perimeter / 1000, 6)
        room["skirting_length_m"] = round(max(0, perimeter - door_widths[room["id"]]) / 1000, 6)
        room["door_ids"] = sorted(set(room["door_ids"]))
        room["opening_ids"] = sorted(set(room["opening_ids"]))
    walls = ir.of_kind("wall")
    shells = [wall for wall in walls if wall.has("external")]
    outer = (min(wall.geometry.bbox.min[0] for wall in shells), min(wall.geometry.bbox.min[1] for wall in shells),
             max(wall.geometry.bbox.max[0] for wall in shells), max(wall.geometry.bbox.max[1] for wall in shells))
    exterior_planes = _segments([[outer[0], outer[1]], [outer[2], outer[1]], [outer[2], outer[3]], [outer[0], outer[3]]], [])
    height = max(wall.derived_as(WallGeometry).height for wall in walls)
    main_planes = _main_planes(ir)
    solid = G.union(G.read_step(ir.path(wall.geometry.step)) for wall in walls if wall.geometry)
    external_area = 0.0
    for mesh in G.planar_face_meshes(solid, lambda normal: abs(normal[2]) < 1e-7):
        axis = 0 if abs(mesh.normal[0]) > .9 else 1
        coordinate = mesh.vertices[0][axis]
        triangles = [Polygon([(mesh.vertices[index][1 - axis], mesh.vertices[index][2]) for index in face]) for face in mesh.triangles]
        surface = unary_union(triangles)
        exterior = surface.intersection(_strips(exterior_planes, axis, coordinate, height))
        external_area += exterior.area
        if exterior.area > 1e-6:
            continue
        eligible = unary_union([box(start, 0, end, wall_height) for plane_axis, value, start, end, wall_height in main_planes
                                if plane_axis == axis and math.isclose(value, coordinate, abs_tol=1e-5)])
        surface = surface.intersection(eligible)
        if surface.area <= 1e-6:
            continue
        assigned = []
        for room in rooms:
            contact = surface.intersection(_strips(segments[room["id"]], axis, coordinate, height))
            room["internal_plaster_area_m2"] += contact.area / 1e6
            assigned.append(contact)
        remainder = surface.difference(unary_union(assigned))
        if remainder.area > 1e-5:
            allocation = _allocate_exposed_band(remainder, axis, coordinate, mesh.normal, rooms, polygons)
            for room in rooms:
                extra = allocation[room["id"]] / 1e6
                room["internal_plaster_area_m2"] += extra
                room["junction_exposed_area_m2"] += extra
    for room in rooms:
        room["internal_plaster_area_m2"] = round(room["internal_plaster_area_m2"], 6)
        room["paint_area_m2"] = room["internal_plaster_area_m2"]
        room["junction_exposed_area_m2"] = round(room["junction_exposed_area_m2"], 6)
    partitions = [{**declaration, "volume_m3": round(ir.entity(declaration["id"]).geometry.volume_mm3 / 1e9, 6)}
                  for declaration in layout["partitions"]]
    aggregate = {
        "floor_finish_area_m2": round(sum(room["area_m2"] for room in rooms), 6),
        "ceiling_area_m2": round(sum(room["area_m2"] for room in rooms), 6),
        "skirting_length_m": round(sum(room["skirting_length_m"] for room in rooms), 6),
        "internal_plaster_area_m2": round(sum(room["internal_plaster_area_m2"] for room in rooms), 6),
        "external_render_area_m2": round(external_area / 1e6, 6),
        "shell_wall_volume_m3": round(sum(wall.geometry.volume_mm3 for wall in shells) / 1e9, 6),
        "partition_wall_volume_m3": round(sum(ir.entity(partition["id"]).geometry.volume_mm3 for partition in partitions) / 1e9, 6),
    }
    aggregate["paint_area_m2"] = round(aggregate["internal_plaster_area_m2"] + aggregate["external_render_area_m2"], 6)
    return rooms, partitions, aggregate
