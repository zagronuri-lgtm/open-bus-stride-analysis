"""Offline unit tests for the stride_analysis package (no network calls)."""

from __future__ import annotations

import pandas as pd
import pytest

from stride_analysis.analysis.compare import compare_periods
from stride_analysis.analysis.execution import calc_execution, execution_totals
from stride_analysis.analysis.penalties import (
    BandTable,
    calc_penalties,
    load_penalty_tables,
    penalties_by_operator,
)
from stride_analysis.analysis.punctuality import add_delay_minutes, calc_punctuality
from stride_analysis.bot.handler import parse_question
from stride_analysis.config import PENALTIES_WORKBOOK
from stride_analysis.data.cache import Cache
from stride_analysis.data.models import Operator, Ride, Route


# --------------------------------------------------------------------------- #
# execution
# --------------------------------------------------------------------------- #
def test_calc_execution_from_raw_rides():
    rides = pd.DataFrame(
        {
            "operator_ref": [5, 5, 5, 5],
            "service_date": ["2026-05-24"] * 4,
            "executed": [True, True, False, True],
        }
    )
    summary = calc_execution(rides, group_by=["operator_ref", "service_date"])
    row = summary.iloc[0]
    assert row["planned"] == 4
    assert row["executed"] == 3
    assert row["missing"] == 1
    assert row["missing_pct"] == 25.0


def test_calc_execution_from_aggregated_counts():
    agg = pd.DataFrame(
        {
            "operator_ref": [5, 3],
            "operator_name": ["דן", "אגד"],
            "service_date": ["2026-05-24", "2026-05-24"],
            "planned": [100, 200],
            "executed": [99, 180],
        }
    )
    summary = calc_execution(agg, group_by=["operator_ref", "operator_name", "service_date"])
    assert set(summary["missing"]) == {1, 20}
    totals = execution_totals(summary)
    assert totals["planned"] == 300
    assert totals["missing"] == 21


def test_calc_execution_empty():
    out = calc_execution(pd.DataFrame())
    assert list(out.columns)[-4:] == ["planned", "executed", "missing", "missing_pct"]
    assert out.empty


def test_calc_execution_derives_executed_from_actual():
    rides = pd.DataFrame(
        {
            "operator_ref": [1, 1],
            "service_date": ["2026-05-24", "2026-05-24"],
            "actual_start_time": ["2026-05-24T05:00:00+00:00", None],
        }
    )
    summary = calc_execution(rides, group_by=["operator_ref", "service_date"])
    assert summary.iloc[0]["executed"] == 1
    assert summary.iloc[0]["missing"] == 1


# --------------------------------------------------------------------------- #
# penalties
# --------------------------------------------------------------------------- #
def test_band_table_lookup_boundaries():
    bands = pd.DataFrame(
        {"from_pct": [0.0, 1.0, 3.5], "to_pct": [1.0, 3.5, 100.0], "amount_nis": [0, 63, 173]}
    )
    table = BandTable(name="t", bands=bands)
    assert table.amount_for(0.5) == 0
    assert table.amount_for(1.0) == 63  # inclusive lower bound
    assert table.amount_for(3.49) == 63
    assert table.amount_for(3.5) == 173
    assert table.amount_for(99.0) == 173  # final band upper bound inclusive
    assert table.amount_for(None) == 0


@pytest.mark.skipif(not PENALTIES_WORKBOOK.exists(), reason="penalties workbook not present")
def test_load_penalty_tables_from_workbook():
    tables = load_penalty_tables()
    assert tables.non_execution is not None
    # Known band from נספח כ"ו table 1.
    assert tables.non_execution.amount_for(1.1) == 63
    assert tables.non_execution.amount_for(0.5) == 0
    assert tables.lateness is not None
    assert tables.earliness is not None


@pytest.mark.skipif(not PENALTIES_WORKBOOK.exists(), reason="penalties workbook not present")
def test_calc_penalties_and_rollup():
    summary = pd.DataFrame(
        {
            "operator_ref": [15, 15],
            "operator_name": ["מטרופולין", "מטרופולין"],
            "service_date": ["2026-05-24", "2026-05-25"],
            "planned": [1000, 1000],
            "executed": [950, 990],
            "missing": [50, 10],
            "missing_pct": [5.0, 1.0],
        }
    )
    penalized = calc_penalties(summary)
    assert "penalty_nis" in penalized.columns
    # 5.0% → 173 ₪/missing × 50 = 8650 ; 1.0% → 63 ₪ × 10 = 630
    assert penalized.iloc[0]["penalty_nis"] == 8650
    assert penalized.iloc[1]["penalty_nis"] == 630
    by_op = penalties_by_operator(penalized)
    assert int(by_op.iloc[0]["penalty_nis"]) == 9280


# --------------------------------------------------------------------------- #
# punctuality
# --------------------------------------------------------------------------- #
def test_punctuality_none_when_no_measurable_delay():
    rides = pd.DataFrame(
        {
            "planned_start_time": ["2026-05-24T05:00:00+00:00"] * 3,
            "actual_start_time": ["2026-05-24T05:00:00+00:00"] * 3,
        }
    )
    out = calc_punctuality(rides)
    assert out.iloc[0]["measurable"] == 0
    assert out.iloc[0]["late_pct"] is None


def test_punctuality_counts_late_and_early():
    rides = pd.DataFrame(
        {
            "planned_start_time": ["2026-05-24T05:00:00+00:00"] * 3,
            "actual_start_time": [
                "2026-05-24T05:08:00+00:00",  # +8 min → late
                "2026-05-24T04:57:00+00:00",  # -3 min → early
                "2026-05-24T05:01:00+00:00",  # +1 min → on time
            ],
        }
    )
    out = calc_punctuality(rides, late_threshold_min=5, early_threshold_min=2)
    row = out.iloc[0]
    assert row["measurable"] == 3
    assert row["late"] == 1
    assert row["early"] == 1


def test_add_delay_minutes_missing_columns():
    df = add_delay_minutes(pd.DataFrame({"x": [1]}))
    assert "delay_min" in df.columns


# --------------------------------------------------------------------------- #
# compare
# --------------------------------------------------------------------------- #
def test_compare_periods_deltas():
    a = pd.DataFrame(
        {"operator_name": ["דן"], "planned": [100], "executed": [90], "missing": [10], "missing_pct": [10.0]}
    )
    b = pd.DataFrame(
        {"operator_name": ["דן"], "planned": [100], "executed": [95], "missing": [5], "missing_pct": [5.0]}
    )
    out = compare_periods(a, b, labels=("a", "b"))
    row = out.iloc[0]
    assert row["missing_delta"] == -5
    assert row["missing_pct_delta"] == -5.0


# --------------------------------------------------------------------------- #
# cache
# --------------------------------------------------------------------------- #
def test_cache_roundtrip(tmp_path):
    cache = Cache(cache_dir=tmp_path, enabled=True)
    payload = [{"a": 1, "b": "x"}, {"a": 2, "b": "y"}]
    assert cache.get("/p", {"k": 1}) is None
    cache.set("/p", {"k": 1}, payload)
    got = cache.get("/p", {"k": 1})
    assert got == payload
    # Different params → miss.
    assert cache.get("/p", {"k": 2}) is None


def test_cache_disabled_is_noop(tmp_path):
    cache = Cache(cache_dir=tmp_path, enabled=False)
    cache.set("/p", {}, [{"a": 1}])
    assert cache.get("/p", {}) is None


# --------------------------------------------------------------------------- #
# models
# --------------------------------------------------------------------------- #
def test_operator_from_api():
    op = Operator.from_api({"operator_ref": "5", "agency_name": "דן"}, route_count=3)
    assert op.operator_ref == 5 and op.name == "דן" and op.route_count == 3


def test_route_and_ride_from_api():
    route = Route.from_api({"line_ref": "123", "operator_ref": "5", "route_short_name": "1"})
    assert route.line_ref == 123 and route.route_short_name == "1"
    ride = Ride.from_api(
        {"operator_ref": "5", "line_ref": "123", "planned_start_time": "t", "actual_start_time": "t2"}
    )
    assert ride.executed is True
    assert Ride.from_api({"planned_start_time": "t"}).executed is False


# --------------------------------------------------------------------------- #
# bot
# --------------------------------------------------------------------------- #
def test_parse_question_resolves_operator_and_window():
    known = [(5, "דן"), (15, "מטרופולין")]
    q = parse_question("מה הביצועים של דן בשבוע שעבר?", known_operators=known)
    assert q.operator_ref == 5
    assert q.date_from and q.date_to
    assert q.is_actionable()


def test_parse_question_detects_intent_and_all():
    q = parse_question("כמה קנס לכל המפעילים אתמול?")
    assert q.intent == "penalties"
    assert q.all_operators is True
    assert q.date_from == q.date_to  # yesterday


def test_parse_question_unresolved_operator():
    q = parse_question("מה קרה ב-2026-05-24?")
    assert "operator" in q.unresolved
