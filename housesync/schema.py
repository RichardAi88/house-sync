"""Validated, non-executable house parameters and editable wall openings."""

from __future__ import annotations

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

OPENING_CLEARANCE_MM = 100.0


class UnitRates(BaseModel):
    """Illustrative allowances only; users must supply project-specific rates."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, validate_assignment=True)

    floor_m2: float = Field(default=80, ge=0, le=1_000_000)
    wall_m3: float = Field(default=300, ge=0, le=1_000_000)
    roof_m2: float = Field(default=120, ge=0, le=1_000_000)
    paint_m2: float = Field(default=12, ge=0, le=1_000_000)
    opening_each: float = Field(default=500, ge=0, le=1_000_000)


class OpeningSettings(BaseModel):
    """One rectangular opening measured along its host's inner reference line.

    A null offset keeps the opening centred when its host changes length.
    W1 runs east, W2 north, W3 west and W4 south from their inner corners.
    """

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, validate_assignment=True)

    id: str = Field(pattern=r"^[DN][1-9][0-9]{0,2}$")
    kind: Literal["door", "window"]
    host: Literal["W1", "W2", "W3", "W4"]
    offset_mm: float | None = Field(default=None, ge=0, le=30000)
    width_mm: float = Field(default=1000, ge=300, le=30000)
    height_mm: float = Field(default=2100, ge=300, le=4900)
    sill_mm: float = Field(default=0, ge=0, le=4500)

    @field_validator("offset_mm", "width_mm", "height_mm", "sill_mm", mode="before")
    @classmethod
    def numeric_dimensions(cls, value: object) -> object:
        if isinstance(value, bool):
            raise ValueError("opening dimensions must be numbers, not booleans")
        return value

    @model_validator(mode="after")
    def opening_type(self) -> Self:
        prefix = "D" if self.kind == "door" else "N"
        if not self.id.startswith(prefix):
            raise ValueError(f"{self.kind} IDs must start with {prefix}")
        if self.kind == "door" and self.sill_mm != 0:
            raise ValueError("doors must start at floor level (sill 0 mm)")
        return self


def default_openings() -> list[OpeningSettings]:
    """Preserve the original design when older settings omit openings."""
    return [
        OpeningSettings(id="D1", kind="door", host="W1", width_mm=1000, height_mm=2100),
        OpeningSettings(id="N1", kind="window", host="W3", width_mm=1500, height_mm=1200, sill_mm=900),
        OpeningSettings(id="N2", kind="window", host="W2", width_mm=1500, height_mm=1200, sill_mm=900),
    ]


class HouseSettings(BaseModel):
    """One rectangular room with editable openings and a flat roof.

    Length and width are clear INTERNAL dimensions. Opening starts use inner
    wall references and must leave 100 mm clear at each end, at the head, and
    between openings on one host. These are modelling constraints, not design approval.
    """

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, validate_assignment=True)

    project_name: str = Field(default="House Sync demonstration", min_length=1, max_length=120)
    length_mm: float = Field(default=10000, ge=3000, le=30000)
    width_mm: float = Field(default=8000, ge=3000, le=30000)
    height_mm: float = Field(default=3000, ge=2400, le=5000)
    wall_thickness_mm: float = Field(default=200, ge=100, le=500)
    paint_colour: str = Field(default="Light blue", min_length=1, max_length=80)
    paint_hex: str = Field(default="#ADD8E6", pattern=r"^#[0-9A-Fa-f]{6}$")
    currency: str = Field(default="SGD", pattern=r"^[A-Z]{3}$")
    rates: UnitRates = Field(default_factory=UnitRates)
    openings: list[OpeningSettings] = Field(default_factory=default_openings, max_length=40)

    @field_validator("project_name", "paint_colour")
    @classmethod
    def printable_text(cls, value: str) -> str:
        value = value.strip()
        if not value or any(ord(character) < 32 for character in value):
            raise ValueError("use a non-empty, single-line name without control characters")
        return value

    @field_validator("paint_hex", "currency", mode="before")
    @classmethod
    def uppercase_code(cls, value: object) -> object:
        return value.strip().upper() if isinstance(value, str) else value

    @model_validator(mode="after")
    def openings_fit(self) -> Self:
        seen: set[str] = set()
        intervals: dict[str, list[tuple[float, float, str]]] = {}
        for opening in self.openings:
            if opening.id in seen:
                raise ValueError(f"Duplicate opening ID {opening.id}")
            seen.add(opening.id)
            span = self.length_mm if opening.host in {"W1", "W3"} else self.width_mm
            start = (span - opening.width_mm) / 2 if opening.offset_mm is None else opening.offset_mm
            end = start + opening.width_mm
            if start < OPENING_CLEARANCE_MM - 1e-6 or end > span - OPENING_CLEARANCE_MM + 1e-6:
                raise ValueError(f"{opening.id} must fit {opening.host} with at least 100 mm clear at each inner end")
            if opening.sill_mm + opening.height_mm > self.height_mm - OPENING_CLEARANCE_MM + 1e-6:
                raise ValueError(f"{opening.id} must leave at least 100 mm below the wall head")
            intervals.setdefault(opening.host, []).append((start, end, opening.id))
        for host, runs in intervals.items():
            ordered = sorted(runs)
            for previous, current in zip(ordered, ordered[1:], strict=False):
                if current[0] < previous[1] + OPENING_CLEARANCE_MM - 1e-6:
                    raise ValueError(f"{previous[2]} and {current[2]} on {host} must be at least 100 mm apart in plan")
        return self
