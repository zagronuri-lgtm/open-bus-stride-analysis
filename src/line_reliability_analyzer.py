"""Line reliability analysis for Open Bus Stride planned vs actual rides."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from src.open_bus_stride_client import OpenBusStrideClient, find_line_refs


@dataclass(frozen=True)
class LineReliabilityRequest:
    """Input filters for line reliability analysis."""

    service_date: str
    operator_ref: str | int
    route_short_name: str
    line_ref: str | None = None
    hour_from: int | None = None
    hour_to: int | None = None


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

    actual_mask = df.get("actual_start_time", pd.Series([None] * len(df))).notna()
    matched_mask = df.get("gtfs_ride_id", pd.Series([None] * len(df))).notna()

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


def _build_html_report(df: pd.DataFrame, kpi: dict[str, float | int], params: LineReliabilityRequest) -> str:
    """Build a small RTL HTML report."""
    kpi_html = "".join(
        f"<tr><th>{name}</th><td>{value}</td></tr>" for name, value in kpi.items()
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
    line_candidates = pd.DataFrame()
    selected_line_ref = request.line_ref

    if not selected_line_ref:
        line_candidates = find_line_refs(
            client,
            service_date=request.service_date,
            operator_ref=request.operator_ref,
            route_short_name=request.route_short_name,
        )
        if line_candidates.empty:
            raise ValueError("No line_ref candidates found for requested service date/operator/route")
        selected_line_ref = str(line_candidates.iloc[0]["line_ref"])

    rides_df = client.get_df(
        "/rides_execution/list",
        date_from=request.service_date,
        date_to=request.service_date,
        gtfs_route__operator_ref=str(request.operator_ref),
        gtfs_route__route_short_name=request.route_short_name,
        gtfs_route__line_ref=str(selected_line_ref),
        order_by="planned_start_time asc",
    )

    rides_df = _normalize_hour_window(rides_df, request.hour_from, request.hour_to)
    kpi = _compute_kpi(rides_df)

    metadata: dict[str, Any] = {
        "endpoint": "/rides_execution/list",
        "params": {
            "date_from": request.service_date,
            "date_to": request.service_date,
            "gtfs_route__operator_ref": str(request.operator_ref),
            "gtfs_route__route_short_name": request.route_short_name,
            "gtfs_route__line_ref": str(selected_line_ref),
            "hour_from": request.hour_from,
            "hour_to": request.hour_to,
        },
        "selected_line_ref": selected_line_ref,
        "line_ref_candidates": line_candidates.to_dict(orient="records"),
    }

    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    base_name = (
        f"line_reliability_{request.service_date}_op_{request.operator_ref}_line_{selected_line_ref}"
    )
    csv_path = out_dir / f"{base_name}.csv"
    html_path = out_dir / f"{base_name}.html"

    rides_df.to_csv(csv_path, index=False)
    html_path.write_text(_build_html_report(rides_df, kpi, request), encoding="utf-8")

    metadata["csv_path"] = str(csv_path)
    metadata["html_path"] = str(html_path)
    return rides_df, kpi, metadata
