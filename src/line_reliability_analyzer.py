"""Line reliability analysis for Open Bus Stride planned vs actual rides."""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence

import pandas as pd

from src.open_bus_stride_client import (
    OpenBusStrideClient,
    _request_metadata_to_dict,
    find_line_refs,
)


@dataclass(frozen=True)
class LineReliabilityRequest:
    """Input filters for line reliability analysis."""

    service_date: str
    operator_ref: str | int
    route_short_name: str
    line_ref: str | None = None
    route_mkt: str | None = None
    route_direction: str | int | None = None
    route_alternative: str | None = None
    hour_from: int | None = None
    hour_to: int | None = None
    max_null_gtfs_ride_id_rate: float = 0.25


@dataclass(frozen=True)
class DataQualityCheck:
    """Single pre-KPI quality validation result."""

    name: str
    status: str
    value: str
    details: str


class LineSelectionError(ValueError):
    """Raised when line_ref identification is unsafe or ambiguous."""

    def __init__(
        self,
        message: str,
        *,
        candidates: list[dict[str, Any]],
        filters: dict[str, Any],
    ) -> None:
        super().__init__(message)
        self.candidates = candidates
        self.filters = filters


def _normalize_hour_window(df: pd.DataFrame, hour_from: int | None, hour_to: int | None) -> pd.DataFrame:
    """Filter rows by planned start hour, when hour window is provided."""
    if hour_from is None and hour_to is None:
        return df

    if "planned_start_time" not in df.columns:
        return df

    planned = pd.to_datetime(df["planned_start_time"], errors="coerce")
    mask = pd.Series(True, index=df.index)
    if hour_from is not None:
        mask &= planned.dt.hour >= hour_from
    if hour_to is not None:
        mask &= planned.dt.hour <= hour_to
    return df.loc[mask].copy()


def _compute_kpi(df: pd.DataFrame) -> dict[str, float | int]:
    """Compute planned/actual KPI metrics from rides execution rows."""
    planned_rides = int(len(df))

    actual_mask = _present_mask(df, "actual_start_time")
    matched_mask = _present_mask(df, "gtfs_ride_id")

    actual_rides = int(actual_mask.sum())
    matched_rides = int((actual_mask & matched_mask).sum())
    missing_actual_start = int((~actual_mask).sum())
    null_gtfs_ride_id_rate = float((~matched_mask).mean()) if planned_rides else 0.0

    return {
        "planned_rides": planned_rides,
        "actual_rides": actual_rides,
        "matched_rides": matched_rides,
        "missing_actual_start": missing_actual_start,
        "null_gtfs_ride_id_rate": null_gtfs_ride_id_rate,
    }


def _build_html_report(
    df: pd.DataFrame,
    kpi: dict[str, float | int],
    params: LineReliabilityRequest,
    quality_checks: list[DataQualityCheck],
) -> str:
    """Build a small RTL HTML report."""
    kpi_html = "".join(
        f"<tr><th>{name}</th><td>{value}</td></tr>" for name, value in kpi.items()
    )
    quality_html = "".join(
        "<tr>"
        f"<th>{check.name}</th>"
        f"<td>{check.status}</td>"
        f"<td>{check.value}</td>"
        f"<td>{check.details}</td>"
        "</tr>"
        for check in quality_checks
    )
    table_html = df.to_html(index=False, classes="dataframe", border=0)

    return f"""<!doctype html>
<html lang=\"he\" dir=\"rtl\">
<head>
  <meta charset=\"utf-8\" />
  <title>דוח אמינות קו</title>
  <style>
    body {{ font-family: Arial, sans-serif; margin: 20px; direction: rtl; }}
    table {{ border-collapse: collapse; width: 100%; margin-bottom: 24px; }}
    th, td {{ border: 1px solid #ddd; padding: 8px; text-align: right; }}
    th {{ background: #f3f3f3; }}
  </style>
</head>
<body>
  <h1>דוח אמינות קו: תכנון מול ביצוע</h1>
  <p>תאריך שירות: {params.service_date} | מפעיל: {params.operator_ref} | מספר קו: {params.route_short_name}</p>
  <h2>KPI</h2>
  <table>{kpi_html}</table>
  <h2>בדיקות איכות לפני KPI</h2>
  <table>
    <tr><th>בדיקה</th><th>סטטוס</th><th>ערך</th><th>פירוט</th></tr>
    {quality_html}
  </table>
  <h2>פירוט נסיעות</h2>
  {table_html}
</body>
</html>
"""


def analyze_line_reliability(
    client: OpenBusStrideClient,
    request: LineReliabilityRequest,
    *,
    output_dir: str | Path = "outputs",
) -> tuple[pd.DataFrame, dict[str, float | int], dict[str, Any]]:
    """Analyze planned vs actual rides and persist CSV + RTL HTML report.

    Returns dataframe, KPI summary, and reproducibility metadata.
    """
    _validate_request_window(request)
    line_candidates = pd.DataFrame()
    selected_line_ref = request.line_ref

    if not selected_line_ref:
        line_candidates = find_line_refs(
            client,
            service_date=request.service_date,
            operator_ref=request.operator_ref,
            route_short_name=request.route_short_name,
            route_direction=request.route_direction,
            route_alternative=request.route_alternative,
            route_mkt=request.route_mkt,
        )
        selected_line_ref = select_line_ref(line_candidates, request)

    rides_df = client.get_df(
        "/rides_execution/list",
        date_from=request.service_date,
        date_to=request.service_date,
        operator_ref=str(request.operator_ref),
        line_ref=str(selected_line_ref),
        order_by="planned_start_time asc",
    )

    rides_df = _normalize_hour_window(rides_df, request.hour_from, request.hour_to)
    quality_checks = validate_data_quality(
        client,
        rides_df,
        request,
        selected_line_ref=str(selected_line_ref),
        line_candidates=line_candidates,
    )
    kpi = _compute_kpi(rides_df)

    metadata: dict[str, Any] = {
        "endpoint": "/rides_execution/list",
        "params": {
            "date_from": request.service_date,
            "date_to": request.service_date,
            "operator_ref": str(request.operator_ref),
            "line_ref": str(selected_line_ref),
            "route_short_name": request.route_short_name,
            "route_mkt": request.route_mkt,
            "route_direction": request.route_direction,
            "route_alternative": request.route_alternative,
            "hour_from": request.hour_from,
            "hour_to": request.hour_to,
        },
        "selected_line_ref": selected_line_ref,
        "line_ref_candidates": line_candidates.to_dict(orient="records"),
        "data_quality": [asdict(check) for check in quality_checks],
        "request_log": [
            metadata
            for metadata in (_request_metadata_to_dict(item) for item in client.request_log)
            if metadata is not None
        ],
    }

    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    base_name = (
        f"line_reliability_{request.service_date}_op_{request.operator_ref}_line_{selected_line_ref}"
    )
    csv_path = out_dir / f"{base_name}.csv"
    html_path = out_dir / f"{base_name}.html"

    rides_df.to_csv(csv_path, index=False)
    html_path.write_text(
        _build_html_report(rides_df, kpi, request, quality_checks),
        encoding="utf-8",
    )

    metadata["csv_path"] = str(csv_path)
    metadata["html_path"] = str(html_path)
    return rides_df, kpi, metadata


def select_line_ref(candidates: pd.DataFrame, request: LineReliabilityRequest) -> str:
    """Select line_ref only when candidate identity is unambiguous."""
    filtered = _filter_line_candidates(candidates, request)
    candidate_records = filtered.to_dict(orient="records")
    filters = _line_identity_filters(request)

    if filtered.empty:
        raise LineSelectionError(
            "No line_ref candidates matched the requested date/operator/route identity",
            candidates=candidates.to_dict(orient="records"),
            filters=filters,
        )
    if "line_ref" not in filtered.columns:
        raise LineSelectionError(
            "Line candidates response is missing line_ref",
            candidates=candidate_records,
            filters=filters,
        )

    line_refs = sorted({str(value) for value in filtered["line_ref"].dropna().tolist()})
    if len(line_refs) == 1:
        return line_refs[0]

    raise LineSelectionError(
        "Ambiguous line_ref candidates; provide line_ref or route_mkt/route_direction/route_alternative",
        candidates=candidate_records,
        filters=filters,
    )


def validate_data_quality(
    client: OpenBusStrideClient,
    rides_df: pd.DataFrame,
    request: LineReliabilityRequest,
    *,
    selected_line_ref: str,
    line_candidates: pd.DataFrame,
) -> list[DataQualityCheck]:
    """Run quality checks before KPI metrics are interpreted."""
    checks = [
        _validate_line_identity(selected_line_ref, line_candidates, request),
        _validate_planned_time_window(rides_df, request),
        _validate_gtfs_ride_id_rate(rides_df, request),
        _validate_siri_snapshots(client),
    ]
    return checks


def _validate_request_window(request: LineReliabilityRequest) -> None:
    try:
        datetime.strptime(request.service_date, "%Y-%m-%d")
    except ValueError as exc:
        raise ValueError("service_date must be formatted as YYYY-MM-DD") from exc

    for name, value in [("hour_from", request.hour_from), ("hour_to", request.hour_to)]:
        if value is not None and not 0 <= value <= 23:
            raise ValueError(f"{name} must be between 0 and 23")
    if (
        request.hour_from is not None
        and request.hour_to is not None
        and request.hour_from > request.hour_to
    ):
        raise ValueError("hour_from must be less than or equal to hour_to")
    if not 0 <= request.max_null_gtfs_ride_id_rate <= 1:
        raise ValueError("max_null_gtfs_ride_id_rate must be between 0 and 1")


def _filter_line_candidates(
    candidates: pd.DataFrame,
    request: LineReliabilityRequest,
) -> pd.DataFrame:
    if candidates.empty:
        return candidates.copy()
    filtered = candidates.copy()
    for column, expected in [
        ("route_mkt", request.route_mkt),
        ("route_direction", request.route_direction),
        ("route_alternative", request.route_alternative),
    ]:
        if expected is None or column not in filtered.columns:
            continue
        filtered = filtered[filtered[column].astype(str) == str(expected)]
    return filtered.copy()


def _line_identity_filters(request: LineReliabilityRequest) -> dict[str, Any]:
    return {
        "service_date": request.service_date,
        "operator_ref": str(request.operator_ref),
        "route_short_name": request.route_short_name,
        "route_mkt": request.route_mkt,
        "route_direction": request.route_direction,
        "route_alternative": request.route_alternative,
    }


def _validate_line_identity(
    selected_line_ref: str,
    line_candidates: pd.DataFrame,
    request: LineReliabilityRequest,
) -> DataQualityCheck:
    details = (
        "line_ref supplied explicitly."
        if request.line_ref
        else "line_ref selected only after candidate set was unique."
    )
    identity = [
        f"line_ref={selected_line_ref}",
        f"route_mkt={request.route_mkt or 'not provided'}",
        f"route_direction={request.route_direction or 'not provided'}",
        f"route_alternative={request.route_alternative or 'not provided'}",
    ]
    if not request.line_ref and not line_candidates.empty:
        identity.append(f"candidate_rows={len(line_candidates)}")
    return DataQualityCheck(
        name="line_identity",
        status="ok",
        value=", ".join(identity),
        details=details,
    )


def _validate_planned_time_window(
    rides_df: pd.DataFrame,
    request: LineReliabilityRequest,
) -> DataQualityCheck:
    if rides_df.empty:
        return DataQualityCheck(
            name="date_time_window",
            status="warning",
            value="0 rides",
            details="No rides were returned for the selected service date and line_ref.",
        )
    if "planned_start_time" not in rides_df.columns:
        return DataQualityCheck(
            name="date_time_window",
            status="warning",
            value="missing planned_start_time",
            details="Cannot validate service date or hour window without planned_start_time.",
        )

    planned = pd.to_datetime(rides_df["planned_start_time"], errors="coerce")
    invalid_count = int(planned.isna().sum())
    service_dates = planned.dropna().dt.strftime("%Y-%m-%d")
    outside_date = int((service_dates != request.service_date).sum())
    outside_hours = 0
    if request.hour_from is not None:
        outside_hours += int((planned.dropna().dt.hour < request.hour_from).sum())
    if request.hour_to is not None:
        outside_hours += int((planned.dropna().dt.hour > request.hour_to).sum())

    status = "ok" if invalid_count == 0 and outside_date == 0 and outside_hours == 0 else "warning"
    return DataQualityCheck(
        name="date_time_window",
        status=status,
        value=(
            f"invalid={invalid_count}; outside_date={outside_date}; "
            f"outside_hours={outside_hours}"
        ),
        details="Validates planned_start_time against service_date and requested hour window.",
    )


def _validate_gtfs_ride_id_rate(
    rides_df: pd.DataFrame,
    request: LineReliabilityRequest,
) -> DataQualityCheck:
    if rides_df.empty:
        rate = 0.0
        missing = 0
    elif "gtfs_ride_id" not in rides_df.columns:
        rate = 1.0
        missing = len(rides_df)
    else:
        missing_mask = ~_present_mask(rides_df, "gtfs_ride_id")
        missing = int(missing_mask.sum())
        rate = float(missing_mask.mean())
    status = "ok" if rate <= request.max_null_gtfs_ride_id_rate else "warning"
    return DataQualityCheck(
        name="gtfs_ride_id_missing_rate",
        status=status,
        value=f"{missing}/{len(rides_df)} ({rate:.1%})",
        details=(
            "High missing gtfs_ride_id rate weakens planned-vs-actual matching. "
            f"Threshold={request.max_null_gtfs_ride_id_rate:.0%}."
        ),
    )


def _validate_siri_snapshots(client: OpenBusStrideClient) -> DataQualityCheck:
    try:
        snapshots = client.get_df(
            "/siri_snapshots/list",
            limit=20,
            order_by="id desc",
        )
    except Exception as exc:
        return DataQualityCheck(
            name="siri_snapshots",
            status="warning",
            value="not available",
            details=f"Could not fetch latest siri_snapshots status before KPI: {exc}",
        )

    if snapshots.empty:
        return DataQualityCheck(
            name="siri_snapshots",
            status="warning",
            value="0 rows",
            details="No SIRI snapshot metadata returned; SIRI coverage cannot be confirmed.",
        )

    status_counts: dict[str, int] = {}
    if "etl_status" in snapshots.columns:
        status_counts = {
            str(status): int(count)
            for status, count in snapshots["etl_status"].fillna("NULL").value_counts().items()
        }
    error_count = int(_present_mask(snapshots, "error").sum()) if "error" in snapshots.columns else 0
    ok_statuses = {"loaded", "success", "completed", "done", "ok"}
    non_ok_statuses = [
        status
        for status in status_counts
        if status.lower() not in ok_statuses and status.upper() != "NULL"
    ]
    status = "ok" if error_count == 0 and not non_ok_statuses else "warning"
    return DataQualityCheck(
        name="siri_snapshots",
        status=status,
        value=f"rows={len(snapshots)}; statuses={status_counts or 'unknown'}; errors={error_count}",
        details="Latest SIRI ETL snapshot metadata checked before KPI interpretation.",
    )


def _present_mask(df: pd.DataFrame, column: str) -> pd.Series:
    if column not in df.columns:
        return pd.Series([False] * len(df), index=df.index)
    return df[column].notna() & (df[column].astype(str).str.strip() != "")


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entrypoint for hardened line reliability analysis."""
    parser = argparse.ArgumentParser(
        description="Analyze Open Bus Stride planned-vs-actual line reliability."
    )
    parser.add_argument("--service-date", required=True, help="Service date in YYYY-MM-DD format.")
    parser.add_argument("--operator-ref", required=True, help="Operator reference, e.g. 3 for Egged.")
    parser.add_argument("--route-short-name", required=True, help="Public route number, e.g. 18.")
    parser.add_argument("--line-ref", default=None, help="Explicit line_ref. Required if candidates are ambiguous.")
    parser.add_argument("--route-mkt", default=None, help="Optional route_mkt filter for safer line identity.")
    parser.add_argument("--route-direction", default=None, help="Optional route_direction filter.")
    parser.add_argument("--route-alternative", default=None, help="Optional route_alternative filter.")
    parser.add_argument("--hour-from", type=int, default=None, help="Optional first planned start hour, 0-23.")
    parser.add_argument("--hour-to", type=int, default=None, help="Optional last planned start hour, 0-23.")
    parser.add_argument(
        "--max-null-gtfs-ride-id-rate",
        type=float,
        default=0.25,
        help="Warning threshold for missing gtfs_ride_id rate, from 0 to 1.",
    )
    parser.add_argument("--output-dir", default="outputs", help="Directory for CSV and HTML report outputs.")
    args = parser.parse_args(argv)

    request = LineReliabilityRequest(
        service_date=args.service_date,
        operator_ref=args.operator_ref,
        route_short_name=args.route_short_name,
        line_ref=args.line_ref,
        route_mkt=args.route_mkt,
        route_direction=args.route_direction,
        route_alternative=args.route_alternative,
        hour_from=args.hour_from,
        hour_to=args.hour_to,
        max_null_gtfs_ride_id_rate=args.max_null_gtfs_ride_id_rate,
    )
    _, kpi, metadata = analyze_line_reliability(
        OpenBusStrideClient(),
        request,
        output_dir=args.output_dir,
    )
    print(f"selected_line_ref: {metadata['selected_line_ref']}")
    print(f"csv: {metadata['csv_path']}")
    print(f"html: {metadata['html_path']}")
    print(f"kpi: {kpi}")
    print(f"data_quality: {metadata['data_quality']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
