"""Validated, non-executable house parameters and editable wall openings."""

from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Annotated, Any, Literal, Self
from unicodedata import category

from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator, model_validator

from .rates import BOQ_CODES

OPENING_CLEARANCE_MM = 100.0


def _quote_text(value: Any, *, multiline: bool = False) -> str:
    """Keep names/scope as plain text, preserving intentional paragraphs."""
    if not isinstance(value, str):
        raise ValueError("use plain text")
    value = value.replace("\r\n", "\n").strip()
    if any(category(character) == "Cc" and not (multiline and character == "\n") for character in value):
        raise ValueError("plain text must not contain control characters")
    return value


def _aud_cents(value: Any) -> Any:
    """Reject fractional cents instead of silently changing a commercial input."""
    if isinstance(value, bool):
        raise ValueError("AUD amounts must be numbers, not booleans")
    try:
        amount = Decimal(str(value))
        if not amount.is_finite() or amount < 0 or amount != amount.quantize(Decimal("0.01")):
            raise ValueError("AUD amounts must be finite, non-negative whole cents")
    except InvalidOperation as exc:
        raise ValueError("AUD amounts must be finite, non-negative whole cents") from exc
    return value


class BuilderPricing(BaseModel):
    """Client quote settings; overhead/margin is one markup on the base cost."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, validate_assignment=True)

    overhead_margin_percent: float = Field(default=20.0, ge=0, le=1000, title="Markup on cost (%)")
    admin_fee_aud: float = Field(default=0.0, ge=0, le=1_000_000)
    gst_percent: Literal[10] = 10
    credit_treatment: Literal["at_cost", "including_margin"] = "at_cost"
    validity_days: StrictInt = Field(default=14, ge=1, le=365)
    builder_name: str = Field(default="", max_length=120)
    client_description: str = Field(default="", max_length=2000)

    @field_validator("overhead_margin_percent", mode="before")
    @classmethod
    def numeric_markup(cls, value: Any) -> Any:
        if isinstance(value, bool):
            raise ValueError("markup on cost must be a number, not a boolean")
        return value

    @field_validator("admin_fee_aud", mode="before")
    @classmethod
    def whole_cent_admin_fee(cls, value: Any) -> Any:
        return _aud_cents(value)

    @field_validator("builder_name", mode="before")
    @classmethod
    def plain_builder_name(cls, value: Any) -> str:
        return _quote_text(value)

    @field_validator("client_description", mode="before")
    @classmethod
    def plain_client_scope(cls, value: Any) -> str:
        return _quote_text(value, multiline=True)


class AllowanceSettings(BaseModel):
    """A separately identified provisional sum or prime-cost allowance in AUD."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, validate_assignment=True)

    id: str = Field(pattern=r"^AL[1-9][0-9]{0,5}$")
    kind: Literal["provisional_sum", "prime_cost"]
    description: str = Field(min_length=1, max_length=240)
    amount: float = Field(ge=0, le=10_000_000)
    note: str = Field(default="", max_length=1000)

    @field_validator("amount", mode="before")
    @classmethod
    def whole_cent_amount(cls, value: Any) -> Any:
        return _aud_cents(value)

    @field_validator("description", "note", mode="before")
    @classmethod
    def plain_allowance_labels(cls, value: Any) -> str:
        return _quote_text(value, multiline=True)


class WastageSettings(BaseModel):
    """Percentage allowances on each BOQ item's net priced quantity."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, validate_assignment=True)

    floor: float = Field(default=0, ge=0, le=100)
    wall: float = Field(default=0, ge=0, le=100)
    roof: float = Field(default=0, ge=0, le=100)
    paint: float = Field(default=0, ge=0, le=100)
    door: float = Field(default=0, ge=0, le=100)
    window: float = Field(default=0, ge=0, le=100)
    floor_finish: float = Field(default=10, ge=0, le=100)
    ceiling: float = Field(default=5, ge=0, le=100)
    skirting: float = Field(default=10, ge=0, le=100)
    internal_plaster: float = Field(default=5, ge=0, le=100)
    external_render: float = Field(default=5, ge=0, le=100)

    @field_validator("*", mode="before")
    @classmethod
    def numeric_percentages(cls, value: object) -> object:
        if isinstance(value, bool):
            raise ValueError("wastage percentages must be numbers, not booleans")
        return value


class OpeningSettings(BaseModel):
    """One rectangular opening measured along its host's inner reference line.

    A null offset keeps the opening centred when its host changes length.
    W1 runs east, W2 north, W3 west and W4 south from their inner corners.
    """

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, validate_assignment=True)

    id: str = Field(pattern=r"^[DN][1-9][0-9]{0,2}$")
    kind: Literal["door", "window"]
    host: str = Field(pattern=r"^(?:W[1-4]|P[1-9][0-9]{0,2})$")
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


class PartitionSettings(BaseModel):
    """An orthogonal centreline, in clear-shell coordinates from the SW datum."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, validate_assignment=True)

    id: str = Field(pattern=r"^P[1-9][0-9]{0,2}$")
    start_mm: tuple[float, float]
    end_mm: tuple[float, float]
    thickness_mm: float = Field(default=100, ge=50, le=500)
    height_mm: float = Field(default=3000, ge=300, le=5000)
    wall_type: str = Field(default="partition", min_length=1, max_length=80)

    @field_validator("start_mm", "end_mm", mode="before")
    @classmethod
    def numeric_points(cls, value: Any) -> Any:
        if not isinstance(value, (list, tuple)) or len(value) != 2 or any(isinstance(item, bool) for item in value):
            raise ValueError("partition points must contain two numeric coordinates")
        return value

    @field_validator("thickness_mm", "height_mm", mode="before")
    @classmethod
    def numeric_sizes(cls, value: Any) -> Any:
        if isinstance(value, bool):
            raise ValueError("partition sizes must be numbers, not booleans")
        return value

    @field_validator("wall_type")
    @classmethod
    def printable_type(cls, value: str) -> str:
        value = value.strip()
        if not value or any(ord(character) < 32 for character in value):
            raise ValueError("wall type must be non-empty single-line text")
        return value


class RoomFinishes(BaseModel):
    """Descriptive finish selections; no manufacturer price is inferred."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    floor: str | None = Field(default=None, min_length=1, max_length=120)
    ceiling: str | None = Field(default=None, min_length=1, max_length=120)
    skirting: str | None = Field(default=None, min_length=1, max_length=120)
    paint: str | None = Field(default=None, min_length=1, max_length=120)

    @field_validator("*", mode="before")
    @classmethod
    def printable_finish(cls, value: Any) -> Any:
        if value is None:
            return value
        if not isinstance(value, str) or not value.strip() or any(ord(character) < 32 for character in value):
            raise ValueError("room finishes must be non-empty single-line text or null")
        return value.strip()


class RoomSettings(BaseModel):
    """Names and finishes keyed to a derived topological room identity."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    id: str = Field(pattern=r"^ROOM(?:[1-9][0-9]*|_[A-F0-9]{12})$")
    name: str = Field(min_length=1, max_length=120)
    finishes: RoomFinishes = Field(default_factory=RoomFinishes)

    @field_validator("name")
    @classmethod
    def printable_name(cls, value: str) -> str:
        value = value.strip()
        if not value or any(ord(character) < 32 for character in value):
            raise ValueError("room name must be non-empty single-line text")
        return value


class HouseSettings(BaseModel):
    """A rectangular shell with optional internal partitions and named rooms.

    Length and width are clear INTERNAL dimensions. Opening starts use inner
    wall references or the partition's authored start. Leave 100 mm clear at
    each end, at the head, beside junctions and between openings on one host.
    """

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, validate_assignment=True)

    project_name: str = Field(default="House Sync demonstration", min_length=1, max_length=120)
    length_mm: float = Field(default=10000, ge=3000, le=30000)
    width_mm: float = Field(default=8000, ge=3000, le=30000)
    height_mm: float = Field(default=3000, ge=2400, le=5000)
    wall_thickness_mm: float = Field(default=200, ge=100, le=500)
    paint_colour: str = Field(default="Light blue", min_length=1, max_length=80)
    paint_hex: str = Field(default="#ADD8E6", pattern=r"^#[0-9A-Fa-f]{6}$")
    currency: str = Field(default="AUD", pattern=r"^[A-Z]{3}$")
    paint_coats: StrictInt = Field(default=2, ge=1, le=10)
    rate_overrides: dict[str, Annotated[float, Field(ge=0, le=1_000_000)]] = Field(default_factory=dict)
    wastage: WastageSettings = Field(default_factory=WastageSettings)
    rate_source: str = Field(default="Illustrative AUD rates, not a tender estimate; no supplier quotes", min_length=1, max_length=240)
    rate_date: date = Field(default=date(2026, 11, 17))
    openings: list[OpeningSettings] = Field(default_factory=default_openings, max_length=40)
    partitions: list[PartitionSettings] = Field(default_factory=list, max_length=40)
    rooms: list[RoomSettings] = Field(default_factory=list, max_length=200)
    min_room_width_mm: float = Field(default=900, ge=300, le=10000)
    builder_pricing: BuilderPricing = Field(default_factory=BuilderPricing)
    allowances: list[AllowanceSettings] = Field(default_factory=list, max_length=100)

    @field_validator("min_room_width_mm", mode="before")
    @classmethod
    def numeric_room_width(cls, value: Any) -> Any:
        if isinstance(value, bool):
            raise ValueError("minimum room width must be a number, not a boolean")
        return value

    @model_validator(mode="after")
    def unique_allowances(self) -> Self:
        identities = [allowance.id for allowance in self.allowances]
        if len(identities) != len(set(identities)):
            raise ValueError("allowance IDs must be unique")
        return self

    @model_validator(mode="before")
    @classmethod
    def migrate_legacy_rates(cls, value: Any) -> Any:
        """Pin supplied historical prices without pinning current library defaults.

        The presence of the new key is authoritative, including an empty map:
        stale legacy ``rates`` then has no effect. Canonical dumps contain only
        overrides, so new settings continue to follow library edits on reload.
        """
        if not isinstance(value, dict):
            return value
        values = dict(value)
        legacy = values.pop("rates", None)
        if "rate_overrides" not in values and "rates" in value:
            if not isinstance(legacy, dict):
                raise ValueError("legacy rates must be an object")
            legacy_keys = {legacy_key for _, legacy_key in BOQ_CODES.values()}
            unknown = set(legacy) - legacy_keys
            if unknown:
                raise ValueError(f"unknown legacy rate keys: {', '.join(sorted(unknown))}")
            values["rate_overrides"] = {
                code: legacy[legacy_key] for code, (_, legacy_key) in BOQ_CODES.items() if legacy_key in legacy
            }
        return values

    @field_validator("rate_overrides", mode="before")
    @classmethod
    def valid_rate_overrides(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            raise ValueError("rate_overrides must be an object keyed by BOQ code")
        unknown = set(value) - BOQ_CODES.keys()
        if unknown:
            raise ValueError(f"unknown rate override codes: {', '.join(sorted(str(code) for code in unknown))}")
        if any(isinstance(rate, bool) for rate in value.values()):
            raise ValueError("rate overrides must be numbers, not booleans")
        return value

    @field_validator("project_name", "paint_colour", "rate_source")
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
        if not self.partitions and self.min_room_width_mm > min(self.length_mm, self.width_mm):
            raise ValueError("Clear room width is below the selected minimum room width")
        if self.partitions or self.rooms:
            from .layout import derive_layout

            derive_layout(self, validate=True)
            return self
        seen: set[str] = set()
        intervals: dict[str, list[tuple[float, float, str]]] = {}
        for opening in self.openings:
            if opening.id in seen:
                raise ValueError(f"Duplicate opening ID {opening.id}")
            seen.add(opening.id)
            if opening.host not in {"W1", "W2", "W3", "W4"}:
                raise ValueError(f"{opening.id} references unknown host {opening.host}")
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
