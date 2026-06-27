"""Fetch the data.gov.il bus ridership dataset via CKAN.

The Ministry of Transport publishes "נסועה בקווי אוטובוס" under the CKAN
package ``ridership``. Resources are currently yearly CSVs with quarter fields
inside the records, so the output file name uses the latest year/quarter found
in the fetched data.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import datetime
import json
from pathlib import Path
import re
from typing import Any, Sequence

import pandas as pd
import requests

CKAN_ACTION_BASE = "https://data.gov.il/api/3/action"
PACKAGE_ID = "ridership"
PAGE_LIMIT = 32000
DEFAULT_CACHE_PATH = Path("data/cache/ridership_meta.json")
DEFAULT_RAW_DIR = Path("data/raw")


class RidershipFetchError(RuntimeError):
    """Raised when CKAN ridership discovery or fetching fails."""


@dataclass(frozen=True)
class CkanResource:
    """The CKAN resource identity needed for cache checks."""

    id: str
    name: str
    metadata_modified: str
    url: str | None = None


@dataclass(frozen=True)
class FetchResult:
    """Result metadata for a completed ridership fetch."""

    resource: CkanResource
    output_path: str
    row_count: int


COLUMN_RENAMES: dict[str, str] = {
    "_id": "_id",
    "RouteID": "route_id",
    "RouteName": "route_short_name",
    "RouteDirection": "route_direction",
    "AgencyName": "agency_name",
    "ClusterName": "cluster_name",
    "Metropolin": "metropolitan_area",
    "OriginCityName": "origin_city_name",
    "DestinationCityName": "destination_city_name",
    "RouteType": "route_type",
    "ServiceType": "service_type",
    "RouteParticular": "route_particular",
    "BusType": "bus_type",
    "BusSize": "bus_size",
    "NumOfAlternatives": "num_of_alternatives",
    "RouteLength": "route_length",
    "WeeklyKM": "weekly_km",
    "AVGPassengersPerWeek": "avg_passengers_per_week",
    "StationsInRoute": "stations_in_route",
    "OperationSince": "operation_since",
    "UniqueStations": "unique_stations",
    "UniqueLocations": "unique_locations",
    "AverageSpeed": "average_speed",
    "AverageTripDuration": "average_trip_duration",
    "OperatingCostPerPassenger": "operating_cost_per_passenger",
    "DailyRides(Tuesday)": "daily_rides_tuesday",
    "WeekyRides": "weekly_rides",
    "DailyPassengers": "daily_passengers",
    "WeeklyPassengers": "weekly_passengers",
    "AVGCommutersPerRide(Weekly)": "avg_commuters_per_ride_weekly",
    "המלצה": "recommendation",
    "ערך מקסימום בתקופת יום": "max_day_period_value",
    "נסועה מקסימלית": "max_ridership_period",
    "ערך מקסימום בתקופת יום קטן מ 3": "max_day_period_value_lt_3",
    "ערך מקסימום בתקופת יום קטן מ 7": "max_day_period_value_lt_7",
    "ערך מקסימום בתקופת יום קטן מ 10": "max_day_period_value_lt_10",
    "ערך מקסימום בתקופת יום קטן מ 15": "max_day_period_value_lt_15",
    "ממוצע נוסעים לקמ נמוך": "low_passengers_per_km_avg",
    "דרוג הפחתה": "reduction_rank",
    "דרוג הוספה": "addition_rank",
    "year": "year",
    "Q": "quarter",
}

TIME_BAND_PREFIXES = {
    "ימי חול - ": "weekday",
    "שישי - ": "friday",
    "שבת - ": "saturday",
}


def discover_latest_resource(
    *,
    package_id: str = PACKAGE_ID,
    session: Any = requests,
    timeout: int = 30,
) -> CkanResource:
    """Discover the newest active CSV resource for the ridership package."""
    payload = _ckan_action(
        "package_show",
        {"id": package_id},
        session=session,
        timeout=timeout,
    )
    result = payload.get("result")
    if not isinstance(result, dict):
        raise RidershipFetchError("CKAN package_show response is missing result")
    resources = result.get("resources")
    if not isinstance(resources, list):
        raise RidershipFetchError("CKAN package_show response is missing resources")
    return _select_latest_resource(resources)


def fetch_ridership(
    *,
    out: str | Path | None = None,
    raw_dir: str | Path = DEFAULT_RAW_DIR,
    cache_path: str | Path = DEFAULT_CACHE_PATH,
    package_id: str = PACKAGE_ID,
    force_refresh: bool = False,
    session: Any = requests,
    timeout: int = 30,
) -> FetchResult:
    """Fetch latest ridership data, normalize columns, persist CSV and cache."""
    resource = discover_latest_resource(
        package_id=package_id,
        session=session,
        timeout=timeout,
    )
    cache = Path(cache_path)
    changed = _check_for_update(resource, cache)
    if not changed and not force_refresh:
        raise RidershipFetchError(
            "No newer ridership resource found. Use --force-refresh to fetch anyway."
        )

    records = _fetch_all_records(
        resource.id,
        session=session,
        page_limit=PAGE_LIMIT,
        timeout=timeout,
    )
    if not records:
        raise RidershipFetchError(f"CKAN resource {resource.id} returned zero records")

    df = _normalize_records(records)
    output_path = Path(out) if out else _default_output_path(df, Path(raw_dir))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(output_path, index=False)
    _write_cache(
        resource,
        cache,
        output_path=output_path,
        row_count=len(df),
    )
    return FetchResult(
        resource=resource,
        output_path=str(output_path),
        row_count=len(df),
    )


def _ckan_action(
    action: str,
    params: dict[str, Any],
    *,
    session: Any,
    timeout: int,
) -> dict[str, Any]:
    url = f"{CKAN_ACTION_BASE.rstrip('/')}/{action}"
    response = session.get(url, params=params, timeout=timeout)
    response.raise_for_status()
    payload = response.json()
    if not payload.get("success"):
        raise RidershipFetchError(f"CKAN {action} reported failure: {payload!r}")
    return payload


def _select_latest_resource(resources: list[dict[str, Any]]) -> CkanResource:
    csv_resources = [
        resource
        for resource in resources
        if str(resource.get("format", "")).upper() == "CSV"
        and bool(resource.get("datastore_active"))
        and resource.get("id")
    ]
    if not csv_resources:
        raise RidershipFetchError("No active CSV datastore resource found for ridership")

    latest = max(csv_resources, key=_resource_sort_key)
    metadata_modified = _resource_modified(latest)
    return CkanResource(
        id=str(latest["id"]),
        name=str(latest.get("name") or latest["id"]),
        metadata_modified=metadata_modified,
        url=str(latest["url"]) if latest.get("url") else None,
    )


def _resource_sort_key(resource: dict[str, Any]) -> tuple[datetime, str]:
    return (_parse_datetime(_resource_modified(resource)), str(resource.get("id") or ""))


def _resource_modified(resource: dict[str, Any]) -> str:
    return str(
        resource.get("metadata_modified")
        or resource.get("last_modified")
        or resource.get("created")
        or ""
    )


def _parse_datetime(value: str) -> datetime:
    if not value:
        return datetime.min
    return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)


def _fetch_all_records(
    resource_id: str,
    *,
    session: Any = requests,
    page_limit: int = PAGE_LIMIT,
    timeout: int = 30,
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    offset = 0
    while True:
        payload = _ckan_action(
            "datastore_search",
            {
                "resource_id": resource_id,
                "limit": page_limit,
                "offset": offset,
            },
            session=session,
            timeout=timeout,
        )
        result = payload.get("result")
        if not isinstance(result, dict):
            raise RidershipFetchError("CKAN datastore_search response is missing result")
        page_records = result.get("records") or []
        if not isinstance(page_records, list):
            raise RidershipFetchError("CKAN datastore_search records is not a list")
        if not page_records:
            break
        records.extend(page_records)
        offset += len(page_records)
        total = int(result.get("total") or 0)
        if offset >= total or len(page_records) < page_limit:
            break
    return records


def _normalize_records(records: list[dict[str, Any]]) -> pd.DataFrame:
    df = pd.DataFrame.from_records(records)
    rename_map = {column: _canonical_column_name(str(column)) for column in df.columns}
    return df.rename(columns=rename_map)


def _canonical_column_name(column: str) -> str:
    if column in COLUMN_RENAMES:
        return COLUMN_RENAMES[column]
    for prefix, day_name in TIME_BAND_PREFIXES.items():
        if column.startswith(prefix):
            return f"{day_name}_{_time_band_slug(column.removeprefix(prefix))}"
    return _snake_case(column)


def _time_band_slug(value: str) -> str:
    digits = re.findall(r"\d{2}:\d{2}", value)
    if len(digits) == 2:
        return "_".join(part.replace(":", "") for part in digits)
    return _snake_case(value)


def _snake_case(value: str) -> str:
    value = re.sub(r"(?<!^)(?=[A-Z])", "_", value)
    value = re.sub(r"[^0-9A-Za-z]+", "_", value).strip("_").lower()
    return value or "unnamed_column"


def _check_for_update(resource: CkanResource, cache_path: Path) -> bool:
    if not cache_path.exists():
        return True
    try:
        cached = json.loads(cache_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return True
    return (
        cached.get("resource_id") != resource.id
        or cached.get("metadata_modified") != resource.metadata_modified
    )


def _write_cache(
    resource: CkanResource,
    cache_path: Path,
    *,
    output_path: Path,
    row_count: int,
) -> None:
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(
        json.dumps(
            {
                "resource_id": resource.id,
                "resource_name": resource.name,
                "metadata_modified": resource.metadata_modified,
                "resource_url": resource.url,
                "output_path": str(output_path),
                "row_count": row_count,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )


def _default_output_path(df: pd.DataFrame, raw_dir: Path) -> Path:
    if "year" not in df.columns or "quarter" not in df.columns:
        return raw_dir / "ridership_latest.csv"

    periods = df[["year", "quarter"]].copy()
    periods["year"] = pd.to_numeric(periods["year"], errors="coerce")
    periods["quarter"] = (
        periods["quarter"]
        .astype(str)
        .str.extract(r"(\d+)", expand=False)
        .pipe(pd.to_numeric, errors="coerce")
    )
    periods = periods.dropna(subset=["year", "quarter"])
    if periods.empty:
        return raw_dir / "ridership_latest.csv"

    latest = periods.sort_values(["year", "quarter"]).iloc[-1]
    return raw_dir / f"ridership_{int(latest['year'])}Q{int(latest['quarter'])}.csv"


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entrypoint for ridership discovery/check/fetch."""
    parser = argparse.ArgumentParser(
        description="Fetch latest data.gov.il bus ridership CSV via CKAN."
    )
    parser.add_argument("--check-only", action="store_true", help="Exit 1 when a new CKAN resource is available.")
    parser.add_argument("--force-refresh", action="store_true", help="Fetch even when cache metadata is unchanged.")
    parser.add_argument("--out", default=None, help="Explicit output CSV path.")
    parser.add_argument("--raw-dir", default=str(DEFAULT_RAW_DIR), help="Default output directory.")
    parser.add_argument("--cache-path", default=str(DEFAULT_CACHE_PATH), help="Ridership metadata cache JSON.")
    parser.add_argument("--package-id", default=PACKAGE_ID, help="CKAN package id.")
    parser.add_argument("--timeout", type=int, default=30, help="HTTP timeout in seconds.")
    args = parser.parse_args(argv)

    try:
        resource = discover_latest_resource(package_id=args.package_id, timeout=args.timeout)
        cache_path = Path(args.cache_path)
        changed = _check_for_update(resource, cache_path)
        if args.check_only:
            if changed:
                print(
                    "new ridership resource available: "
                    f"{resource.id} ({resource.metadata_modified})"
                )
                return 1
            print(
                "ridership resource unchanged: "
                f"{resource.id} ({resource.metadata_modified})"
            )
            return 0

        if not changed and not args.force_refresh:
            print(
                "ridership resource unchanged; skipping fetch. "
                "Use --force-refresh to download again."
            )
            return 0

        result = fetch_ridership(
            out=args.out,
            raw_dir=args.raw_dir,
            cache_path=args.cache_path,
            package_id=args.package_id,
            force_refresh=args.force_refresh,
            timeout=args.timeout,
        )
    except RidershipFetchError as exc:
        parser.exit(2, f"ridership_fetch: {exc}\n")

    print(f"resource_id: {result.resource.id}")
    print(f"metadata_modified: {result.resource.metadata_modified}")
    print(f"rows: {result.row_count}")
    print(f"csv: {result.output_path}")
    print(f"resource: {json.dumps(asdict(result.resource), ensure_ascii=False)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
