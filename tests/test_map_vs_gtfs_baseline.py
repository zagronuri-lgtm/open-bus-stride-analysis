import json
from pathlib import Path

import pandas as pd
import pytest

from src.map_vs_gtfs_baseline import (
    MapVsGtfsRequest,
    coverage_matrix,
    deadhead_catalog_checks,
    detect_stale_snapshot,
    gtfs_rides_to_local,
    load_map_export,
    match_departures,
    run_analysis,
    travel_time_gaps,
)


def _write_map_export(directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    trips = pd.DataFrame(
        [
            # 09:00 local, 91 minutes
            {"trip_id": "22073_1_0_09:00_fri", "route_id": "22073-1-0", "mkt": "22073", "line_sign": "73",
             "direction": 1, "alternative": 0, "departure": "0.09:00", "arrival": "0.10:31",
             "dep_min": 540, "duration_min": 91, "assigned": True},
            # 10:15 local — exists only in the map
            {"trip_id": "22073_1_0_10:15_fri", "route_id": "22073-1-0", "mkt": "22073", "line_sign": "73",
             "direction": 1, "alternative": 0, "departure": "0.10:15", "arrival": "0.11:45",
             "dep_min": 615, "duration_min": 90, "assigned": True},
            # stale mkt — not in GTFS for the target date
            {"trip_id": "99999_1_0_08:00_fri", "route_id": "99999-1-0", "mkt": "99999", "line_sign": "5",
             "direction": 1, "alternative": 0, "departure": "0.08:00", "arrival": "0.08:30",
             "dep_min": 480, "duration_min": 30, "assigned": False},
        ]
    )
    routes = pd.DataFrame(
        [
            {"route_id": "22073-1-0", "mkt": "22073", "line_sign": "73", "direction": 1, "alternative": 0,
             "origin_stop_id": 63450, "origin_name": "גני ילדים/היובל", "dest_stop_id": 39522,
             "dest_name": "ת. מרכזית נתניה/הורדה", "catalog_number": "22073"},
            {"route_id": "99999-1-0", "mkt": "99999", "line_sign": "5", "direction": 1, "alternative": 0,
             "origin_stop_id": 100, "origin_name": "מסוף חריש/איסוף", "dest_stop_id": 200,
             "dest_name": "מרכז אריאל", "catalog_number": "99999"},
        ]
    )
    deadheads = pd.DataFrame(
        [
            # zero-time leg with real distance — catalog defect
            {"origin_stop_id": 60777, "origin_name": "בריכה", "dest_stop_id": 43898,
             "dest_name": "מסוף חריש/איסוף", "type": "deadhead", "travel_time_min": 0,
             "distance_km": 17.0, "dep_min": 870, "duty_id": ""},
            # unrealistic speed: 60 km in 20 minutes = 180 km/h
            {"origin_stop_id": 1, "origin_name": "א", "dest_stop_id": 2, "dest_name": "ב",
             "type": "deadhead", "travel_time_min": 20, "distance_km": 60.0, "dep_min": 500, "duty_id": ""},
            # pull-out with asymmetric reverse pull-in (1 min out vs 9 min in)
            {"origin_stop_id": 98001, "origin_name": "חניון T אריאל", "dest_stop_id": 63401,
             "dest_name": "בית כנסת", "type": "depot_pull_out", "travel_time_min": 1,
             "distance_km": 0.7, "dep_min": 454, "duty_id": ""},
            {"origin_stop_id": 63401, "origin_name": "בית כנסת", "dest_stop_id": 98001,
             "dest_name": "חניון T אריאל", "type": "depot_pull_in", "travel_time_min": 9,
             "distance_km": 6.3, "dep_min": 892, "duty_id": ""},
            # pull-out with no reverse pull-in at all
            {"origin_stop_id": 98002, "origin_name": "חניון T קרני שומרון", "dest_stop_id": 63294,
             "dest_name": "הרב פנחס לוין", "type": "depot_pull_out", "travel_time_min": 9,
             "distance_km": 6.3, "dep_min": 400, "duty_id": ""},
            # normal symmetric leg
            {"origin_stop_id": 98001, "origin_name": "חניון T אריאל", "dest_stop_id": 500,
             "dest_name": "מסוף", "type": "depot_pull_out", "travel_time_min": 10,
             "distance_km": 8.0, "dep_min": 300, "duty_id": ""},
            {"origin_stop_id": 500, "origin_name": "מסוף", "dest_stop_id": 98001,
             "dest_name": "חניון T אריאל", "type": "depot_pull_in", "travel_time_min": 11,
             "distance_km": 8.0, "dep_min": 900, "duty_id": ""},
        ]
    )
    trips.to_csv(directory / "trips.csv", index=False)
    routes.to_csv(directory / "routes.csv", index=False)
    deadheads.to_csv(directory / "deadheads.csv", index=False)
    return directory


def _gtfs_fixtures() -> tuple[pd.DataFrame, pd.DataFrame]:
    gtfs_routes = pd.DataFrame(
        [
            {"id": 1, "date": "2026-07-10", "line_ref": 29708, "operator_ref": 34,
             "route_short_name": "73",
             "route_long_name": "גני ילדים/היובל-קדומים<->ת. מרכזית נתניה/הורדה-נתניה-10",
             "route_mkt": "22073", "route_direction": "1", "route_alternative": "0",
             "agency_name": "תנופה", "route_type": "3"},
            # new mkt in GTFS, same endpoints as the stale map route 99999
            {"id": 2, "date": "2026-07-10", "line_ref": 30001, "operator_ref": 34,
             "route_short_name": "5",
             "route_long_name": "מסוף חריש/איסוף-חריש<->מרכז אריאל-אריאל-20",
             "route_mkt": "88888", "route_direction": "1", "route_alternative": "0",
             "agency_name": "תנופה", "route_type": "3"},
        ]
    )
    gtfs_rides = pd.DataFrame(
        [
            # 06:00 UTC = 09:00 Israel summer time, 74.3 min scheduled
            {"id": 101, "gtfs_route_id": 1, "journey_ref": "j1",
             "start_time": "2026-07-10T06:00:00+00:00", "end_time": "2026-07-10T07:14:16+00:00",
             "gtfs_route__route_mkt": "22073", "gtfs_route__route_direction": "1",
             "gtfs_route__route_alternative": "0",
             "gtfs_route__route_long_name": "גני ילדים/היובל-קדומים<->ת. מרכזית נתניה/הורדה-נתניה-10"},
            # 09:30 UTC = 12:30 local — exists only in GTFS
            {"id": 102, "gtfs_route_id": 1, "journey_ref": "j2",
             "start_time": "2026-07-10T09:30:00+00:00", "end_time": "2026-07-10T10:44:00+00:00",
             "gtfs_route__route_mkt": "22073", "gtfs_route__route_direction": "1",
             "gtfs_route__route_alternative": "0",
             "gtfs_route__route_long_name": "גני ילדים/היובל-קדומים<->ת. מרכזית נתניה/הורדה-נתניה-10"},
            # ride of the new mkt — outside map scope
            {"id": 103, "gtfs_route_id": 2, "journey_ref": "j3",
             "start_time": "2026-07-10T05:00:00+00:00", "end_time": "2026-07-10T05:30:00+00:00",
             "gtfs_route__route_mkt": "88888", "gtfs_route__route_direction": "1",
             "gtfs_route__route_alternative": "0",
             "gtfs_route__route_long_name": "מסוף חריש/איסוף-חריש<->מרכז אריאל-אריאל-20"},
        ]
    )
    return gtfs_routes, gtfs_rides


def test_gtfs_rides_to_local_summer_offset():
    _, gtfs_rides = _gtfs_fixtures()
    local = gtfs_rides_to_local(gtfs_rides, "2026-07-10")

    ride = local[local["gtfs_ride_id"] == 101].iloc[0]
    assert ride["dep_local"] == "09:00"
    assert ride["dep_min"] == 540  # UTC+3 in July
    assert ride["duration_min"] == pytest.approx(74.3, abs=0.05)
    assert ride["route_key"] == "22073-1-0"


def test_gtfs_rides_to_local_winter_offset_and_after_midnight():
    gtfs_rides = pd.DataFrame(
        [
            # 07:00 UTC in January = 09:00 Israel winter time (UTC+2)
            {"id": 1, "gtfs_route_id": 1, "journey_ref": "w1",
             "start_time": "2026-01-09T07:00:00+00:00", "end_time": "2026-01-09T08:00:00+00:00",
             "gtfs_route__route_mkt": "11026", "gtfs_route__route_direction": "1",
             "gtfs_route__route_alternative": "0", "gtfs_route__route_long_name": "א<->ב-1"},
            # 22:15 UTC = 00:15 next local day -> 1455 minutes from Friday midnight
            {"id": 2, "gtfs_route_id": 1, "journey_ref": "w2",
             "start_time": "2026-01-09T22:15:00+00:00", "end_time": "2026-01-09T23:00:00+00:00",
             "gtfs_route__route_mkt": "11026", "gtfs_route__route_direction": "1",
             "gtfs_route__route_alternative": "0", "gtfs_route__route_long_name": "א<->ב-1"},
        ]
    )
    local = gtfs_rides_to_local(gtfs_rides, "2026-01-09")

    assert local.loc[local["gtfs_ride_id"] == 1, "dep_min"].iloc[0] == 540
    assert local.loc[local["gtfs_ride_id"] == 2, "dep_min"].iloc[0] == 1455
    assert local.loc[local["gtfs_ride_id"] == 2, "dep_local"].iloc[0] == "00:15"


def test_coverage_matrix_counts_and_status(tmp_path):
    export = load_map_export(_write_map_export(tmp_path / "map"))
    _, gtfs_rides = _gtfs_fixtures()
    gtfs_local = gtfs_rides_to_local(gtfs_rides, "2026-07-10")
    gtfs_in_scope = gtfs_local[gtfs_local["mkt"].isin(set(export.trips["mkt"].astype(str)))]

    matrix = coverage_matrix(export.trips, gtfs_in_scope)

    row_22073 = matrix[matrix["mkt"] == "22073"].iloc[0]
    assert row_22073["map_departures"] == 2
    assert row_22073["gtfs_departures"] == 2
    assert row_22073["status"] == "match"

    row_stale = matrix[matrix["mkt"] == "99999"].iloc[0]
    assert row_stale["map_departures"] == 1
    assert row_stale["gtfs_departures"] == 0
    assert row_stale["status"] == "map_only"


def test_match_departures_exact_minute(tmp_path):
    export = load_map_export(_write_map_export(tmp_path / "map"))
    _, gtfs_rides = _gtfs_fixtures()
    gtfs_local = gtfs_rides_to_local(gtfs_rides, "2026-07-10")
    gtfs_in_scope = gtfs_local[gtfs_local["mkt"].isin(set(export.trips["mkt"].astype(str)))]

    matched, map_only, gtfs_only = match_departures(export.trips, gtfs_in_scope, tolerance_min=0)

    assert len(matched) == 1
    assert matched.iloc[0]["trip_id"] == "22073_1_0_09:00_fri"
    assert matched.iloc[0]["gtfs_ride_id"] == 101
    assert matched.iloc[0]["dep_delta_min"] == 0

    assert set(map_only["trip_id"]) == {"22073_1_0_10:15_fri", "99999_1_0_08:00_fri"}
    assert set(gtfs_only["gtfs_ride_id"]) == {102}


def test_match_departures_with_tolerance():
    map_trips = pd.DataFrame(
        [{"trip_id": "t1", "route_key": "1-1-0", "dep_min": 540, "duration_min": 60}]
    )
    gtfs_local = pd.DataFrame(
        [
            {"gtfs_ride_id": 9, "route_key": "1-1-0", "dep_min": 542, "dep_local": "09:02", "duration_min": 55},
            {"gtfs_ride_id": 10, "route_key": "1-1-0", "dep_min": 549, "dep_local": "09:09", "duration_min": 55},
        ]
    )
    matched, map_only, gtfs_only = match_departures(map_trips, gtfs_local, tolerance_min=3)

    assert len(matched) == 1
    assert matched.iloc[0]["gtfs_ride_id"] == 9  # nearest within tolerance
    assert matched.iloc[0]["dep_delta_min"] == -2
    assert map_only.empty
    assert set(gtfs_only["gtfs_ride_id"]) == {10}


def test_travel_time_gaps_report():
    matched = pd.DataFrame(
        [
            {"route_key": "22073-1-0", "map_duration_min": 91, "gtfs_duration_min": 74.3},
            {"route_key": "22073-1-0", "map_duration_min": 90, "gtfs_duration_min": 74.0},
        ]
    )
    report = travel_time_gaps(matched)

    assert len(report) == 1
    row = report.iloc[0]
    assert row["matched_trips"] == 2
    assert row["mean_gap_min"] == pytest.approx(16.4, abs=0.05)
    assert row["max_abs_gap_min"] == pytest.approx(16.7, abs=0.05)


def test_detect_stale_snapshot_flags_replaced_mkt(tmp_path):
    export = load_map_export(_write_map_export(tmp_path / "map"))
    gtfs_routes, _ = _gtfs_fixtures()

    map_missing, gtfs_new = detect_stale_snapshot(export.routes, gtfs_routes)

    assert set(map_missing["mkt"].astype(str)) == {"99999"}
    assert set(gtfs_new["route_mkt"].astype(str)) == {"88888"}
    replacement = gtfs_new.iloc[0]
    assert replacement["best_map_mkt_by_endpoints"] == "99999"
    assert replacement["likely_replacement"]


def test_deadhead_catalog_checks(tmp_path):
    export = load_map_export(_write_map_export(tmp_path / "map"))
    checks = deadhead_catalog_checks(export.deadheads, speed_warning_kmh=90)

    assert checks["legs_total"] == 7
    assert checks["zero_time_count"] == 1
    assert checks["zero_time_legs"].iloc[0]["origin_stop_id"] == 60777
    assert checks["speeding_count"] == 1
    assert checks["speeding_legs"].iloc[0]["speed_kmh"] == 180.0
    assert checks["speed_distribution_kmh"]["max"] == 180.0

    asymmetry = checks["pull_asymmetry"]
    issues = {
        (row["depot_stop_id"], row["service_stop_id"]): row["issue"]
        for _, row in asymmetry.iterrows()
    }
    assert issues[("98001", "63401")] == "time_asymmetry"
    assert issues[("98002", "63294")] == "missing_direction"
    assert ("98001", "500") not in issues  # symmetric pair is not flagged


def test_run_analysis_offline_end_to_end(tmp_path):
    map_dir = _write_map_export(tmp_path / "map")
    gtfs_routes, gtfs_rides = _gtfs_fixtures()
    out_dir = tmp_path / "out"

    request = MapVsGtfsRequest(
        map_export_dir=map_dir,
        service_date="2026-07-10",
        operator_ref=34,
        output_dir=out_dir,
    )
    summary = run_analysis(request, gtfs_routes=gtfs_routes, gtfs_rides=gtfs_rides)

    assert summary["map_trips"] == 3
    assert summary["matched_departures"] == 1
    assert summary["map_only_departures"] == 2
    assert summary["gtfs_only_departures"] == 1
    assert summary["departure_match_rate"] == pytest.approx(1 / 3, abs=1e-4)
    assert summary["stale_map_mkts"] == ["99999"]
    assert summary["new_gtfs_mkts"] == ["88888"]
    assert summary["deadhead_checks"]["zero_time_count"] == 1
    assert summary["deadhead_checks"]["pull_asymmetry_count"] == 2

    for path in summary["csv_paths"].values():
        assert Path(path).exists()
    saved = json.loads(Path(summary["summary_path"]).read_text(encoding="utf-8"))
    assert saved["service_date"] == "2026-07-10"


def test_load_map_export_missing_column(tmp_path):
    map_dir = _write_map_export(tmp_path / "map")
    trips = pd.read_csv(map_dir / "trips.csv").drop(columns=["dep_min"])
    trips.to_csv(map_dir / "trips.csv", index=False)

    with pytest.raises(ValueError, match="dep_min"):
        load_map_export(map_dir)


def test_run_analysis_rejects_bad_date(tmp_path):
    request = MapVsGtfsRequest(map_export_dir=tmp_path, service_date="10/07/2026")
    with pytest.raises(ValueError, match="YYYY-MM-DD"):
        run_analysis(request, gtfs_routes=pd.DataFrame(), gtfs_rides=pd.DataFrame())
