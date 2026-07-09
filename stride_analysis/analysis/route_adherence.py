"""Route adherence: how far do actual GPS pings stray from the planned route?

"Route deviation" (סטייה מהמסלול) is **not** a field the Open Bus Stride API
computes — it must be derived by comparing real-time vehicle positions
(``siri_vehicle_locations``) to the planned route geometry.

This module derives an **approximate** per-line signal that does not depend on the
GTFS↔SIRI per-ride match (which lags 1–3 days):

  1. Build a planned poly-line per ``(line_ref, direction, alternative)`` from the
     ordered planned stops (``gtfs_ride_stops`` → ``gtfs_stop__lat/lon``).
  2. For each GPS ping of that line, compute the minimum distance to *any* of the
     line's planned variants (so we never need to know which direction a vehicle
     was driving).
  3. Summarise the distance distribution per line / per vehicle and flag pings
     beyond a distance threshold as candidate off-route points.

**Known limitations (read before quoting numbers):**
  * The planned poly-line connects *stops* with straight chords, not the true road
    shape. On intercity segments with far-apart stops this overstates distance on
    curves — so small/medium distances are not reliable evidence of deviation.
    Treat only large, sustained distances (e.g. > 1 km) as likely real.
  * GPS noise, wrong-line tagging, and deadheading also produce large distances.
  * Without the per-ride match we cannot attribute a deviation to a specific
    scheduled ride/driver — only to a vehicle_ref + line.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd

from stride_analysis import config
from stride_analysis.data.stride_client import StrideClient

_logger = config.get_logger("stride_analysis.route_adherence")

# Local equirectangular projection origin (central Israel) — good to ~city scale.
_LAT0 = 31.7
_LON0 = 35.0

# Valid coordinate bounding box for Israel (drops null-island / corrupt GPS).
_LAT_MIN, _LAT_MAX = 29.3, 33.45
_LON_MIN, _LON_MAX = 34.1, 35.95


def valid_il_coords(df: pd.DataFrame, *, lat_col: str = "lat", lon_col: str = "lon") -> pd.Series:
    """Boolean mask of rows whose lat/lon fall inside Israel's bounding box."""
    lat = pd.to_numeric(df[lat_col], errors="coerce")
    lon = pd.to_numeric(df[lon_col], errors="coerce")
    return (
        lat.between(_LAT_MIN, _LAT_MAX)
        & lon.between(_LON_MIN, _LON_MAX)
    )
_M_PER_DEG_LAT = 110_540.0
_M_PER_DEG_LON = 111_320.0 * math.cos(math.radians(_LAT0))


def _to_xy(lat: np.ndarray, lon: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Project lat/lon arrays to local metres (planar, central-Israel origin)."""
    x = (np.asarray(lon, dtype=float) - _LON0) * _M_PER_DEG_LON
    y = (np.asarray(lat, dtype=float) - _LAT0) * _M_PER_DEG_LAT
    return x, y


def _point_segment_dist(px: np.ndarray, py: np.ndarray, ax: float, ay: float, bx: float, by: float) -> np.ndarray:
    """Vectorised distance from points (px,py) to segment A→B, in metres."""
    abx, aby = bx - ax, by - ay
    denom = abx * abx + aby * aby
    if denom == 0.0:
        return np.hypot(px - ax, py - ay)
    t = ((px - ax) * abx + (py - ay) * aby) / denom
    t = np.clip(t, 0.0, 1.0)
    cx, cy = ax + t * abx, ay + t * aby
    return np.hypot(px - cx, py - cy)


def _min_dist_to_polyline(px: np.ndarray, py: np.ndarray, poly: list[tuple[float, float]]) -> np.ndarray:
    """Min distance from each point to a poly-line given as [(x,y), ...] metres."""
    if len(poly) == 1:
        return np.hypot(px - poly[0][0], py - poly[0][1])
    best = np.full(px.shape, np.inf)
    for (ax, ay), (bx, by) in zip(poly[:-1], poly[1:]):
        best = np.minimum(best, _point_segment_dist(px, py, ax, ay, bx, by))
    return best


@dataclass
class RouteAdherenceResult:
    """Per-line and per-vehicle adherence summary plus the flagged pings."""

    per_line: pd.DataFrame
    per_vehicle: pd.DataFrame
    flagged: pd.DataFrame
    threshold_m: float
    coverage: dict[str, object]


def build_planned_polylines(
    client: StrideClient,
    *,
    line_ref: int | str,
    date: str,
    max_stops: int = 4000,
) -> list[list[tuple[float, float]]]:
    """Return planned poly-lines (in local metres) for a line on a date.

    One poly-line per ``(direction, alternative)`` variant, built from the ordered
    planned stops of a representative journey for that variant.
    """
    rows = client.get_df(
        config.ENDPOINT_GTFS_RIDE_STOPS if hasattr(config, "ENDPOINT_GTFS_RIDE_STOPS")
        else "/gtfs_ride_stops/list",
        gtfs_route__line_refs=str(line_ref),
        gtfs_route__date_from=date,
        gtfs_route__date_to=date,
        arrival_time_from=f"{date}T00:00:00+00:00",
        arrival_time_to=f"{date}T23:59:59+00:00",
        limit=max_stops,
    )
    if rows.empty or "gtfs_stop__lat" not in rows.columns:
        return []
    rows = rows.dropna(subset=["gtfs_stop__lat", "gtfs_stop__lon"])
    polylines: list[list[tuple[float, float]]] = []
    group_keys = [c for c in ("gtfs_route__route_direction", "gtfs_route__route_alternative") if c in rows.columns]
    grouped = rows.groupby(group_keys) if group_keys else [(None, rows)]
    for _, variant in grouped:
        # Use one representative journey to avoid duplicate overlapping sequences.
        ride_col = "gtfs_ride_id" if "gtfs_ride_id" in variant.columns else None
        if ride_col:
            first_ride = variant[ride_col].iloc[0]
            variant = variant[variant[ride_col] == first_ride]
        variant = variant.sort_values("stop_sequence") if "stop_sequence" in variant.columns else variant
        x, y = _to_xy(variant["gtfs_stop__lat"].to_numpy(), variant["gtfs_stop__lon"].to_numpy())
        poly = list(zip(x.tolist(), y.tolist()))
        if len(poly) >= 2:
            polylines.append(poly)
    return polylines


def analyze_route_adherence(
    gps: pd.DataFrame,
    client: StrideClient,
    *,
    date: str,
    threshold_m: float = 250.0,
    severe_m: float = 1000.0,
    top_n_lines: int | None = None,
    in_service_only: bool = False,
    line_col: str = "siri_route__line_ref",
    vehicle_col: str = "siri_ride__vehicle_ref",
    journey_dist_col: str = "distance_from_journey_start",
) -> RouteAdherenceResult:
    """Compute approximate route-deviation stats from GPS pings.

    Args:
        gps: ``siri_vehicle_locations`` rows (need ``lat``, ``lon``, ``line_col``).
        client: Used to fetch planned stops per line.
        date: Service date (``YYYY-MM-DD``) for the planned geometry.
        threshold_m: Distance above which a ping is "off the planned chord".
        severe_m: Distance above which a ping is a likely *real* deviation.
        top_n_lines: If set, analyse only the N busiest lines (by ping count).

    Returns:
        A :class:`RouteAdherenceResult`.
    """
    gps = gps.dropna(subset=["lat", "lon", line_col]).copy()
    # Data quality: drop corrupt / out-of-country coordinates before any KPI.
    n_before = len(gps)
    gps = gps[valid_il_coords(gps)].copy()
    n_invalid = n_before - len(gps)
    if n_invalid:
        _logger.warning("dropped %d/%d pings with invalid coordinates", n_invalid, n_before)

    # In-service filter: keep only pings that have progressed along the journey,
    # removing pre-trip / depot / dead-head positioning (the main confounder).
    n_deadhead = 0
    if in_service_only and journey_dist_col in gps.columns:
        moved = pd.to_numeric(gps[journey_dist_col], errors="coerce") > 0
        n_deadhead = int((~moved).sum())
        gps = gps[moved].copy()
        _logger.info("in-service filter kept %d, dropped %d non-progressing pings", len(gps), n_deadhead)

    line_counts = gps[line_col].value_counts()
    lines = list(line_counts.index)
    if top_n_lines:
        lines = lines[:top_n_lines]

    px_all, py_all = _to_xy(gps["lat"].to_numpy(), gps["lon"].to_numpy())
    gps["_x"], gps["_y"] = px_all, py_all
    gps["dist_m"] = np.nan

    analysed_lines, skipped_lines = [], []
    for line_ref in lines:
        polys = build_planned_polylines(client, line_ref=line_ref, date=date)
        mask = gps[line_col] == line_ref
        if not polys:
            skipped_lines.append(int(line_ref))
            continue
        sub = gps.loc[mask]
        dmin = np.full(len(sub), np.inf)
        for poly in polys:
            dmin = np.minimum(dmin, _min_dist_to_polyline(sub["_x"].to_numpy(), sub["_y"].to_numpy(), poly))
        gps.loc[mask, "dist_m"] = dmin
        analysed_lines.append(int(line_ref))

    scored = gps.dropna(subset=["dist_m"]).copy()
    scored["off_route"] = scored["dist_m"] > threshold_m
    scored["severe"] = scored["dist_m"] > severe_m

    per_line = (
        scored.groupby(line_col)
        .agg(
            pings=("dist_m", "size"),
            median_dist_m=("dist_m", "median"),
            p90_dist_m=("dist_m", lambda s: float(np.percentile(s, 90))),
            p95_dist_m=("dist_m", lambda s: float(np.percentile(s, 95))),
            max_dist_m=("dist_m", "max"),
            off_route_pct=("off_route", lambda s: round(100.0 * s.mean(), 1)),
            severe_pct=("severe", lambda s: round(100.0 * s.mean(), 1)),
        )
        .reset_index()
        .sort_values("severe_pct", ascending=False)
        .reset_index(drop=True)
    )
    for col in ("median_dist_m", "p90_dist_m", "p95_dist_m", "max_dist_m"):
        per_line[col] = per_line[col].round(0)

    vehicle_group = [vehicle_col, line_col] if vehicle_col in scored.columns else [line_col]
    per_vehicle = (
        scored.groupby(vehicle_group)
        .agg(
            pings=("dist_m", "size"),
            median_dist_m=("dist_m", "median"),
            max_dist_m=("dist_m", "max"),
            severe_pct=("severe", lambda s: round(100.0 * s.mean(), 1)),
        )
        .reset_index()
        .sort_values(["severe_pct", "max_dist_m"], ascending=False)
        .reset_index(drop=True)
    )
    per_vehicle["median_dist_m"] = per_vehicle["median_dist_m"].round(0)
    per_vehicle["max_dist_m"] = per_vehicle["max_dist_m"].round(0)

    flagged = scored[scored["severe"]].copy()

    coverage = {
        "lines_analysed": len(analysed_lines),
        "lines_skipped_no_plan": len(skipped_lines),
        "pings_invalid_coords": int(n_invalid),
        "pings_deadhead_dropped": int(n_deadhead),
        "pings_scored": int(len(scored)),
        "pings_total_valid": int(len(gps)),
        "vehicles": int(scored[vehicle_col].nunique()) if vehicle_col in scored.columns else None,
        "skipped_line_refs_sample": skipped_lines[:10],
    }
    _logger.info("route adherence: %s", coverage)
    return RouteAdherenceResult(
        per_line=per_line,
        per_vehicle=per_vehicle,
        flagged=flagged,
        threshold_m=threshold_m,
        coverage=coverage,
    )
