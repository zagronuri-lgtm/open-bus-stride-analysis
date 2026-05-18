from pathlib import Path

import pandas as pd
import pytest

from src.line_reliability_analyzer import (
    LineReliabilityRequest,
    LineSelectionError,
    analyze_line_reliability,
    select_line_ref,
    validate_data_quality,
)
from src.open_bus_stride_client import OpenBusStrideClient


def test_analyze_line_reliability_with_explicit_line_ref(monkeypatch, tmp_path):
    def fake_get_df(self, path, **params):
        if path == "/rides_execution/list":
            assert params["line_ref"] == "L-1"
            assert params["operator_ref"] == "3"
            return pd.DataFrame(
                [
                    {"planned_start_time": "2026-01-01T08:00:00", "actual_start_time": "2026-01-01T08:03:00", "gtfs_ride_id": "r1"},
                    {"planned_start_time": "2026-01-01T09:00:00", "actual_start_time": None, "gtfs_ride_id": None},
                ]
            )
        if path == "/siri_snapshots/list":
            return pd.DataFrame([{"etl_status": "loaded", "error": None}])
        raise AssertionError(f"Unexpected path {path}")

    monkeypatch.setattr(OpenBusStrideClient, "get_df", fake_get_df)

    request = LineReliabilityRequest(
        service_date="2026-01-01",
        operator_ref="3",
        route_short_name="18",
        line_ref="L-1",
    )
    df, kpi, metadata = analyze_line_reliability(OpenBusStrideClient(), request, output_dir=tmp_path)

    assert len(df) == 2
    assert kpi["planned_rides"] == 2
    assert kpi["actual_rides"] == 1
    assert kpi["matched_rides"] == 1
    assert kpi["missing_actual_start"] == 1
    assert kpi["null_gtfs_ride_id_rate"] == 0.5
    assert {check["name"] for check in metadata["data_quality"]} == {
        "line_identity",
        "date_time_window",
        "gtfs_ride_id_missing_rate",
        "siri_snapshots",
    }
    assert Path(metadata["csv_path"]).exists()
    assert Path(metadata["html_path"]).exists()


def test_analyze_line_reliability_resolves_unique_line_ref_with_filters(monkeypatch, tmp_path):
    captured_filters = {}

    def fake_find_line_refs(*args, **kwargs):
        captured_filters.update(kwargs)

        return pd.DataFrame(
            [{"line_ref": "L-77", "route_mkt": "mkt-1", "route_direction": 1, "route_alternative": "#"}]
        )

    def fake_get_df(self, path, **params):
        if path == "/rides_execution/list":
            assert params["line_ref"] == "L-77"
            return pd.DataFrame(
                [
                    {"planned_start_time": "2026-01-01T07:00:00", "actual_start_time": "2026-01-01T07:00:00", "gtfs_ride_id": None},
                    {"planned_start_time": "2026-01-01T10:00:00", "actual_start_time": "2026-01-01T10:00:00", "gtfs_ride_id": "r2"},
                ]
            )
        if path == "/siri_snapshots/list":
            return pd.DataFrame([{"etl_status": "loaded", "error": None}])
        raise AssertionError(f"Unexpected path {path}")

    monkeypatch.setattr("src.line_reliability_analyzer.find_line_refs", fake_find_line_refs)
    monkeypatch.setattr(OpenBusStrideClient, "get_df", fake_get_df)

    request = LineReliabilityRequest(
        service_date="2026-01-01",
        operator_ref="3",
        route_short_name="18",
        route_mkt="mkt-1",
        route_direction=1,
        route_alternative="#",
        hour_from=8,
        hour_to=10,
    )

    df, kpi, metadata = analyze_line_reliability(OpenBusStrideClient(), request, output_dir=tmp_path)

    assert len(df) == 1
    assert kpi["planned_rides"] == 1
    assert kpi["actual_rides"] == 1
    assert kpi["matched_rides"] == 1
    assert kpi["null_gtfs_ride_id_rate"] == 0.0
    assert metadata["selected_line_ref"] == "L-77"
    assert len(metadata["line_ref_candidates"]) == 1
    assert captured_filters["route_mkt"] == "mkt-1"
    assert captured_filters["route_direction"] == 1
    assert captured_filters["route_alternative"] == "#"


def test_select_line_ref_raises_when_candidates_are_ambiguous():
    candidates = pd.DataFrame(
        [
            {"line_ref": "L-1", "route_mkt": "m1", "route_direction": 1, "route_alternative": "#"},
            {"line_ref": "L-2", "route_mkt": "m1", "route_direction": 2, "route_alternative": "#"},
        ]
    )
    request = LineReliabilityRequest(
        service_date="2026-01-01",
        operator_ref="3",
        route_short_name="18",
        route_mkt="m1",
    )

    with pytest.raises(LineSelectionError) as exc:
        select_line_ref(candidates, request)

    assert len(exc.value.candidates) == 2
    assert exc.value.filters["route_mkt"] == "m1"


def test_select_line_ref_uses_direction_and_alternative_to_disambiguate():
    candidates = pd.DataFrame(
        [
            {"line_ref": "L-1", "route_mkt": "m1", "route_direction": 1, "route_alternative": "#"},
            {"line_ref": "L-2", "route_mkt": "m1", "route_direction": 2, "route_alternative": "#"},
        ]
    )
    request = LineReliabilityRequest(
        service_date="2026-01-01",
        operator_ref="3",
        route_short_name="18",
        route_mkt="m1",
        route_direction=2,
        route_alternative="#",
    )

    assert select_line_ref(candidates, request) == "L-2"


def test_validate_data_quality_flags_missing_gtfs_and_snapshot_errors():
    class DummyClient(OpenBusStrideClient):
        def get_df(self, path, **params):
            assert path == "/siri_snapshots/list"
            return pd.DataFrame([{"etl_status": "failed", "error": "parse error"}])

    rides_df = pd.DataFrame(
        [
            {"planned_start_time": "2026-01-01T08:00:00", "actual_start_time": "2026-01-01T08:01:00", "gtfs_ride_id": ""},
            {"planned_start_time": "2026-01-01T09:00:00", "actual_start_time": None, "gtfs_ride_id": None},
        ]
    )
    request = LineReliabilityRequest(
        service_date="2026-01-01",
        operator_ref="3",
        route_short_name="18",
        line_ref="L-1",
        max_null_gtfs_ride_id_rate=0.25,
    )

    checks = validate_data_quality(
        DummyClient(),
        rides_df,
        request,
        selected_line_ref="L-1",
        line_candidates=pd.DataFrame(),
    )
    by_name = {check.name: check for check in checks}

    assert by_name["gtfs_ride_id_missing_rate"].status == "warning"
    assert by_name["gtfs_ride_id_missing_rate"].value.startswith("2/2")
    assert by_name["siri_snapshots"].status == "warning"


def test_analyze_line_reliability_validates_hour_window():
    request = LineReliabilityRequest(
        service_date="2026-01-01",
        operator_ref="3",
        route_short_name="18",
        line_ref="L-1",
        hour_from=18,
        hour_to=8,
    )

    with pytest.raises(ValueError, match="hour_from"):
        analyze_line_reliability(OpenBusStrideClient(), request)
