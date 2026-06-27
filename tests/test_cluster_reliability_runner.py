from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd

from src.cluster_reliability_runner import (
    WEEKLY_SUMMARY,
    _cluster_for_service_date,
    _summary_path,
    build_weekly_summary,
    run_cluster_reliability,
)


def _write_manifest(path: Path) -> None:
    pd.DataFrame(
        [
            {
                "cluster": "שרון חולון מרחבי",
                "operator_ref": "15",
                "route_short_name": "10",
                "route_mkt": "10010",
                "route_direction": "1",
                "route_alternative": "#",
            },
            {
                "cluster": "שרון חולון מרחבי",
                "operator_ref": "15",
                "route_short_name": "11",
                "route_mkt": "10011",
                "route_direction": "2",
                "route_alternative": "1",
            },
            {
                "cluster": "בקעת אונו אלעד",
                "operator_ref": "15",
                "route_short_name": "20",
                "route_mkt": "10020",
                "route_direction": "1",
                "route_alternative": "#",
            },
        ]
    ).to_csv(path, index=False)


def test_cluster_for_service_date_uses_israeli_service_week() -> None:
    assert _cluster_for_service_date(date(2026, 6, 28)) == "שרון חולון מרחבי"
    assert _cluster_for_service_date(date(2026, 6, 29)) == "בקעת אונו אלעד"
    assert _cluster_for_service_date(date(2026, 6, 30)) == "שרון"
    assert _cluster_for_service_date(date(2026, 7, 1)) == "הנגב"
    assert _cluster_for_service_date(date(2026, 7, 2)) == WEEKLY_SUMMARY
    assert _cluster_for_service_date(date(2026, 7, 3)) is None


def test_dry_run_reports_rows_and_unique_lines_without_analysis(tmp_path: Path, monkeypatch) -> None:
    manifest = tmp_path / "manifest.csv"
    _write_manifest(manifest)

    def fail_if_called(*args, **kwargs):
        raise AssertionError("analyze_line_reliability should not run in dry-run")

    monkeypatch.setattr(
        "src.cluster_reliability_runner.analyze_line_reliability",
        fail_if_called,
    )

    result = run_cluster_reliability(
        manifest=manifest,
        service_date="2026-06-28",
        output_dir=tmp_path / "out",
        dry_run=True,
    )

    assert result.mode == "dry-run"
    assert result.cluster == "שרון חולון מרחבי"
    assert result.total_rows == 2
    assert result.unique_lines == 2
    assert result.summary_path is None


def test_runner_continues_after_line_error_and_writes_summary(tmp_path: Path, monkeypatch) -> None:
    manifest = tmp_path / "manifest.csv"
    _write_manifest(manifest)
    calls = []

    def fake_analyze(client, request, *, output_dir):
        calls.append(request)
        if request.route_short_name == "11":
            raise RuntimeError("boom")
        return (
            pd.DataFrame([{"planned_start_time": "2026-06-28T08:00:00"}]),
            {"planned_rides": 1, "actual_rides": 1, "matched_rides": 1},
            {
                "selected_line_ref": "L-10",
                "csv_path": str(Path(output_dir) / "line.csv"),
                "html_path": str(Path(output_dir) / "line.html"),
            },
        )

    monkeypatch.setattr(
        "src.cluster_reliability_runner.analyze_line_reliability",
        fake_analyze,
    )

    result = run_cluster_reliability(
        manifest=manifest,
        service_date="2026-06-28",
        output_dir=tmp_path / "out",
    )

    assert result.mode == "cluster"
    assert result.error_count == 1
    assert [request.route_mkt for request in calls] == ["10010", "10011"]
    assert [request.route_direction for request in calls] == ["1", "2"]

    summary = pd.read_csv(result.summary_path)
    assert list(summary["status"]) == ["ok", "error"]
    assert summary.loc[0, "selected_line_ref"] == "L-10"
    assert summary.loc[1, "error_message"] == "boom"


def test_drop_day_is_excluded_without_running(tmp_path: Path, monkeypatch) -> None:
    manifest = tmp_path / "manifest.csv"
    _write_manifest(manifest)

    def fail_if_called(*args, **kwargs):
        raise AssertionError("analysis must not run on a dropped holiday")

    monkeypatch.setattr(
        "src.cluster_reliability_runner.analyze_line_reliability",
        fail_if_called,
    )

    # 2026-04-22 — יום העצמאות (national drop day).
    result = run_cluster_reliability(
        manifest=manifest,
        service_date="2026-04-22",
        output_dir=tmp_path / "out",
        cluster="שרון חולון מרחבי",
    )

    assert result.mode == "excluded"
    assert result.exclusion_treatment == "drop"
    assert result.summary_path is None


def test_segment_day_runs_and_tags_summary(tmp_path: Path, monkeypatch) -> None:
    manifest = tmp_path / "manifest.csv"
    _write_manifest(manifest)

    def fake_analyze(client, request, *, output_dir):
        return (
            pd.DataFrame([{"planned_start_time": "2026-07-15T08:00:00"}]),
            {"planned_rides": 1, "actual_rides": 1, "matched_rides": 1},
            {"selected_line_ref": "L-10", "csv_path": "c", "html_path": "h"},
        )

    monkeypatch.setattr(
        "src.cluster_reliability_runner.analyze_line_reliability",
        fake_analyze,
    )

    # 2026-07-15 — חופש גדול (national segment period).
    result = run_cluster_reliability(
        manifest=manifest,
        service_date="2026-07-15",
        output_dir=tmp_path / "out",
        cluster="שרון חולון מרחבי",
    )

    assert result.mode == "cluster"
    assert result.exclusion_treatment == "segment"
    summary = pd.read_csv(result.summary_path)
    assert set(summary["exclusion_treatment"]) == {"segment"}


def test_ignore_exclusions_runs_on_a_drop_day(tmp_path: Path, monkeypatch) -> None:
    manifest = tmp_path / "manifest.csv"
    _write_manifest(manifest)
    monkeypatch.setattr(
        "src.cluster_reliability_runner.analyze_line_reliability",
        lambda *a, **k: (pd.DataFrame(), {"planned_rides": 0}, {"selected_line_ref": None, "csv_path": "", "html_path": ""}),
    )

    result = run_cluster_reliability(
        manifest=manifest,
        service_date="2026-04-22",
        output_dir=tmp_path / "out",
        cluster="שרון חולון מרחבי",
        ignore_exclusions=True,
    )

    assert result.mode == "cluster"
    assert result.exclusion_treatment == "normal"


def test_weekly_summary_drops_segment_rows(tmp_path: Path) -> None:
    normal = _summary_path(tmp_path, "2026-06-28", "שרון חולון מרחבי")
    segment = _summary_path(tmp_path, "2026-06-29", "בקעת אונו אלעד")
    normal.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame([{"service_date": "2026-06-28", "exclusion_treatment": "normal"}]).to_csv(normal, index=False)
    pd.DataFrame([{"service_date": "2026-06-29", "exclusion_treatment": "segment"}]).to_csv(segment, index=False)

    path = build_weekly_summary(tmp_path, date(2026, 7, 2))

    weekly = pd.read_csv(path)
    assert list(weekly["service_date"]) == ["2026-06-28"]


def test_build_weekly_summary_combines_previous_service_days(tmp_path: Path) -> None:
    first = _summary_path(tmp_path, "2026-06-28", "שרון חולון מרחבי")
    second = _summary_path(tmp_path, "2026-06-29", "בקעת אונו אלעד")
    first.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame([{"service_date": "2026-06-28", "status": "ok"}]).to_csv(first, index=False)
    pd.DataFrame([{"service_date": "2026-06-29", "status": "error"}]).to_csv(second, index=False)

    path = build_weekly_summary(tmp_path, date(2026, 7, 2))

    assert path == tmp_path / "weekly_summary_2026W27.csv"
    weekly = pd.read_csv(path)
    assert list(weekly["service_date"]) == ["2026-06-28", "2026-06-29"]
