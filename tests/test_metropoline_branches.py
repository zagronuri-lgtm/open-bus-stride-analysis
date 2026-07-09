"""Unit tests for Metropoline branch mapping and weekly record enrichment."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest

from src.metropoline_branches import (
    METROPOLINE_OPERATOR_REF,
    UNASSIGNED_BRANCH,
    branch_for_line,
    branch_for_mkt,
    load_branch_map,
)
from src import weekly_report as wr


REF_CSV = (
    Path(__file__).resolve().parent.parent
    / "data"
    / "reference"
    / "metropoline_line_branch_map.csv"
)


def test_branch_map_file_exists_and_loads():
    assert REF_CSV.exists()
    m = load_branch_map(REF_CSV)
    assert len(m) >= 300
    # known row from 02.07.2026 export
    assert branch_for_mkt("11501", m) == "500"
    assert m["11501"].cluster == "שרון חולון מרחבי"


def test_branch_for_unknown_mkt():
    m = load_branch_map(REF_CSV)
    assert branch_for_mkt("999999", m) == UNASSIGNED_BRANCH
    assert branch_for_mkt(None, m) == UNASSIGNED_BRANCH


def test_branch_for_line_only_metropoline():
    m = load_branch_map(REF_CSV)
    line_mkt = {(15, "501"): "11501", (5, "1"): "999"}
    assert branch_for_line(15, "501", line_mkt, m) == "500"
    assert branch_for_line(5, "1", line_mkt, m) == ""


def test_operators_exclude_tnufa():
    assert 34 not in wr.OPERATORS
    assert set(wr.OPERATORS) == {3, 5, 15, 16, 18, 25}
    assert set(wr.P95) == set(wr.OPERATORS)


def test_build_records_attaches_branch():
    m = load_branch_map(REF_CSV)
    day = wr.DayData(dt.date(2026, 6, 22))
    day.planned = {(15, "501"): 10, (5, "1"): 8}
    day.executed = {15: {"501": 7}, 5: {"1": 8}}

    line_mkt = {(15, "501"): "11501", (5, "1"): "999"}
    clusters = {"11501": "שרון חולון מרחבי", "999": "גוש דן"}
    route_len = {"501": 12.0, "1": 5.0}

    recs = wr.build_records([day], line_mkt, clusters, route_len, branch_map=m)
    by_op = {(r.op, r.line): r for r in recs}

    metro = by_op[(15, "501")]
    assert metro.branch == "500"
    assert metro.cluster == "שרון חולון מרחבי"
    assert metro.planned == 10
    assert metro.executed == 7
    assert metro.nonexec == 3

    dan = by_op[(5, "1")]
    assert dan.branch == ""
    assert dan.nonexec == 0


def test_branch_aggregation_sums_to_metropoline():
    """סכום סניפים (כולל לא משויך) == סכום מטרופולין."""
    m = load_branch_map(REF_CSV)
    day = wr.DayData(dt.date(2026, 6, 23))
    day.planned = {
        (15, "501"): 10,   # branch 500
        (15, "9999"): 5,   # unmapped mkt → לא משויך
    }
    day.executed = {15: {"501": 9, "9999": 4}}
    line_mkt = {(15, "501"): "11501", (15, "9999"): "888888"}
    clusters = {"11501": "שרון חולון מרחבי"}
    recs = wr.build_records([day], line_mkt, clusters, {}, branch_map=m)
    metro = [r for r in recs if r.op == METROPOLINE_OPERATOR_REF]
    total_planned = sum(r.planned for r in metro)
    by_branch = {}
    for r in metro:
        by_branch[r.branch] = by_branch.get(r.branch, 0) + r.planned
    assert total_planned == 15
    assert sum(by_branch.values()) == total_planned
    assert by_branch["500"] == 10
    assert by_branch[UNASSIGNED_BRANCH] == 5


def test_branch_500_mapping_has_expected_mkts():
    m = load_branch_map(REF_CSV)
    mkts_500 = sorted(k for k, v in m.items() if v.branch == "500")
    assert len(mkts_500) == 20
    assert "11501" in mkts_500
    assert "11230" in mkts_500  # קו לילה — תורם חסרים יחסיים גבוהים
    assert "11231" in mkts_500
    # קווי תלמידים/שבת שעשויים להיות ללא נפח בימי חול תקפים — עדיין ממופים ל-500
    assert "11909" in mkts_500
    assert "12247" in mkts_500


def test_branch_sensitivity_windows():
    """רגישות: ימים תקפים מול כולל segment."""
    m = load_branch_map(REF_CSV)
    d1 = wr.DayData(dt.date(2026, 6, 28))  # Sunday workday
    d1.valid = True
    d1.planned = {(15, "501"): 100}
    d1.executed = {15: {"501": 99}}

    d2 = wr.DayData(dt.date(2026, 7, 1))  # Wednesday — simulate segment invalid
    d2.valid = False
    d2.classification = "דפוס מיוחד (לוח)"
    d2.planned = {(15, "501"): 100}
    d2.executed = {15: {"501": 90}}

    line_mkt = {(15, "501"): "11501"}
    clusters = {"11501": "שרון חולון מרחבי"}
    recs = wr.build_records([d1, d2], line_mkt, clusters, {}, branch_map=m)
    windows = wr.branch_sensitivity_windows(recs, [d1, d2], branch="500")
    by_label = {w["label"]: w for w in windows}

    valid = by_label["א׳–ג׳ תקפים (כמו סיכום)"]
    assert valid["planned"] == 100
    assert valid["missing"] == 1
    assert abs(valid["pct"] - 0.01) < 1e-9

    sun_thu = by_label["א׳–ה׳ כולל segment"]
    assert sun_thu["planned"] == 200
    assert sun_thu["missing"] == 11
    assert abs(sun_thu["pct"] - 0.055) < 1e-9
