from pathlib import Path

from src.line_reliability_analyzer import LineReliabilityRequest, analyze_line_reliability
from src.open_bus_stride_client import OpenBusStrideClient


def test_analyze_line_reliability_with_explicit_line_ref(monkeypatch, tmp_path):
    def fake_get_df(self, path, **params):
        assert path == "/rides_execution/list"
        assert params["gtfs_route__line_ref"] == "L-1"
        import pandas as pd

        return pd.DataFrame(
            [
                {"planned_start_time": "2026-01-01T08:00:00", "actual_start_time": "2026-01-01T08:03:00", "gtfs_ride_id": "r1"},
                {"planned_start_time": "2026-01-01T09:00:00", "actual_start_time": None, "gtfs_ride_id": None},
            ]
        )

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
    assert Path(metadata["csv_path"]).exists()
    assert Path(metadata["html_path"]).exists()


def test_analyze_line_reliability_resolves_line_ref(monkeypatch, tmp_path):
    def fake_find_line_refs(*args, **kwargs):
        import pandas as pd

        return pd.DataFrame(
            [{"line_ref": "L-77", "route_mkt": "mkt-1", "route_direction": 1, "route_alternative": "#"}]
        )

    def fake_get_df(self, path, **params):
        import pandas as pd

        assert path == "/rides_execution/list"
        assert params["gtfs_route__line_ref"] == "L-77"
        return pd.DataFrame(
            [
                {"planned_start_time": "2026-01-01T07:00:00", "actual_start_time": "2026-01-01T07:00:00", "gtfs_ride_id": None},
                {"planned_start_time": "2026-01-01T10:00:00", "actual_start_time": "2026-01-01T10:00:00", "gtfs_ride_id": "r2"},
            ]
        )

    monkeypatch.setattr("src.line_reliability_analyzer.find_line_refs", fake_find_line_refs)
    monkeypatch.setattr(OpenBusStrideClient, "get_df", fake_get_df)

    request = LineReliabilityRequest(
        service_date="2026-01-01",
        operator_ref="3",
        route_short_name="18",
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
