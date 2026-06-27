"""Run line reliability analysis for Metropoline clusters on a daily rotation."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Sequence
from zoneinfo import ZoneInfo

import pandas as pd

from src.line_reliability_analyzer import (
    LineReliabilityRequest,
    analyze_line_reliability,
)
from src.open_bus_stride_client import OpenBusStrideClient

ISRAEL_TZ = ZoneInfo("Asia/Jerusalem")
DEFAULT_MANIFEST = Path("data/reference/metropoline_line_ids.csv")
DEFAULT_OUTPUT_DIR = Path("data/output/reliability")
WEEKLY_SUMMARY = "__weekly_summary__"

CLUSTER_BY_WEEKDAY = {
    6: "שרון חולון מרחבי",
    0: "בקעת אונו אלעד",
    1: "שרון",
    2: "הנגב",
    3: WEEKLY_SUMMARY,
}

REQUIRED_MANIFEST_COLUMNS = {
    "cluster",
    "operator_ref",
    "route_short_name",
    "route_mkt",
    "route_direction",
    "route_alternative",
}


@dataclass(frozen=True)
class ClusterRunResult:
    """Summary of one cluster reliability run."""

    mode: str
    service_date: str
    cluster: str | None
    total_rows: int = 0
    unique_lines: int = 0
    success_count: int = 0
    error_count: int = 0
    summary_path: str | None = None


class ClusterReliabilityRunnerError(RuntimeError):
    """Raised when the cluster runner cannot load or validate inputs."""


def _cluster_for_service_date(service_date: date) -> str | None:
    """Return the scheduled cluster for an Israeli service date."""
    return CLUSTER_BY_WEEKDAY.get(service_date.weekday())


def run_cluster_reliability(
    *,
    manifest: str | Path = DEFAULT_MANIFEST,
    service_date: str | date | None = None,
    output_dir: str | Path = DEFAULT_OUTPUT_DIR,
    cluster: str | None = None,
    dry_run: bool = False,
) -> ClusterRunResult:
    """Run the scheduled cluster, continue after line errors, and write summary CSV."""
    service_day = _resolve_service_date(service_date)
    selected_cluster = cluster or _cluster_for_service_date(service_day)
    out_dir = Path(output_dir)

    if selected_cluster is None:
        return ClusterRunResult(
            mode="skipped",
            service_date=service_day.isoformat(),
            cluster=None,
        )

    if selected_cluster == WEEKLY_SUMMARY:
        path = build_weekly_summary(out_dir, service_day)
        return ClusterRunResult(
            mode="weekly-summary",
            service_date=service_day.isoformat(),
            cluster=None,
            summary_path=str(path),
        )

    manifest_df = _load_manifest(Path(manifest))
    cluster_df = manifest_df[manifest_df["cluster"] == selected_cluster].copy()
    if cluster_df.empty:
        raise ClusterReliabilityRunnerError(
            f"No manifest rows found for cluster: {selected_cluster}"
        )
    cluster_df = cluster_df.sort_values(
        ["route_short_name", "route_mkt", "route_direction", "route_alternative"]
    )
    total_rows = int(len(cluster_df))
    unique_lines = int(cluster_df["route_short_name"].nunique())

    if dry_run:
        _print_dry_run(selected_cluster, service_day, cluster_df)
        return ClusterRunResult(
            mode="dry-run",
            service_date=service_day.isoformat(),
            cluster=selected_cluster,
            total_rows=total_rows,
            unique_lines=unique_lines,
        )

    summary_rows: list[dict[str, Any]] = []
    for _, row in cluster_df.iterrows():
        summary_rows.append(
            _run_manifest_row(row, selected_cluster, service_day, out_dir)
        )

    summary_path = _summary_path(out_dir, service_day.isoformat(), selected_cluster)
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(summary_rows).to_csv(summary_path, index=False)
    success_count = sum(row["status"] == "ok" for row in summary_rows)
    error_count = sum(row["status"] == "error" for row in summary_rows)
    return ClusterRunResult(
        mode="cluster",
        service_date=service_day.isoformat(),
        cluster=selected_cluster,
        total_rows=total_rows,
        unique_lines=unique_lines,
        success_count=success_count,
        error_count=error_count,
        summary_path=str(summary_path),
    )


def build_weekly_summary(output_dir: str | Path, service_date: date) -> Path:
    """Combine Sunday-Wednesday cluster summary files for the service week."""
    out_dir = Path(output_dir)
    week_start = _week_start_sunday(service_date)
    frames: list[pd.DataFrame] = []
    for offset in range(4):
        day = week_start + timedelta(days=offset)
        cluster = _cluster_for_service_date(day)
        if not cluster or cluster == WEEKLY_SUMMARY:
            continue
        path = _summary_path(out_dir, day.isoformat(), cluster)
        if path.exists():
            frames.append(pd.read_csv(path))

    iso_year, iso_week, _ = service_date.isocalendar()
    weekly_path = out_dir / f"weekly_summary_{iso_year}W{iso_week:02d}.csv"
    weekly_path.parent.mkdir(parents=True, exist_ok=True)
    if frames:
        pd.concat(frames, ignore_index=True).to_csv(weekly_path, index=False)
    else:
        pd.DataFrame().to_csv(weekly_path, index=False)
    return weekly_path


def _run_manifest_row(
    row: pd.Series,
    cluster: str,
    service_day: date,
    output_dir: Path,
) -> dict[str, Any]:
    base = _base_summary_row(row, cluster, service_day)
    request = LineReliabilityRequest(
        service_date=service_day.isoformat(),
        operator_ref=str(row["operator_ref"]),
        route_short_name=str(row["route_short_name"]),
        route_mkt=str(row["route_mkt"]),
        route_direction=str(row["route_direction"]),
        route_alternative=str(row["route_alternative"]),
    )
    try:
        _, kpi, metadata = analyze_line_reliability(
            OpenBusStrideClient(timeout_seconds=120, max_retries=2),
            request,
            output_dir=output_dir,
        )
    except Exception as exc:  # noqa: BLE001 - batch runner must continue.
        return {
            **base,
            "status": "error",
            "error_type": exc.__class__.__name__,
            "error_message": str(exc),
        }

    return {
        **base,
        "status": "ok",
        "selected_line_ref": metadata.get("selected_line_ref"),
        "csv_path": metadata.get("csv_path"),
        "html_path": metadata.get("html_path"),
        **kpi,
    }


def _base_summary_row(row: pd.Series, cluster: str, service_day: date) -> dict[str, Any]:
    return {
        "service_date": service_day.isoformat(),
        "cluster": cluster,
        "operator_ref": str(row["operator_ref"]),
        "route_short_name": str(row["route_short_name"]),
        "route_mkt": str(row["route_mkt"]),
        "route_direction": str(row["route_direction"]),
        "route_alternative": str(row["route_alternative"]),
    }


def _load_manifest(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise ClusterReliabilityRunnerError(f"Manifest not found: {path}")
    df = pd.read_csv(path, dtype=str).fillna("")
    missing = REQUIRED_MANIFEST_COLUMNS.difference(df.columns)
    if missing:
        raise ClusterReliabilityRunnerError(
            f"Manifest is missing required columns: {sorted(missing)}"
        )
    return df[list(REQUIRED_MANIFEST_COLUMNS)].copy()


def _summary_path(output_dir: str | Path, service_date: str, cluster: str) -> Path:
    cluster_slug = str(cluster).replace(" ", "_")
    return Path(output_dir) / f"summary_{service_date}_{cluster_slug}.csv"


def _resolve_service_date(value: str | date | None) -> date:
    if isinstance(value, date):
        return value
    if value:
        return datetime.strptime(value, "%Y-%m-%d").date()
    return (datetime.now(ISRAEL_TZ).date() - timedelta(days=1))


def _week_start_sunday(value: date) -> date:
    return value - timedelta(days=(value.weekday() + 1) % 7)


def _print_dry_run(cluster: str, service_day: date, df: pd.DataFrame) -> None:
    print(f"service_date: {service_day.isoformat()}")
    print(f"cluster: {cluster}")
    print(f"manifest_rows: {len(df)}")
    print(f"unique_route_short_names: {df['route_short_name'].nunique()}")
    for _, row in df.iterrows():
        identity = (
            f"{row['route_short_name']} | route_mkt={row['route_mkt']} | "
            f"direction={row['route_direction']} | alternative={row['route_alternative']}"
        )
        print(identity)


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entrypoint for scheduled cluster reliability runs."""
    parser = argparse.ArgumentParser(
        description="Run Metropoline line reliability analysis by scheduled cluster."
    )
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    parser.add_argument("--service-date", default=None, help="Service date in YYYY-MM-DD format. Default: yesterday in Asia/Jerusalem.")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--cluster", default=None, help="Manual cluster override for debugging.")
    parser.add_argument("--dry-run", action="store_true", help="Print selected route identities without API calls.")
    args = parser.parse_args(argv)

    try:
        result = run_cluster_reliability(
            manifest=args.manifest,
            service_date=args.service_date,
            output_dir=args.output_dir,
            cluster=args.cluster,
            dry_run=args.dry_run,
        )
    except ClusterReliabilityRunnerError as exc:
        parser.exit(2, f"cluster_reliability_runner: {exc}\n")

    print(f"mode: {result.mode}")
    print(f"service_date: {result.service_date}")
    print(f"cluster: {result.cluster}")
    print(f"manifest_rows: {result.total_rows}")
    print(f"unique_route_short_names: {result.unique_lines}")
    print(f"success_count: {result.success_count}")
    print(f"error_count: {result.error_count}")
    if result.summary_path:
        print(f"summary: {result.summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
