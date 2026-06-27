from __future__ import annotations

from datetime import date

import pytest

from src.exclusions_calendar import (
    DEFAULT_EXCLUSIONS_DIR,
    classify_date,
    classify_for_cluster,
    load_calendar,
    load_line_uniqueness_treatments,
)


@pytest.fixture(scope="module")
def entries():
    return load_calendar(DEFAULT_EXCLUSIONS_DIR)


def test_calendar_loads_entries(entries) -> None:
    assert len(entries) > 0


def test_national_holiday_is_dropped(entries) -> None:
    # יום העצמאות — national drop day.
    result = classify_date(date(2026, 4, 22), entries)
    assert result.treatment == "drop"
    assert result.is_excluded is True
    assert "עצמאות" in (result.name or "")


def test_light_fast_is_kept(entries) -> None:
    # ט"ו בשבט — keep (special in name only).
    result = classify_date(date(2026, 2, 2), entries)
    assert result.treatment == "keep"
    assert result.is_normal is True


def test_summer_break_is_segmented(entries) -> None:
    # חופש גדול — long period, must segment (never drop).
    result = classify_date(date(2026, 7, 15), entries)
    assert result.treatment == "segment"
    assert result.is_excluded is False


def test_multi_day_range_matches_interior_date(entries) -> None:
    # חול המועד פסח 2026-04-03..2026-04-07.
    result = classify_date(date(2026, 4, 5), entries)
    assert result.treatment == "segment"


def test_ordinary_day_is_normal(entries) -> None:
    result = classify_date(date(2026, 6, 10), entries)
    assert result.treatment == "normal"
    assert result.is_normal is True


def test_template_placeholder_dates_never_match(entries) -> None:
    # 04_oneoff_events.csv holds 2026-00-00 placeholders; no real date should hit them.
    assert classify_date(date(2026, 1, 1), entries).treatment == "normal"


def test_muslim_calendar_is_branch_scoped_not_national(entries) -> None:
    # 2026-03-01 falls in Ramadan (scope: סניפים מוסלמיים), not nationwide.
    national = classify_date(date(2026, 3, 1), entries, national_only=True)
    branch = classify_date(date(2026, 3, 1), entries, national_only=False)
    assert national.treatment == "normal"
    assert branch.treatment == "segment"


def test_muslim_cluster_segments_on_eid(entries) -> None:
    # 2026-05-27 — עיד אל-אדחא. Negev/Sharon carry Muslim-sector lines.
    negev = classify_for_cluster(date(2026, 5, 27), "הנגב", entries)
    elad = classify_for_cluster(date(2026, 5, 27), "בקעת אונו אלעד", entries)
    assert negev.treatment == "segment"
    assert elad.treatment == "normal"  # Jewish/Haredi cluster, unaffected by Eid


def test_national_drop_wins_over_cluster_sector(entries) -> None:
    # National drop closes everything, even for a Muslim-sector cluster.
    result = classify_for_cluster(date(2026, 4, 22), "הנגב", entries)
    assert result.treatment == "drop"


def test_line_uniqueness_treatments_load() -> None:
    treatments = load_line_uniqueness_treatments()
    assert treatments["תלמידים"]["treatment"] == "segment"
    assert treatments["סדיר - מאושר שבת"]["treatment"] == "keep"
