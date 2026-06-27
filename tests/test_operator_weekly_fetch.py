from __future__ import annotations

import pandas as pd

from src.operator_weekly_fetch import annotate_exclusions


def _frame():
    return pd.DataFrame(
        [
            {"operator_ref": 15, "service_date": "2026-04-22", "planned": 100, "executed": 10},  # יום העצמאות -> drop
            {"operator_ref": 15, "service_date": "2026-07-15", "planned": 90, "executed": 80},   # חופש גדול -> segment
            {"operator_ref": 15, "service_date": "2026-06-10", "planned": 95, "executed": 92},    # normal
        ]
    )


def test_annotate_marks_drop_segment_normal():
    out = annotate_exclusions(_frame())
    by_date = dict(zip(out["service_date"], out["exclusion_treatment"]))
    assert by_date["2026-04-22"] == "drop"
    assert by_date["2026-07-15"] == "segment"
    assert by_date["2026-06-10"] == "normal"
    # raw fetch keeps every row; nothing is deleted.
    assert len(out) == 3


def test_annotate_names_the_holiday():
    out = annotate_exclusions(_frame())
    name = out.loc[out["service_date"] == "2026-04-22", "exclusion_name"].iloc[0]
    assert "עצמאות" in name


def test_ignore_flag_marks_all_normal():
    out = annotate_exclusions(_frame(), ignore=True)
    assert set(out["exclusion_treatment"]) == {"normal"}


def test_empty_frame_gets_columns():
    out = annotate_exclusions(pd.DataFrame())
    assert "exclusion_treatment" in out.columns
    assert "exclusion_name" in out.columns
    assert out.empty
