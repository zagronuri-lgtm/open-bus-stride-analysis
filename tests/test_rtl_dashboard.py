"""Tests for src/rtl_dashboard.py — no network calls."""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from src.rtl_dashboard import _hour_series, _kpi_from_df, build_dashboard


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_df(
    n_planned: int = 10,
    n_actual: int = 7,
    n_matched: int = 6,
    with_times: bool = True,
) -> pd.DataFrame:
    """Create a minimal rides-execution DataFrame for testing."""
    rows = []
    base_hour = 8
    for i in range(n_planned):
        planned_time = f"2024-01-15 {base_hour + (i % 3):02d}:00:00"
        actual_time = f"2024-01-15 {base_hour + (i % 3):02d}:05:00" if i < n_actual else None
        gtfs_id = f"gtfs_{i}" if i < n_matched else None
        rows.append(
            {
                "planned_start_time": planned_time if with_times else None,
                "actual_start_time": actual_time,
                "gtfs_ride_id": gtfs_id,
            }
        )
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Unit tests: _kpi_from_df
# ---------------------------------------------------------------------------

def test_kpi_planned_count() -> None:
    df = _make_df(n_planned=10, n_actual=7, n_matched=6)
    kpi = _kpi_from_df(df)
    assert kpi["planned_rides"] == 10


def test_kpi_actual_count() -> None:
    df = _make_df(n_planned=10, n_actual=7, n_matched=6)
    kpi = _kpi_from_df(df)
    assert kpi["actual_rides"] == 7


def test_kpi_matched_count() -> None:
    df = _make_df(n_planned=10, n_actual=7, n_matched=6)
    kpi = _kpi_from_df(df)
    assert kpi["matched_rides"] == 6


def test_kpi_missing_count() -> None:
    df = _make_df(n_planned=10, n_actual=7)
    kpi = _kpi_from_df(df)
    assert kpi["missing_rides"] == 3


def test_kpi_execution_rate() -> None:
    df = _make_df(n_planned=10, n_actual=8)
    kpi = _kpi_from_df(df)
    assert kpi["execution_rate_pct"] == 80.0


def test_kpi_empty_df_does_not_raise() -> None:
    df = pd.DataFrame(columns=["planned_start_time", "actual_start_time", "gtfs_ride_id"])
    kpi = _kpi_from_df(df)
    assert kpi["planned_rides"] == 0
    assert kpi["execution_rate_pct"] == 0.0


# ---------------------------------------------------------------------------
# Unit tests: _hour_series
# ---------------------------------------------------------------------------

def test_hour_series_returns_correct_lengths() -> None:
    df = _make_df(n_planned=9, n_actual=6, with_times=True)
    labels, planned, actual = _hour_series(df)
    assert len(labels) == len(planned) == len(actual)


def test_hour_series_no_planned_col_returns_empty() -> None:
    df = pd.DataFrame({"actual_start_time": ["2024-01-15 08:00:00"]})
    labels, planned, actual = _hour_series(df)
    assert labels == [] and planned == [] and actual == []


def test_hour_series_planned_counts_correct() -> None:
    # 3 planned at 08:xx, 3 at 09:xx, 3 at 10:xx
    df = _make_df(n_planned=9, n_actual=0, with_times=True)
    labels, planned, _ = _hour_series(df)
    assert sum(planned) == 9


# ---------------------------------------------------------------------------
# Integration test: build_dashboard writes valid HTML
# ---------------------------------------------------------------------------

def test_build_dashboard_creates_html_file(tmp_path: Path) -> None:
    df = _make_df()
    csv_path = tmp_path / "reliability.csv"
    df.to_csv(csv_path, index=False)

    out = build_dashboard(csv_path, tmp_path / "dashboard.html", title="בדיקה")
    assert out.exists()
    text = out.read_text(encoding="utf-8")
    assert "<!doctype html" in text.lower()
    assert 'dir="rtl"' in text
    assert "בדיקה" in text  # custom title
    assert "hourChart" in text
    assert "kpi-grid" in text


def test_build_dashboard_chart_data_embedded(tmp_path: Path) -> None:
    df = _make_df(n_planned=6, n_actual=4, with_times=True)
    csv_path = tmp_path / "rel.csv"
    df.to_csv(csv_path, index=False)
    out = build_dashboard(csv_path)
    text = out.read_text(encoding="utf-8")
    # The chart data JSON object must be present
    assert '"labels"' in text
    assert '"planned"' in text
    assert '"actual"' in text


def test_build_dashboard_default_output_path(tmp_path: Path) -> None:
    df = _make_df()
    csv_path = tmp_path / "test_file.csv"
    df.to_csv(csv_path, index=False)
    out = build_dashboard(csv_path)
    assert out.name == "test_file.dashboard.html"
    assert out.exists()
