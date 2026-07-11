"""Compare an Optibus map export against the GTFS baseline for a target service date.

The module loads a map export directory (trips.csv / routes.csv / deadheads.csv),
fetches the operator's GTFS routes and rides for the target date from the Open Bus
Stride API, and produces:

1. Coverage matrix per route_mkt x direction x alternative (map vs GTFS counts).
2. Minute-by-minute departure matching (UTC converted to Israel local time via
   ``zoneinfo`` — correct for both summer and winter clocks, no hard-coded offset).
3. Travel-time gap report (map duration vs GTFS scheduled duration).
4. Stale snapshot detection: map route_mkts missing from GTFS on the target date,
   and new GTFS route_mkts missing from the map (with endpoint-name similarity
   against map routes, based on route_long_name).
5. Deadhead catalog sanity checks: zero-time legs, speed distribution, and
   pull-in / pull-out asymmetry per depot.

Outputs are CSV files plus a JSON summary. All comparison functions are pure
(operate on DataFrames), so they can be tested offline with small fixtures.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Sequence
from zoneinfo import ZoneInfo

import pandas as pd

from src.open_bus_stride_client import OpenBusStrideClient

ISRAEL_TZ = ZoneInfo("Asia/Jerusalem")

MAP_TRIPS_REQUIRED_COLUMNS = {
    "trip_id",
    "route_id",
    "mkt",
    "direction",
    "alternative",
    "dep_min",
    "duration_min",
}
MAP_ROUTES_REQUIRED_COLUMNS = {"route_id", "mkt", "direction", "alternative", "origin_name", "dest_name"}
DEADHEADS_REQUIRED_COLUMNS = {
    "origin_stop_id",
    "dest_stop_id",
    "type",
    "travel_time_min",
    "distance_km",
}

DEFAULT_SPEED_WARNING_KMH = 90.0


@dataclass(frozen=True)
class MapVsGtfsRequest:
    """Input parameters for a map-vs-GTFS baseline comparison."""

    map_export_dir: str | Path
    service_date: str
    operator_ref: str | int = 34
    match_tolerance_min: int = 0
    assigned_only: bool = False
    speed_warning_kmh: float = DEFAULT_SPEED_WARNING_KMH
    output_dir: str | Path = "outputs"


@dataclass
class MapExport:
    """Loaded map export tables."""

    trips: pd.DataFrame
    routes: pd.DataFrame
    deadheads: pd.DataFrame
    source_dir: Path
    warnings: list[str] = field(default_factory=list)


def load_map_export(map_export_dir: str | Path, *, assigned_only: bool = False) -> MapExport:
    """Load and validate trips.csv / routes.csv / deadheads.csv from a map export dir."""
    map_dir = Path(map_export_dir)
    if not map_dir.is_dir():
        raise FileNotFoundError(f"Map export directory not found: {map_dir}")

    warnings: list[str] = []
    trips = _read_required_csv(map_dir / "trips.csv", MAP_TRIPS_REQUIRED_COLUMNS)
    routes = _read_required_csv(map_dir / "routes.csv", MAP_ROUTES_REQUIRED_COLUMNS)
    deadheads = _read_required_csv(map_dir / "deadheads.csv", DEADHEADS_REQUIRED_COLUMNS)

    trips = trips.copy()
    trips["route_key"] = _route_key(trips)
    trips["dep_min"] = pd.to_numeric(trips["dep_min"], errors="coerce")
    trips["duration_min"] = pd.to_numeric(trips["duration_min"], errors="coerce")
    invalid_dep = int(trips["dep_min"].isna().sum())
    if invalid_dep:
        warnings.append(f"{invalid_dep} map trips have invalid dep_min and were dropped")
        trips = trips[trips["dep_min"].notna()].copy()

    if assigned_only and "assigned" in trips.columns:
        before = len(trips)
        assigned_mask = trips["assigned"].astype(str).str.strip().str.lower().isin({"true", "1", "yes"})
        trips = trips[assigned_mask].copy()
        warnings.append(f"assigned_only=True: kept {len(trips)}/{before} trips")

    routes = routes.copy()
    routes["route_key"] = _route_key(routes)

    return MapExport(trips=trips, routes=routes, deadheads=deadheads.copy(), source_dir=map_dir, warnings=warnings)


def fetch_gtfs_routes(
    client: OpenBusStrideClient,
    *,
    service_date: str,
    operator_ref: str | int,
) -> pd.DataFrame:
    """Fetch all GTFS routes of the operator for the target service date."""
    return client.get_df(
        "/gtfs_routes/list",
        date_from=service_date,
        date_to=service_date,
        operator_refs=str(operator_ref),
        order_by="id asc",
    )


def fetch_gtfs_rides(
    client: OpenBusStrideClient,
    *,
    service_date: str,
    operator_ref: str | int,
    page_size: int = 5000,
) -> pd.DataFrame:
    """Fetch all GTFS rides of the operator for the target service date (paginated)."""
    rows: list[dict[str, Any]] = []
    for page in client.paginated(
        "/gtfs_rides/list",
        page_size=page_size,
        gtfs_route__date_from=service_date,
        gtfs_route__date_to=service_date,
        gtfs_route__operator_refs=str(operator_ref),
        order_by="id asc",
    ):
        rows.extend(page)
    return pd.DataFrame(rows)


def gtfs_rides_to_local(gtfs_rides: pd.DataFrame, service_date: str) -> pd.DataFrame:
    """Convert GTFS ride UTC times to Israel local time and minutes-from-midnight.

    Uses ``zoneinfo`` (Asia/Jerusalem), so the UTC offset follows the actual DST
    rule for the target date (+3 in summer, +2 in winter) instead of a hard-coded
    shift. ``dep_min`` is measured from local midnight of ``service_date``, so an
    after-midnight ride yields a value above 1440 rather than a small number.
    """
    if gtfs_rides.empty:
        return pd.DataFrame(
            columns=["gtfs_ride_id", "route_key", "mkt", "direction", "alternative", "dep_local", "dep_min", "duration_min"]
        )

    midnight_local = datetime.strptime(service_date, "%Y-%m-%d").replace(tzinfo=ISRAEL_TZ)

    start = pd.to_datetime(gtfs_rides["start_time"], utc=True, errors="coerce")
    end = pd.to_datetime(gtfs_rides["end_time"], utc=True, errors="coerce")
    start_local = start.dt.tz_convert("Asia/Jerusalem")

    out = pd.DataFrame(
        {
            "gtfs_ride_id": gtfs_rides.get("id"),
            "journey_ref": gtfs_rides.get("journey_ref"),
            "mkt": gtfs_rides["gtfs_route__route_mkt"].astype(str),
            "direction": gtfs_rides["gtfs_route__route_direction"].astype(str),
            "alternative": gtfs_rides["gtfs_route__route_alternative"].astype(str),
            "route_long_name": gtfs_rides.get("gtfs_route__route_long_name"),
            "dep_local": start_local.dt.strftime("%H:%M"),
            "dep_min": ((start - pd.Timestamp(midnight_local)).dt.total_seconds() / 60).round().astype("Int64"),
            "duration_min": ((end - start).dt.total_seconds() / 60).round(1),
        }
    )
    out["route_key"] = out["mkt"] + "-" + out["direction"] + "-" + out["alternative"]
    dropped = int(out["dep_min"].isna().sum())
    if dropped:
        out = out[out["dep_min"].notna()].copy()
    out.attrs["dropped_invalid_start_time"] = dropped
    return out


def coverage_matrix(map_trips: pd.DataFrame, gtfs_local: pd.DataFrame) -> pd.DataFrame:
    """Per mkt x direction x alternative: map departures vs GTFS departures."""
    map_counts = (
        map_trips.assign(mkt=map_trips["mkt"].astype(str),
                         direction=map_trips["direction"].astype(str),
                         alternative=map_trips["alternative"].astype(str))
        .groupby(["mkt", "direction", "alternative"], dropna=False)
        .size()
        .rename("map_departures")
    )
    gtfs_counts = (
        gtfs_local.groupby(["mkt", "direction", "alternative"], dropna=False).size().rename("gtfs_departures")
        if not gtfs_local.empty
        else pd.Series(dtype="int64", name="gtfs_departures")
    )
    matrix = pd.concat([map_counts, gtfs_counts], axis=1).fillna(0).astype(int).reset_index()
    matrix["gap"] = matrix["map_departures"] - matrix["gtfs_departures"]
    matrix["status"] = "match"
    matrix.loc[matrix["gap"] != 0, "status"] = "count_mismatch"
    matrix.loc[matrix["map_departures"] == 0, "status"] = "gtfs_only"
    matrix.loc[matrix["gtfs_departures"] == 0, "status"] = "map_only"
    return matrix.sort_values(["mkt", "direction", "alternative"]).reset_index(drop=True)


def match_departures(
    map_trips: pd.DataFrame,
    gtfs_local: pd.DataFrame,
    *,
    tolerance_min: int = 0,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Match map departures to GTFS departures per route_key, minute by minute.

    Greedy nearest-first matching within ``tolerance_min`` (0 = exact minute).
    Returns (matched, map_only, gtfs_only) DataFrames.
    """
    matched_rows: list[dict[str, Any]] = []
    map_only_rows: list[dict[str, Any]] = []
    gtfs_only_rows: list[dict[str, Any]] = []

    map_by_key = {key: group for key, group in map_trips.groupby("route_key")}
    gtfs_by_key = (
        {key: group for key, group in gtfs_local.groupby("route_key")} if not gtfs_local.empty else {}
    )

    for route_key in sorted(set(map_by_key) | set(gtfs_by_key)):
        map_group = map_by_key.get(route_key)
        gtfs_group = gtfs_by_key.get(route_key)
        map_records = (
            map_group.sort_values("dep_min").to_dict(orient="records") if map_group is not None else []
        )
        gtfs_records = (
            gtfs_group.sort_values("dep_min").to_dict(orient="records") if gtfs_group is not None else []
        )
        used_gtfs: set[int] = set()

        for map_record in map_records:
            best_index: int | None = None
            best_delta: float | None = None
            for index, gtfs_record in enumerate(gtfs_records):
                if index in used_gtfs:
                    continue
                delta = abs(float(map_record["dep_min"]) - float(gtfs_record["dep_min"]))
                if delta > tolerance_min:
                    continue
                if best_delta is None or delta < best_delta:
                    best_index = index
                    best_delta = delta
            if best_index is None:
                map_only_rows.append(
                    {
                        "route_key": route_key,
                        "trip_id": map_record.get("trip_id"),
                        "dep_min": map_record["dep_min"],
                        "dep_local": _minutes_to_hhmm(map_record["dep_min"]),
                        "map_duration_min": map_record.get("duration_min"),
                    }
                )
                continue
            used_gtfs.add(best_index)
            gtfs_record = gtfs_records[best_index]
            matched_rows.append(
                {
                    "route_key": route_key,
                    "trip_id": map_record.get("trip_id"),
                    "gtfs_ride_id": gtfs_record.get("gtfs_ride_id"),
                    "map_dep_min": map_record["dep_min"],
                    "gtfs_dep_min": gtfs_record["dep_min"],
                    "dep_delta_min": float(map_record["dep_min"]) - float(gtfs_record["dep_min"]),
                    "dep_local": _minutes_to_hhmm(map_record["dep_min"]),
                    "map_duration_min": map_record.get("duration_min"),
                    "gtfs_duration_min": gtfs_record.get("duration_min"),
                }
            )

        for index, gtfs_record in enumerate(gtfs_records):
            if index in used_gtfs:
                continue
            gtfs_only_rows.append(
                {
                    "route_key": route_key,
                    "gtfs_ride_id": gtfs_record.get("gtfs_ride_id"),
                    "dep_min": gtfs_record["dep_min"],
                    "dep_local": gtfs_record.get("dep_local") or _minutes_to_hhmm(gtfs_record["dep_min"]),
                    "gtfs_duration_min": gtfs_record.get("duration_min"),
                }
            )

    matched = pd.DataFrame(matched_rows)
    map_only = pd.DataFrame(map_only_rows)
    gtfs_only = pd.DataFrame(gtfs_only_rows)
    return matched, map_only, gtfs_only


def travel_time_gaps(matched: pd.DataFrame) -> pd.DataFrame:
    """Per route_key travel-time gap report for matched departures (map minus GTFS)."""
    if matched.empty:
        return pd.DataFrame(
            columns=[
                "route_key",
                "matched_trips",
                "map_mean_min",
                "gtfs_mean_min",
                "mean_gap_min",
                "median_gap_min",
                "max_abs_gap_min",
            ]
        )
    data = matched.copy()
    data["map_duration_min"] = pd.to_numeric(data["map_duration_min"], errors="coerce")
    data["gtfs_duration_min"] = pd.to_numeric(data["gtfs_duration_min"], errors="coerce")
    data["gap_min"] = data["map_duration_min"] - data["gtfs_duration_min"]

    grouped = data.groupby("route_key")
    report = pd.DataFrame(
        {
            "matched_trips": grouped.size(),
            "map_mean_min": grouped["map_duration_min"].mean().round(1),
            "gtfs_mean_min": grouped["gtfs_duration_min"].mean().round(1),
            "mean_gap_min": grouped["gap_min"].mean().round(1),
            "median_gap_min": grouped["gap_min"].median().round(1),
            "max_abs_gap_min": grouped["gap_min"].apply(lambda s: float(s.abs().max())).round(1),
        }
    ).reset_index()
    return report.sort_values("mean_gap_min", ascending=False, key=lambda s: s.abs()).reset_index(drop=True)


def detect_stale_snapshot(
    map_routes: pd.DataFrame,
    gtfs_routes: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Detect map mkts missing from GTFS on the target date and new GTFS mkts.

    New GTFS mkts get a best-similarity map route candidate, comparing
    endpoint names extracted from ``route_long_name`` against the map route
    origin/destination names. This flags cases where an mkt was replaced but
    the line itself still exists (a stale map snapshot).
    """
    map_mkts = set(map_routes["mkt"].astype(str))
    gtfs = gtfs_routes.copy()
    if gtfs.empty:
        gtfs_mkts: set[str] = set()
    else:
        gtfs["route_mkt"] = gtfs["route_mkt"].astype(str)
        gtfs_mkts = set(gtfs["route_mkt"])

    missing_in_gtfs = sorted(map_mkts - gtfs_mkts)
    map_missing = (
        map_routes[map_routes["mkt"].astype(str).isin(missing_in_gtfs)]
        .loc[:, ["route_key", "mkt", "direction", "alternative", "origin_name", "dest_name"]]
        .copy()
        .reset_index(drop=True)
    )

    new_rows: list[dict[str, Any]] = []
    map_endpoint_labels = [
        (str(record["mkt"]), f"{record['origin_name']} - {record['dest_name']}")
        for record in map_routes.to_dict(orient="records")
    ]
    if not gtfs.empty:
        new_gtfs = gtfs[~gtfs["route_mkt"].isin(map_mkts)]
        for _, row in new_gtfs.drop_duplicates(subset=["route_mkt", "route_direction", "route_alternative"]).iterrows():
            gtfs_endpoints = _endpoints_from_long_name(str(row.get("route_long_name") or ""))
            best_mkt, best_score = None, 0.0
            for map_mkt, map_label in map_endpoint_labels:
                score = SequenceMatcher(None, gtfs_endpoints, map_label).ratio()
                if score > best_score:
                    best_mkt, best_score = map_mkt, score
            new_rows.append(
                {
                    "route_mkt": row["route_mkt"],
                    "route_direction": row.get("route_direction"),
                    "route_alternative": row.get("route_alternative"),
                    "route_short_name": row.get("route_short_name"),
                    "route_long_name": row.get("route_long_name"),
                    "best_map_mkt_by_endpoints": best_mkt,
                    "endpoint_similarity": round(best_score, 3),
                    "likely_replacement": bool(best_score >= 0.6),
                }
            )
    gtfs_new = pd.DataFrame(new_rows)
    if not gtfs_new.empty:
        gtfs_new = gtfs_new.sort_values("endpoint_similarity", ascending=False).reset_index(drop=True)
    return map_missing, gtfs_new


def deadhead_catalog_checks(
    deadheads: pd.DataFrame,
    *,
    speed_warning_kmh: float = DEFAULT_SPEED_WARNING_KMH,
) -> dict[str, Any]:
    """Sanity checks on deadhead legs: zero-time, speed distribution, pull asymmetry."""
    legs = deadheads.copy()
    legs["travel_time_min"] = pd.to_numeric(legs["travel_time_min"], errors="coerce")
    legs["distance_km"] = pd.to_numeric(legs["distance_km"], errors="coerce")

    zero_time = legs[(legs["travel_time_min"] <= 0) & (legs["distance_km"] > 0)].copy()

    movable = legs[(legs["travel_time_min"] > 0) & (legs["distance_km"] > 0)].copy()
    movable["speed_kmh"] = (movable["distance_km"] / (movable["travel_time_min"] / 60)).round(1)
    speeding = movable[movable["speed_kmh"] > speed_warning_kmh].copy()
    if movable.empty:
        speed_distribution: dict[str, float] = {}
    else:
        quantiles = movable["speed_kmh"].quantile([0, 0.25, 0.5, 0.75, 1.0])
        speed_distribution = {
            "min": float(quantiles.iloc[0]),
            "p25": float(quantiles.iloc[1]),
            "median": float(quantiles.iloc[2]),
            "p75": float(quantiles.iloc[3]),
            "max": float(quantiles.iloc[4]),
            "mean": round(float(movable["speed_kmh"].mean()), 1),
        }

    asymmetry = _pull_asymmetry(legs)

    return {
        "legs_total": int(len(legs)),
        "zero_time_legs": zero_time.reset_index(drop=True),
        "zero_time_count": int(len(zero_time)),
        "speed_distribution_kmh": speed_distribution,
        "speeding_legs": speeding.reset_index(drop=True),
        "speeding_count": int(len(speeding)),
        "speed_warning_kmh": float(speed_warning_kmh),
        "pull_asymmetry": asymmetry,
        "pull_asymmetry_count": int(len(asymmetry)),
    }


def run_analysis(
    request: MapVsGtfsRequest,
    *,
    client: OpenBusStrideClient | None = None,
    gtfs_routes: pd.DataFrame | None = None,
    gtfs_rides: pd.DataFrame | None = None,
) -> dict[str, Any]:
    """Run the full comparison and persist CSV outputs + a JSON summary.

    ``gtfs_routes`` / ``gtfs_rides`` may be injected (offline mode / tests);
    otherwise they are fetched from the Stride API using ``client``.
    """
    _validate_request(request)
    export = load_map_export(request.map_export_dir, assigned_only=request.assigned_only)

    if gtfs_routes is None or gtfs_rides is None:
        if client is None:
            client = OpenBusStrideClient()
        if gtfs_routes is None:
            gtfs_routes = fetch_gtfs_routes(
                client, service_date=request.service_date, operator_ref=request.operator_ref
            )
        if gtfs_rides is None:
            gtfs_rides = fetch_gtfs_rides(
                client, service_date=request.service_date, operator_ref=request.operator_ref
            )

    gtfs_local = gtfs_rides_to_local(gtfs_rides, request.service_date)
    map_mkts = set(export.trips["mkt"].astype(str))
    gtfs_local_in_scope = gtfs_local[gtfs_local["mkt"].isin(map_mkts)].copy()

    matrix = coverage_matrix(export.trips, gtfs_local_in_scope)
    matched, map_only, gtfs_only = match_departures(
        export.trips, gtfs_local_in_scope, tolerance_min=request.match_tolerance_min
    )
    gaps = travel_time_gaps(matched)
    map_missing, gtfs_new = detect_stale_snapshot(export.routes, gtfs_routes)
    deadhead_checks = deadhead_catalog_checks(export.deadheads, speed_warning_kmh=request.speed_warning_kmh)

    out_dir = Path(request.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    base = f"map_vs_gtfs_{request.service_date}_op_{request.operator_ref}"

    csv_outputs = {
        "coverage_matrix": matrix,
        "matched_departures": matched,
        "map_only_departures": map_only,
        "gtfs_only_departures": gtfs_only,
        "travel_time_gaps": gaps,
        "stale_map_mkts": map_missing,
        "new_gtfs_mkts": gtfs_new,
        "deadhead_zero_time_legs": deadhead_checks["zero_time_legs"],
        "deadhead_speeding_legs": deadhead_checks["speeding_legs"],
        "deadhead_pull_asymmetry": deadhead_checks["pull_asymmetry"],
    }
    csv_paths: dict[str, str] = {}
    for name, frame in csv_outputs.items():
        path = out_dir / f"{base}_{name}.csv"
        frame.to_csv(path, index=False)
        csv_paths[name] = str(path)

    total_map = int(len(export.trips))
    total_matched = int(len(matched))
    summary: dict[str, Any] = {
        "service_date": request.service_date,
        "operator_ref": str(request.operator_ref),
        "map_export_dir": str(export.source_dir),
        "match_tolerance_min": request.match_tolerance_min,
        "assigned_only": request.assigned_only,
        "map_trips": total_map,
        "map_route_keys": int(export.trips["route_key"].nunique()),
        "gtfs_rides_operator_total": int(len(gtfs_local)),
        "gtfs_rides_in_map_scope": int(len(gtfs_local_in_scope)),
        "matched_departures": total_matched,
        "map_only_departures": int(len(map_only)),
        "gtfs_only_departures": int(len(gtfs_only)),
        "departure_match_rate": round(total_matched / total_map, 4) if total_map else None,
        "mean_travel_time_gap_min": (
            round(float((matched["map_duration_min"] - matched["gtfs_duration_min"]).mean()), 1)
            if total_matched
            else None
        ),
        "stale_map_mkts": sorted(map_missing["mkt"].astype(str).unique().tolist()),
        "new_gtfs_mkts": sorted(gtfs_new["route_mkt"].astype(str).unique().tolist()) if not gtfs_new.empty else [],
        "deadhead_checks": {
            "legs_total": deadhead_checks["legs_total"],
            "zero_time_count": deadhead_checks["zero_time_count"],
            "speed_distribution_kmh": deadhead_checks["speed_distribution_kmh"],
            "speeding_count": deadhead_checks["speeding_count"],
            "speed_warning_kmh": deadhead_checks["speed_warning_kmh"],
            "pull_asymmetry_count": deadhead_checks["pull_asymmetry_count"],
        },
        "load_warnings": export.warnings,
        "csv_paths": csv_paths,
    }

    summary_path = out_dir / f"{base}_summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    summary["summary_path"] = str(summary_path)
    return summary


def _validate_request(request: MapVsGtfsRequest) -> None:
    try:
        datetime.strptime(request.service_date, "%Y-%m-%d")
    except ValueError as exc:
        raise ValueError("service_date must be formatted as YYYY-MM-DD") from exc
    if request.match_tolerance_min < 0:
        raise ValueError("match_tolerance_min must be >= 0")
    if request.speed_warning_kmh <= 0:
        raise ValueError("speed_warning_kmh must be positive")


def _read_required_csv(path: Path, required_columns: set[str]) -> pd.DataFrame:
    if not path.is_file():
        raise FileNotFoundError(f"Missing map export file: {path}")
    frame = pd.read_csv(path)
    missing = required_columns - set(frame.columns)
    if missing:
        raise ValueError(f"{path.name} is missing required columns: {sorted(missing)}")
    return frame


def _route_key(frame: pd.DataFrame) -> pd.Series:
    return (
        frame["mkt"].astype(str)
        + "-"
        + frame["direction"].astype(str)
        + "-"
        + frame["alternative"].astype(str)
    )


def _minutes_to_hhmm(minutes: float | int) -> str:
    total = int(round(float(minutes)))
    return f"{(total // 60) % 24:02d}:{total % 60:02d}"


def _endpoints_from_long_name(route_long_name: str) -> str:
    """Extract 'origin - dest' endpoint text from an Israeli GTFS route_long_name.

    Format: '<origin stop>-<origin city><-><dest stop>-<dest city>-<alt code>'.
    """
    parts = route_long_name.split("<->")
    if len(parts) != 2:
        return route_long_name
    origin, dest = parts[0], parts[1]
    dest = dest.rsplit("-", 1)[0] if "-" in dest else dest
    return f"{origin} - {dest}"


def _pull_asymmetry(legs: pd.DataFrame) -> pd.DataFrame:
    """Compare pull-out vs reverse pull-in per depot-stop pair.

    Flags pairs where only one direction exists in the catalog, or where the
    travel times of the two directions differ by more than 50%.
    """
    if "type" not in legs.columns:
        return pd.DataFrame()

    pull_out = legs[legs["type"] == "depot_pull_out"]
    pull_in = legs[legs["type"] == "depot_pull_in"]

    out_pairs = {
        (str(row["origin_stop_id"]), str(row["dest_stop_id"])): float(row["travel_time_min"])
        for _, row in pull_out.iterrows()
        if pd.notna(row["travel_time_min"])
    }
    in_pairs = {
        (str(row["dest_stop_id"]), str(row["origin_stop_id"])): float(row["travel_time_min"])
        for _, row in pull_in.iterrows()
        if pd.notna(row["travel_time_min"])
    }

    rows: list[dict[str, Any]] = []
    for (depot, stop) in sorted(set(out_pairs) | set(in_pairs)):
        out_time = out_pairs.get((depot, stop))
        in_time = in_pairs.get((depot, stop))
        if out_time is None or in_time is None:
            rows.append(
                {
                    "depot_stop_id": depot,
                    "service_stop_id": stop,
                    "pull_out_min": out_time,
                    "pull_in_min": in_time,
                    "issue": "missing_direction",
                }
            )
            continue
        longest = max(out_time, in_time)
        shortest = min(out_time, in_time)
        if shortest > 0 and longest / shortest > 1.5:
            rows.append(
                {
                    "depot_stop_id": depot,
                    "service_stop_id": stop,
                    "pull_out_min": out_time,
                    "pull_in_min": in_time,
                    "issue": "time_asymmetry",
                }
            )
    return pd.DataFrame(rows)


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entrypoint: compare a map export against the GTFS baseline."""
    parser = argparse.ArgumentParser(
        description="Compare an Optibus map export against the GTFS baseline for a service date."
    )
    parser.add_argument("--map-export-dir", required=True, help="Directory with trips.csv/routes.csv/deadheads.csv.")
    parser.add_argument("--service-date", required=True, help="Target service date, YYYY-MM-DD (a Friday for a Friday map).")
    parser.add_argument("--operator-ref", default="34", help="GTFS operator_ref (default 34 = Tnufa).")
    parser.add_argument("--match-tolerance-min", type=int, default=0, help="Departure match tolerance in minutes (0 = exact).")
    parser.add_argument("--assigned-only", action="store_true", help="Compare only trips assigned to vehicles.")
    parser.add_argument(
        "--speed-warning-kmh",
        type=float,
        default=DEFAULT_SPEED_WARNING_KMH,
        help="Deadhead speed above this value is flagged (default 90).",
    )
    parser.add_argument("--output-dir", default="outputs", help="Directory for CSV and JSON outputs.")
    args = parser.parse_args(argv)

    request = MapVsGtfsRequest(
        map_export_dir=args.map_export_dir,
        service_date=args.service_date,
        operator_ref=args.operator_ref,
        match_tolerance_min=args.match_tolerance_min,
        assigned_only=args.assigned_only,
        speed_warning_kmh=args.speed_warning_kmh,
        output_dir=args.output_dir,
    )
    summary = run_analysis(request)
    print(f"summary: {summary['summary_path']}")
    print(
        "match: "
        f"{summary['matched_departures']}/{summary['map_trips']} "
        f"(rate={summary['departure_match_rate']}), "
        f"map_only={summary['map_only_departures']}, gtfs_only={summary['gtfs_only_departures']}"
    )
    print(f"stale_map_mkts: {summary['stale_map_mkts']}")
    print(f"new_gtfs_mkts: {summary['new_gtfs_mkts']}")
    print(f"deadhead_checks: {summary['deadhead_checks']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
