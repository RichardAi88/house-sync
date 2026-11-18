"""Orthogonal partition topology and clear room polygons in millimetres.

This is the shared, non-CAD layout used for validation and draft previews. The
compiler builds these same wall footprints and independently measures its final
solids. Door voids connect rooms without merging their zoning polygons.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from typing import Any

from shapely.geometry import LineString, Point, Polygon, box
from shapely.geometry.polygon import orient
from shapely.ops import polygonize, unary_union

EPS = 1e-5
CLEARANCE = 100.0


def _number(value: Any, label: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{label} must be a finite number")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{label} must be a finite number")
    return number


def _point(value: Any, label: str) -> tuple[float, float]:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise ValueError(f"{label} must have two numeric coordinates")
    return _number(value[0], label), _number(value[1], label)


def _coords(ring) -> list[list[float]]:
    return [[round(x, 6), round(y, 6)] for x, y in list(ring.coords)[:-1]]


def _polygons(geometry) -> list[Polygon]:
    if geometry.is_empty:
        return []
    if geometry.geom_type == "Polygon":
        return [geometry]
    return [part for child in getattr(geometry, "geoms", ()) for part in _polygons(child)]


def _lines(geometry) -> list[LineString]:
    if geometry.is_empty:
        return []
    if geometry.geom_type in {"LineString", "LinearRing"}:
        return [geometry]
    return [part for child in getattr(geometry, "geoms", ()) for part in _lines(child)]


def _side(wall: dict, point: tuple[float, float]) -> str:
    a, b = wall["start_mm"], wall["end_mm"]
    if abs(a[0] - b[0]) < EPS:
        return "west" if point[0] < a[0] else "east"
    return "south" if point[1] < a[1] else "north"


def _canonical(tokens: list[str]) -> str:
    merged = [token for index, token in enumerate(tokens) if index == 0 or token != tokens[index - 1]]
    if len(merged) > 1 and merged[0] == merged[-1]:
        merged.pop()
    if not merged:
        return ""
    variants = [merged, list(reversed(merged))]
    return min("|".join(values[index:] + values[:index]) for values in variants for index in range(len(values)))


def _ring_signature(ring, polygon: Polygon, walls: dict[str, dict]) -> str:
    tokens = []
    points = list(ring.coords)
    # Topological faces use centrelines, independent of physical thickness.
    # A small point on the face's side of each edge identifies signed wall side.
    for a, b in zip(points, points[1:], strict=False):
        edge = LineString([a, b])
        middle = edge.interpolate(.5, normalized=True)
        matches = [wall for wall in walls.values() if wall["line"].distance(middle) < EPS
                   and wall["line"].intersection(edge).length > EPS]
        if not matches:
            raise ValueError("Unable to identify a room boundary wall")
        wall = sorted(matches, key=lambda row: row["id"])[0]
        dx, dy = b[0] - a[0], b[1] - a[1]
        length = math.hypot(dx, dy)
        probe = (middle.x - dy / length * .01, middle.y + dx / length * .01)
        if not polygon.covers(Point(probe)):
            probe = (middle.x + dy / length * .01, middle.y - dx / length * .01)
        tokens.append(f"{wall['id']}:{_side(wall, probe)}")
    return _canonical(tokens)


def _room_id(polygon: Polygon, walls: dict[str, dict], has_partitions: bool) -> str:
    if not has_partitions:
        return "ROOM1"
    rings = [_ring_signature(polygon.exterior, polygon, walls)]
    rings.extend(sorted(_ring_signature(ring, polygon, walls) for ring in polygon.interiors))
    return "ROOM_" + hashlib.sha256(json.dumps(rings, separators=(",", ":")).encode()).hexdigest()[:12].upper()


def _min_width(polygon: Polygon) -> float:
    """Check every orthogonal slice; bounding boxes miss narrow L-room legs."""
    points = [point for ring in [polygon.exterior, *polygon.interiors] for point in ring.coords]
    x0, y0, x1, y1 = polygon.bounds
    shortest = math.inf
    for axis in (0, 1):
        positions = sorted({round(point[axis], 6) for point in points})
        for low, high in zip(positions, positions[1:], strict=False):
            if high - low <= EPS:
                continue
            midpoint = (low + high) / 2
            cut = (LineString([(midpoint, y0 - 1), (midpoint, y1 + 1)]) if axis == 0
                   else LineString([(x0 - 1, midpoint), (x1 + 1, midpoint)]))
            for segment in _lines(polygon.intersection(cut)):
                if segment.length > EPS:
                    shortest = min(shortest, segment.length)
    return shortest


def _derive(data: dict, errors: list[str], warnings: list[str]) -> dict:
    length = _number(data.get("length_mm", 10000), "Internal length")
    width = _number(data.get("width_mm", 8000), "Internal width")
    height = _number(data.get("height_mm", 3000), "Wall height")
    shell_thickness = _number(data.get("wall_thickness_mm", 200), "Wall thickness")
    minimum = _number(data.get("min_room_width_mm", 900), "Minimum room width")
    if not (3000 <= length <= 30000 and 3000 <= width <= 30000 and 2400 <= height <= 5000
            and 100 <= shell_thickness <= 500 and 300 <= minimum <= 10000):
        raise ValueError("Shell dimensions or minimum room width are outside the model's validated limits")
    shell = box(0, 0, length, width)
    shell_points = [(0, 0), (length, 0), (length, width), (0, width)]
    walls: dict[str, dict] = {}
    for index, (a, b) in enumerate(zip(shell_points, [*shell_points[1:], shell_points[0]], strict=True), 1):
        walls[f"W{index}"] = {"id": f"W{index}", "start_mm": a, "end_mm": b, "length_mm": math.dist(a, b),
                              "thickness_mm": shell_thickness, "height_mm": height, "line": LineString([a, b])}
    raw_partitions = data.get("partitions", [])
    if not isinstance(raw_partitions, list) or len(raw_partitions) > 40:
        raise ValueError("Partitions must be a list of at most 40 walls")
    partitions = []
    for row in raw_partitions:
        if not isinstance(row, dict):
            raise ValueError("Each partition must be an object")
        identity = row.get("id")
        if not isinstance(identity, str) or not re.fullmatch(r"P[1-9][0-9]{0,2}", identity):
            raise ValueError("Partitions require stable P1 to P999 identifiers")
        if identity in walls:
            raise ValueError(f"Duplicate partition ID {identity}")
        a, b = _point(row.get("start_mm"), identity), _point(row.get("end_mm"), identity)
        if any(not (-EPS <= point[0] <= length + EPS and -EPS <= point[1] <= width + EPS) for point in (a, b)):
            raise ValueError(f"{identity} must stay inside the clear shell")
        thickness = _number(row.get("thickness_mm", 100), f"{identity} thickness")
        wall_height = _number(row.get("height_mm", 3000), f"{identity} height")
        if not (50 <= thickness <= 500 and 300 <= wall_height <= height + EPS):
            raise ValueError(f"{identity} thickness/height are outside model limits or its height exceeds the shell")
        if math.dist(a, b) < EPS or (abs(a[0] - b[0]) > EPS and abs(a[1] - b[1]) > EPS):
            raise ValueError(f"{identity} must be a non-zero axis-aligned partition")
        line = LineString([a, b])
        if not shell.buffer(EPS).covers(line):
            errors.append(f"{identity} must stay inside the clear shell")
        if shell.boundary.intersection(line).length > EPS:
            errors.append(f"{identity} cannot overlap an external wall reference line")
        wall = {"id": identity, "start_mm": a, "end_mm": b, "thickness_mm": thickness,
                "height_mm": wall_height, "wall_type": row.get("wall_type", "partition"),
                "length_mm": line.length, "line": line}
        walls[identity] = wall
        partitions.append(wall)
    partitions.sort(key=lambda row: int(row["id"][1:]))
    for index, wall in enumerate(partitions):
        for endpoint in (wall["start_mm"], wall["end_mm"]):
            at = Point(endpoint)
            if shell.boundary.distance(at) < EPS:
                continue
            elif not any(other["line"].distance(at) < EPS for other in partitions if other is not wall):
                errors.append(f"{wall['id']} has a floating endpoint; connect it to the shell or another partition centreline")
        for other in partitions[:index]:
            intersection = wall["line"].intersection(other["line"])
            if intersection.length > EPS:
                errors.append(f"{wall['id']} overlaps {other['id']} along their centrelines")
    for wall in partitions:
        a, b = wall["start_mm"], wall["end_mm"]
        direction = ((b[0] - a[0]) / wall["length_mm"], (b[1] - a[1]) / wall["length_mm"])
        extensions = []
        for endpoint in (a, b):
            if shell.boundary.distance(Point(endpoint)) < EPS:
                extensions.append(0.0)
                continue
            others = [other for other in partitions if other is not wall and other["line"].distance(Point(endpoint)) < EPS
                      and abs(direction[0] * (other["end_mm"][0] - other["start_mm"][0])
                              + direction[1] * (other["end_mm"][1] - other["start_mm"][1])) < EPS]
            extensions.append(max((other["thickness_mm"] / 2 for other in others), default=0))
        physical_a = (a[0] - direction[0] * extensions[0], a[1] - direction[1] * extensions[0])
        physical_b = (b[0] + direction[0] * extensions[1], b[1] + direction[1] * extensions[1])
        full_footprint = LineString([physical_a, physical_b]).buffer(wall["thickness_mm"] / 2, cap_style="flat")
        if not shell.buffer(EPS).covers(full_footprint):
            errors.append(f"{wall['id']} wall thickness or junction return extends outside the clear shell")
        footprint = full_footprint.intersection(shell)
        wall.update({"physical_start_mm": physical_a, "physical_end_mm": physical_b,
                     "start_extension_mm": extensions[0], "end_extension_mm": extensions[1], "footprint": footprint})
    for index, wall in enumerate(partitions):
        for other in partitions[:index]:
            overlap = wall["footprint"].intersection(other["footprint"])
            if (overlap.area > EPS or overlap.length > EPS) and wall["line"].distance(other["line"]) > EPS:
                errors.append(f"{wall['id']} physically overlaps or touches {other['id']} without a centreline junction")
    solid_plan = unary_union([wall["footprint"] for wall in partitions]) if partitions else Polygon()
    clear = shell.difference(solid_plan)
    topological = list(polygonize(unary_union([shell.boundary, *(wall["line"] for wall in partitions)])))
    metadata = data.get("rooms", [])
    if not isinstance(metadata, list) or len(metadata) > 200:
        raise ValueError("Room metadata must be a list of at most 200 entries")
    metadata_by_id = {}
    for row in metadata:
        if not isinstance(row, dict) or not isinstance(row.get("id"), str):
            raise ValueError("Room metadata requires a stable room ID")
        if row["id"] in metadata_by_id:
            errors.append(f"Duplicate room metadata ID {row['id']}")
        metadata_by_id[row["id"]] = row
    rooms = []
    room_polygons = {}
    defaults = {"floor": "Floor finish allowance", "ceiling": "Ceiling allowance", "skirting": "Skirting allowance",
                "paint": f"{data.get('paint_colour', 'Light blue')} ({data.get('paint_hex', '#ADD8E6')})"}
    clear_polygons = sorted(_polygons(clear), key=lambda polygon: (polygon.centroid.x, polygon.centroid.y))
    for index, raw_polygon in enumerate(clear_polygons, 1):
        polygon = orient(raw_polygon, sign=1)
        point = polygon.representative_point()
        face = next((face for face in topological if face.covers(point)), None)
        if face is None:
            raise ValueError("A clear room has no matching centreline topology")
        identity = _room_id(face, walls, bool(partitions))
        if identity in room_polygons:
            raise ValueError("Wall footprints split one topological room; check overlapping wall junctions")
        room_polygons[identity] = polygon
        info = metadata_by_id.get(identity, {})
        selected = info.get("finishes") or {}
        if not isinstance(selected, dict):
            raise ValueError(f"{identity} finishes must be an object")
        finishes = {key: selected.get(key) or value for key, value in defaults.items()}
        minimum_width = _min_width(polygon)
        if minimum_width < minimum - EPS:
            errors.append(f"{identity} clear width {minimum_width:g} mm is below the minimum {minimum:g} mm")
        boundaries = []
        for ring in [polygon.exterior, *polygon.interiors]:
            coordinates = list(ring.coords)
            for a, b in zip(coordinates, coordinates[1:], strict=False):
                edge = LineString([a, b])
                midpoint = edge.interpolate(.5, normalized=True)
                candidates = [wall for wall in walls.values()
                              if (wall["footprint"].boundary.distance(midpoint) if "footprint" in wall else wall["line"].distance(midpoint)) < EPS]
                if not candidates:
                    continue
                host = sorted(candidates, key=lambda wall: (wall["id"].startswith("P"), wall["id"]))[0]
                dx, dy = b[0] - a[0], b[1] - a[1]
                local_probe = (midpoint.x - dy / edge.length * .01, midpoint.y + dx / edge.length * .01)
                if not polygon.covers(Point(local_probe)):
                    local_probe = (midpoint.x + dy / edge.length * .01, midpoint.y - dx / edge.length * .01)
                boundaries.append({"wall_id": host["id"], "side": _side(host, local_probe),
                                   "start_mm": list(a), "end_mm": list(b), "length_mm": edge.length,
                                   "height_mm": host["height_mm"]})
        rooms.append({"id": identity, "name": info.get("name") or f"Room {index}", "finishes": finishes,
                      "outline_mm": _coords(polygon.exterior), "holes_mm": [_coords(ring) for ring in polygon.interiors],
                      "label_point_mm": [point.x, point.y], "area_m2": round(polygon.area / 1e6, 6),
                      "perimeter_m": round(polygon.length / 1000, 6), "min_width_mm": round(minimum_width, 6),
                      "boundaries": boundaries})
    if not rooms:
        errors.append("The layout has no usable clear room area")
    # Names must not swap when centroid ordering changes (for example when an
    # interior ring moves). User names are keyed by identity; default names are
    # numbered by that same stable identity rather than current coordinates.
    default_names = {identity: f"Room {index}" for index, identity in enumerate(sorted(room_polygons), 1)}
    for room in rooms:
        room["name"] = metadata_by_id.get(room["id"], {}).get("name") or default_names[room["id"]]
    for identity in metadata_by_id.keys() - room_polygons.keys():
        warnings.append(f"Room metadata {identity} has no current region; retained for revision history")
    connections = _opening_checks(data, walls, partitions, room_polygons, errors)
    if partitions:
        adjacency = {identity: set() for identity in room_polygons}
        accessible = set()
        for connection in connections:
            ids = connection["room_ids"]
            if connection["kind"] != "door":
                continue
            if connection["exterior"]:
                accessible.update(ids)
            for identity in ids:
                adjacency[identity].update(set(ids) - {identity})
        pending = list(accessible)
        while pending:
            for identity in adjacency[pending.pop()] - accessible:
                accessible.add(identity)
                pending.append(identity)
        for room in rooms:
            room["reachable"] = room["id"] in accessible
            if not room["reachable"]:
                errors.append(f"{room['id']} ({room['name']}) is unreachable by doors from the exterior")
    else:
        for room in rooms:
            room["reachable"] = True
    published_partitions = []
    for index, wall in enumerate(partitions):
        junctions = []
        for other in walls.values():
            if other["id"] == wall["id"]:
                continue
            intersection = wall["line"].intersection(other["line"])
            if intersection.geom_type == "Point":
                junctions.append({"other_id": other["id"], "at_mm": [intersection.x, intersection.y],
                                  "offset_mm": wall["line"].project(intersection), "half_width_mm": other["thickness_mm"] / 2})
        published_partitions.append({key: value for key, value in wall.items() if key not in {"line", "footprint"}} | {
            "footprint_mm": _coords(wall["footprint"].exterior), "junctions": junctions,
            "cut_against": [earlier["id"] for earlier in partitions[:index]
                            if earlier["footprint"].intersection(wall["footprint"]).area > EPS],
        })
    return {"rooms": rooms, "partitions": published_partitions, "connections": connections,
            "clear_area_m2": round(clear.area / 1e6, 6), "partition_area_m2": round(solid_plan.area / 1e6, 6)}


def _opening_checks(data: dict, walls: dict, partitions: list[dict], rooms: dict, errors: list[str]) -> list[dict]:
    # Imports are local to avoid a schema/layout import cycle during validation.
    from .schema import default_openings

    openings = data.get("openings")
    if openings is None:
        openings = [opening.model_dump(mode="json") for opening in default_openings()]
    if not isinstance(openings, list) or len(openings) > 40:
        raise ValueError("Openings must be a list of at most 40 items")
    seen = set()
    runs: dict[str, list] = {}
    connections = []
    for opening in openings:
        if not isinstance(opening, dict):
            raise ValueError("Each opening must be an object")
        identity, host_id = opening.get("id"), opening.get("host")
        if not isinstance(identity, str) or not re.fullmatch(r"[DN][1-9][0-9]{0,2}", identity) or identity in seen:
            errors.append(f"Duplicate or invalid opening ID {identity}")
            continue
        seen.add(identity)
        if host_id not in walls:
            errors.append(f"{identity} references unknown host {host_id}")
            continue
        host = walls[host_id]
        opening_width = _number(opening.get("width_mm", 1000), f"{identity} width")
        opening_height = _number(opening.get("height_mm", 2100), f"{identity} height")
        sill = _number(opening.get("sill_mm", 0), f"{identity} sill")
        if not (300 <= opening_width <= 30000 and 300 <= opening_height <= 4900 and 0 <= sill <= 4500):
            errors.append(f"{identity} opening dimensions are outside the model's validated limits")
            continue
        kind = opening.get("kind", "door")
        if kind not in {"door", "window"} or not identity.startswith("D" if kind == "door" else "N"):
            errors.append(f"{identity} must have a matching door/window kind and identifier")
        if kind == "door" and sill != 0:
            errors.append(f"{identity} doors must start at floor level")
        start = ((host["length_mm"] - opening_width) / 2 if opening.get("offset_mm") is None
                 else _number(opening["offset_mm"], f"{identity} offset"))
        if not 0 <= start <= 30000:
            errors.append(f"{identity} opening start must be between 0 and 30000 mm")
            continue
        end = start + opening_width
        if opening_width <= 0 or opening_height <= 0 or sill < 0:
            errors.append(f"{identity} opening dimensions must be positive with a non-negative sill")
        if start < CLEARANCE - EPS or end > host["length_mm"] - CLEARANCE + EPS:
            errors.append(f"{identity} must fit {host_id} with at least 100 mm clear at each inner end")
        if sill + opening_height > host["height_mm"] - CLEARANCE + EPS:
            errors.append(f"{identity} must leave at least 100 mm below its host wall head")
        for other in partitions:
            if other["id"] == host_id:
                continue
            intersection = host["line"].intersection(other["line"])
            if intersection.geom_type == "Point":
                at = host["line"].project(intersection)
                exclusion = other["thickness_mm"] / 2 + CLEARANCE
                if start < at + exclusion - EPS and end > at - exclusion + EPS:
                    errors.append(f"{identity} must leave 100 mm clear of the {other['id']} junction footprint")
        for prior_start, prior_end, prior_id in runs.get(host_id, []):
            if start < prior_end + CLEARANCE - EPS and end > prior_start - CLEARANCE + EPS:
                errors.append(f"{prior_id} and {identity} on {host_id} must be at least 100 mm apart in plan")
        runs.setdefault(host_id, []).append((start, end, identity))
        a, b = host["start_mm"], host["end_mm"]
        u = ((b[0] - a[0]) / host["length_mm"], (b[1] - a[1]) / host["length_mm"])
        normal = (-u[1], u[0])
        side_sets = []
        for sign in (-1, 1):
            distance = (host["thickness_mm"] / 2 + .01) if host_id.startswith("P") else .01
            probe = LineString([(a[0] + u[0] * offset + normal[0] * distance * sign,
                                 a[1] + u[1] * offset + normal[1] * distance * sign)
                                for offset in (start + EPS, end - EPS)])
            adjacent = [identity for identity, polygon in rooms.items() if polygon.buffer(EPS).covers(probe)]
            side_sets.append(adjacent)
        room_ids = sorted(set(side_sets[0] + side_sets[1]))
        exterior = host_id.startswith("W")
        expected = 1 if exterior else 2
        if len(room_ids) != expected:
            errors.append(f"{identity} does not adjoin {expected} distinct clear room(s) across its full opening width")
        connections.append({"opening_id": identity, "kind": opening.get("kind", "door"),
                            "host": host_id, "room_ids": room_ids, "exterior": exterior})
    return connections


def derive_layout(settings: Any, *, validate: bool = False) -> dict:
    """Return plain JSON topology; invalid drafts still provide check messages.

    Schema validation calls ``validate=True``. Preview callers use the default
    so incomplete door connections do not prevent reviewing room polygons.
    """
    errors: list[str] = []
    warnings: list[str] = []
    result: dict = {"rooms": [], "partitions": [], "connections": []}
    try:
        data = settings.model_dump(mode="json") if hasattr(settings, "model_dump") else settings
        if not isinstance(data, dict):
            raise ValueError("Layout settings must be an object")
        result = _derive(data, errors, warnings)
    except (ValueError, TypeError, KeyError, AttributeError, IndexError, OverflowError) as exc:
        errors.append(str(exc))
    result["checks"] = {"errors": list(dict.fromkeys(errors)), "warnings": list(dict.fromkeys(warnings))}
    if validate and errors:
        raise ValueError("; ".join(result["checks"]["errors"]))
    return result
