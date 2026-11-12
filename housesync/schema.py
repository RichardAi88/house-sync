"""Validated, non-executable inputs for the first House Sync model."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator


class UnitRates(BaseModel):
    """Illustrative allowances only; users must supply project-specific rates."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, validate_assignment=True)

    floor_m2: float = Field(default=80, ge=0, le=1_000_000)
    wall_m3: float = Field(default=300, ge=0, le=1_000_000)
    roof_m2: float = Field(default=120, ge=0, le=1_000_000)
    paint_m2: float = Field(default=12, ge=0, le=1_000_000)
    opening_each: float = Field(default=500, ge=0, le=1_000_000)


class HouseSettings(BaseModel):
    """One rectangular room, fixed centred openings, and a flat roof.

    Length and width are clear INTERNAL dimensions. Limits keep the fixed
    1,000 x 2,100 mm door and 1,500 x 1,200 mm windows within their walls.
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
