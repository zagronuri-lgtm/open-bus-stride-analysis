"""Stop adherence: did the vehicle actually serve each scheduled station?

Maps to the penalty category *"אי עצירה בתחנה מתוכננת"* (failing to stop at a
planned station). The Open Bus 2026 data does **not** populate the GTFS↔SIRI stop
match, so we bridge it ourselves:

  * ``siri_ride_stops`` gives each ride's ordered scheduled stops with the official
    MOT stop ``code`` (``siri_stop__code``) and ``order`` — both populated.
  * ``gtfs_stops`` resolves a stop ``code`` → planned ``lat/lon``.
  * ``siri_vehicle_locations`` (filtered by ``siri_rides__ids``) gives the ride's
    **full** GPS trace.

For each scheduled stop we compute the closest the vehicle's trace came to it.

**Robust skip detection.** Raw "closest approach" is confounded by (a) GPS
sparsity — a fast bus may have no ping exactly at a stop even though it passed —
and (b) trace truncation at the ends of the captured journey. We therefore only
declare a stop *skipped* when it is **bracketed by served stops** (the bus
demonstrably traversed that segment) yet never came within ``skip_m``. End stops
that are merely unobserved are reported as ``unobserved``, not skipped.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd

from stride_analysis import config
from stride_analysis.data.stride_client import StrideClient

_logger = config.get_logger("stride_analysis.stop_adherence")

_EARTH_R = 6_371_000.0


def _haversine_m(lat1: np.ndarray, lon1: np.ndarray, lat2: float, lon2: float) -> np.ndarray:
    """Vectorised haversine distance (metres) from many points to one point."""
    p = math.pi / 180.0
    lat1 = np.asarray(lat1, dtype=float) * p
    lon1 = np.asarray(lon1, dtype=float) * p
    lat2 *= p
    lon2 *= p
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = np.sin(dlat / 2) ** 2 + np.cos(lat1) * math.cos(lat2) * np.sin(dlon / 2) ** 2
    return 2 * _EARTH_R * np.arcsin(np.minimum(1.0, np.sqrt(a)))


@dataclass
class StopAdherenceResult:
    """Per-ride and per-stop stop-service results."""

    per_ride: pd.DataFrame      # one row per analysed ride
    per_stop: pd.DataFrame      # one row per (ride, scheduled stop)
    skipped: pd.DataFrame       # bracketed stops the vehicle clearly skipped
    coverage: dict[str, object]


def resolve_stop_coords(
    client: StrideClient, codes: list[int], *, date: str
) -> dict[int, tuple[float, float]]:
    """Resolve MOT stop codes → planned ``(lat, lon)`` via ``gtfs_stops``.

    Results are cached by the client, so shared stops across rides cost one call.
    """
    coords: dict[int, tuple[float, float]] = {}
    for code in codes:
        rows = client.get_json(
            "/gtfs_stops/list", code=str(code), date_from=date, date_to=date, limit=1
        )
        if rows and rows[0].get("lat") is not None:
            coords[int(code)] = (float(rows[0]["lat"]), float(rows[0]["lon"]))
    return coords


def analyze_stop_adherence(
    client: StrideClient,
    ride_ids: list[int],
    *,
    date: str,
    served_m: float = 150.0,
    skip_m: float = 250.0,
    min_pings: int = 8,
) -> StopAdherenceResult:
    """Assess scheduled-stop service for a sample of SIRI rides.

    Args:
        client: Stride client.
        ride_ids: ``siri_ride`` ids to analyse (one journey each).
        date: Service date (``YYYY-MM-DD``) for stop-coordinate resolution.
        served_m: A stop is "served" if the trace came within this distance.
        skip_m: A *bracketed* stop with closest approach beyond this is "skipped".
        min_pings: Skip rides with fewer GPS points than this (too sparse).

    Returns:
        A :class:`StopAdherenceResult`.
    """
    per_ride_rows: list[dict] = []
    per_stop_frames: list[pd.DataFrame] = []
    n_no_stops = n_too_sparse = 0

    # First pass: pull each ride's stops to gather every code, resolve coords once.
    ride_stops: dict[int, pd.DataFrame] = {}
    all_codes: set[int] = set()
    for rid in ride_ids:
        rs = pd.DataFrame(
            client.get_json("/siri_ride_stops/list", siri_ride_ids=str(rid), limit=400)
        )
        if rs.empty or "siri_stop__code" not in rs.columns:
            n_no_stops += 1
            continue
        rs = rs.dropna(subset=["siri_stop__code", "order"]).copy()
        rs["siri_stop__code"] = rs["siri_stop__code"].astype(int)
        rs["order"] = rs["order"].astype(int)
        ride_stops[rid] = rs.sort_values("order")
        all_codes.update(rs["siri_stop__code"].tolist())

    coords = resolve_stop_coords(client, sorted(all_codes), date=date)
    _logger.info("resolved %d/%d stop codes", len(coords), len(all_codes))

    # Second pass: each ride's full GPS trace, per-stop closest approach.
    for rid, rs in ride_stops.items():
        gps = pd.DataFrame(
            client.get_json(
                "/siri_vehicle_locations/list", siri_rides__ids=str(rid), limit=15000
            )
        )
        gps = gps.dropna(subset=["lat", "lon"]) if not gps.empty else gps
        if gps.empty or len(gps) < min_pings:
            n_too_sparse += 1
            continue
        plat = gps["lat"].to_numpy()
        plon = gps["lon"].to_numpy()
        line_ref = gps["siri_route__line_ref"].dropna().iloc[0] if "siri_route__line_ref" in gps else None
        vehicle = gps["siri_ride__vehicle_ref"].dropna().iloc[0] if "siri_ride__vehicle_ref" in gps else None

        rows = []
        for _, s in rs.iterrows():
            code = int(s["siri_stop__code"])
            if code not in coords:
                continue
            slat, slon = coords[code]
            dmin = float(np.min(_haversine_m(plat, plon, slat, slon)))
            rows.append({"order": int(s["order"]), "stop_code": code, "closest_m": round(dmin)})
        if not rows:
            continue
        stop_df = pd.DataFrame(rows).sort_values("order").reset_index(drop=True)
        stop_df["served"] = stop_df["closest_m"] <= served_m

        # Bracketed-skip detection: unserved stop strictly between two served ones.
        served_idx = stop_df.index[stop_df["served"]].tolist()
        if served_idx:
            first_s, last_s = served_idx[0], served_idx[-1]
        else:
            first_s, last_s = -1, -1
        bracket = (stop_df.index > first_s) & (stop_df.index < last_s)
        stop_df["in_service_span"] = bracket | stop_df["served"]
        stop_df["skipped"] = (~stop_df["served"]) & bracket & (stop_df["closest_m"] > skip_m)
        stop_df["unobserved"] = (~stop_df["served"]) & (~bracket)
        stop_df["ride_id"] = rid
        stop_df["line_ref"] = line_ref
        stop_df["vehicle_ref"] = vehicle
        per_stop_frames.append(stop_df)

        span = int(stop_df["in_service_span"].sum())
        served = int(stop_df["served"].sum())
        skipped = int(stop_df["skipped"].sum())
        per_ride_rows.append(
            {
                "ride_id": rid,
                "line_ref": line_ref,
                "vehicle_ref": vehicle,
                "pings": int(len(gps)),
                "scheduled_stops": int(len(stop_df)),
                "stops_in_span": span,
                "served": served,
                "skipped": skipped,
                "unobserved": int(stop_df["unobserved"].sum()),
                "serve_rate_pct": round(100.0 * served / span, 1) if span else None,
            }
        )

    per_ride = pd.DataFrame(per_ride_rows)
    per_stop = (
        pd.concat(per_stop_frames, ignore_index=True) if per_stop_frames else pd.DataFrame()
    )
    skipped = (
        per_stop[per_stop["skipped"]].copy() if not per_stop.empty else pd.DataFrame()
    )
    coverage = {
        "rides_requested": len(ride_ids),
        "rides_analysed": int(len(per_ride)),
        "rides_no_stops": n_no_stops,
        "rides_too_sparse": n_too_sparse,
        "stop_codes_resolved": len(coords),
        "stop_codes_total": len(all_codes),
        "served_m": served_m,
        "skip_m": skip_m,
    }
    _logger.info("stop adherence: %s", coverage)
    return StopAdherenceResult(per_ride=per_ride, per_stop=per_stop, skipped=skipped, coverage=coverage)
