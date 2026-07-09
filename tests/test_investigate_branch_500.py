"""Unit tests for branch 500 investigation helpers (no live API)."""

from __future__ import annotations

import datetime as dt

from src import weekly_report as wr
from src.investigate_branch_500 import summarize_branch
from src.metropoline_branches import load_branch_map


REF_CSV = "data/reference/metropoline_line_branch_map.csv"


def _day(d: dt.date, *, valid: bool, planned: int, executed: int) -> wr.DayData:
    day = wr.DayData(d)
    day.valid = valid
    day.is_workday = d.weekday() in wr.WEEKDAY_WORK
    day.classification = "תקין" if valid else "דפוס מיוחד (לוח)"
    day.planned = {(15, "7728"): planned}
    day.executed = {15: {"7728": executed}}
    return day


def test_summarize_branch_includes_line_day_and_night_focus():
    m = load_branch_map(REF_CSV)
    d1 = _day(dt.date(2026, 6, 28), valid=True, planned=10, executed=2)  # Sunday
    d2 = _day(dt.date(2026, 7, 1), valid=False, planned=10, executed=5)  # Wed segment
    line_mkt = {(15, "7728"): "11230"}
    clusters = {"11230": "שרון"}
    recs = wr.build_records([d1, d2], line_mkt, clusters, {}, branch_map=m)
    summary = summarize_branch(recs, [d1, d2], branch="500")

    assert "by_mkt_day" in summary
    assert len(summary["by_mkt_day"]) == 2
    assert summary["by_mkt_day"][0]["route_mkt"] == "11230"
    assert summary["by_mkt_day"][0]["in_valid_avg"] is True
    assert summary["by_mkt_day"][1]["in_valid_avg"] is False

    night = summary["night_lines_11230_11231"]
    assert len(night) == 2
    assert night[0]["missing"] == 8

    valid = summary["windows"]["valid_weekdays_sun_tue"]
    assert valid["planned"] == 10
    assert valid["missing"] == 8


def test_branch_sensitivity_sheet_name_constant():
    """גיליון הרגישות חייב להיווצר בדוח השבועי."""
    import inspect

    src = inspect.getsource(wr.build_workbook)
    assert "סניף 500 — רגישות" in src
    assert "branch_sensitivity_windows" in src
