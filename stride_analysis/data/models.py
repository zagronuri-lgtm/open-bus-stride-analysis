"""Domain dataclasses for transit entities.

These are lightweight, serializable views over the Stride API payloads. They are
deliberately permissive: ``from_api`` constructors tolerate missing keys so a
schema change on one field never crashes a whole analysis run.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


def _to_int(value: Any) -> int | None:
    """Best-effort int conversion that returns ``None`` instead of raising."""
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True)
class Operator:
    """A transit operator (agency), e.g. Dan, Egged, Israel Railways.

    ``operator_ref`` is the stable numeric key used across the API; ``name`` is
    the human-readable agency name (usually Hebrew). Never hard-code these — they
    are discovered via :meth:`StrideClient.list_operators`.
    """

    operator_ref: int
    name: str
    route_count: int | None = None

    @classmethod
    def from_api(cls, row: dict[str, Any], route_count: int | None = None) -> "Operator":
        """Build from a ``/gtfs_routes/list`` row."""
        return cls(
            operator_ref=_to_int(row.get("operator_ref")) or 0,
            name=str(row.get("agency_name") or row.get("operator_ref") or "").strip(),
            route_count=route_count,
        )


@dataclass(frozen=True)
class Route:
    """A GTFS route (a public line number, in one direction/alternative)."""

    line_ref: int | None
    operator_ref: int | None
    route_short_name: str | None
    route_long_name: str | None = None
    route_mkt: str | None = None
    route_direction: str | None = None
    route_alternative: str | None = None
    agency_name: str | None = None
    route_type: str | None = None
    date: str | None = None

    @classmethod
    def from_api(cls, row: dict[str, Any]) -> "Route":
        """Build from a ``/gtfs_routes/list`` row."""
        return cls(
            line_ref=_to_int(row.get("line_ref")),
            operator_ref=_to_int(row.get("operator_ref")),
            route_short_name=_str_or_none(row.get("route_short_name")),
            route_long_name=_str_or_none(row.get("route_long_name")),
            route_mkt=_str_or_none(row.get("route_mkt")),
            route_direction=_str_or_none(row.get("route_direction")),
            route_alternative=_str_or_none(row.get("route_alternative")),
            agency_name=_str_or_none(row.get("agency_name")),
            route_type=_str_or_none(row.get("route_type")),
            date=_str_or_none(row.get("date")),
        )


@dataclass(frozen=True)
class Ride:
    """A single planned ride and whether it was executed.

    Sourced from ``/rides_execution/list``. ``actual_start_time`` being present
    means the planned ride was matched to a real-time (SIRI) ride, i.e. executed.
    """

    operator_ref: int | None
    line_ref: int | None
    planned_start_time: str | None
    actual_start_time: str | None = None
    gtfs_ride_id: int | None = None
    siri_ride_id: int | None = None

    @property
    def executed(self) -> bool:
        """True when the ride was matched to a real-time ride."""
        return bool(self.actual_start_time)

    @classmethod
    def from_api(cls, row: dict[str, Any]) -> "Ride":
        """Build from a ``/rides_execution/list`` row."""
        return cls(
            operator_ref=_to_int(row.get("operator_ref")),
            line_ref=_to_int(row.get("line_ref")),
            planned_start_time=_str_or_none(row.get("planned_start_time")),
            actual_start_time=_str_or_none(row.get("actual_start_time")),
            gtfs_ride_id=_to_int(row.get("gtfs_ride_id")),
            siri_ride_id=_to_int(row.get("siri_ride_id")),
        )


@dataclass
class ExecutionSummary:
    """Aggregated planned-vs-actual KPIs for one slice (operator/day/line/…)."""

    planned: int = 0
    executed: int = 0
    missing: int = 0
    missing_pct: float = 0.0
    # Optional dimension labels describing the slice this summary covers.
    dimensions: dict[str, Any] = field(default_factory=dict)
    # Optional punctuality KPIs, when SIRI data is available.
    late_pct: float | None = None
    avg_delay_min: float | None = None
    # Optional monetary penalty for this slice (NIS).
    penalty_nis: float | None = None

    def to_dict(self) -> dict[str, Any]:
        """Flatten dimensions and metrics into a single record."""
        record = {k: v for k, v in asdict(self).items() if k != "dimensions"}
        return {**self.dimensions, **record}


def _str_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
