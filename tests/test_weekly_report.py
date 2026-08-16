# -*- coding: utf-8 -*-
"""
בדיקות רגרסיה ל-src/weekly_report.py.

הבדיקות המרכזיות כאן מקבעות שני באגים שהתגלו בריצת הדוח של 9-15.8.2026
ותוקנו (מיפוי קו→מק"ט משבת בלבד; פסילת ימי segment). הן נכשלות על הקוד
שלפני התיקון ועוברות אחריו.

הערה: בדיקות הקנס-הקבוע ברמת קו-יום (תיקון 2 של הקווארק) אינן כאן —
התיקון הושהה עד אימות חוזי מול נספח כ"ו והבדיקות שלו יושבות בענף
quark-fixes-full יחד עם compute_fixed_penalties().
"""
import datetime as dt

import pytest

from src import weekly_report as W


# --------------------------------------------------------------------------
# עזר
# --------------------------------------------------------------------------
def _day(d, *, valid=True, workday=True):
    day = W.DayData(d)
    day.valid = valid
    day.is_workday = workday
    return day


# --------------------------------------------------------------------------
# באג 1 — מיפוי קו→מק"ט נבנה מיום שבת בלבד
# --------------------------------------------------------------------------
def test_fetch_line_to_mkt_accepts_multiple_dates(monkeypatch):
    """המיפוי חייב לאחד כמה תאריכים, אחרת קווים שאינם נוסעים בשבת נעלמים."""
    sat, wed = dt.date(2026, 8, 15), dt.date(2026, 8, 12)
    by_date = {
        sat.isoformat(): [{"line_ref": 111, "route_mkt": "10111"}],           # קו שבת
        wed.isoformat(): [{"line_ref": 111, "route_mkt": "10111"},
                          {"line_ref": 222, "route_mkt": "10222"}],           # + קו יום-חול
    }

    class _Resp:
        def __init__(self, rows): self._rows = rows
        def raise_for_status(self): pass
        def json(self): return self._rows

    def fake_get(url, params=None, timeout=None):
        if params.get("offset"):
            return _Resp([])
        if params.get("operator_refs") != 3:      # מספיק מפעיל אחד לבדיקה
            return _Resp([])
        return _Resp(by_date[params["date_from"]])

    monkeypatch.setattr(W.SESSION, "get", fake_get)

    only_sat = W.fetch_line_to_mkt(sat)
    assert (3, "222") not in only_sat, "הקו שאינו נוסע בשבת אכן חסר ב-snapshot של שבת"

    both = W.fetch_line_to_mkt([sat, wed])
    assert (3, "111") in both and (3, "222") in both, \
        "האיחוד חייב לכלול גם קווי שבת וגם קווי יום-חול"


def test_fetch_line_to_mkt_backwards_compatible(monkeypatch):
    """קריאה עם תאריך בודד עדיין נתמכת."""
    class _Resp:
        def raise_for_status(self): pass
        def json(self): return []
    monkeypatch.setattr(W.SESSION, "get", lambda *a, **k: _Resp())
    assert W.fetch_line_to_mkt(dt.date(2026, 8, 12)) == {}


# --------------------------------------------------------------------------
# באג 3 — יום segment נפסל ומאפס את יולי-אוגוסט
# --------------------------------------------------------------------------
def test_segment_day_stays_valid():
    """
    "חופש גדול" (segment) מוגדר 1.7-31.8 ארצי. פסילת ימי segment מבטלת
    שישית מהשנה ומאפסת את חשיפת הקנסות בכל יולי-אוגוסט.
    """
    entries = W.load_calendar()
    d = dt.date(2026, 8, 12)                       # רביעי בתוך חופש גדול
    assert W.classify_date(d, entries, national_only=True).treatment == "segment", \
        "התאריך אמור להיות מסווג segment בלוח ההחרגות"

    days = [_day(dt.date(2026, 8, 9) + dt.timedelta(days=i)) for i in range(5)]
    for day in days:
        day.planned_raw = {(15, "L1"): 100}
    W.classify_days(days, entries)

    target = next(x for x in days if x.date == d)
    assert target.valid is True, "יום segment חייב להישאר תקף"
    assert target.percentages is True
    assert target.segment, "יש לתייג את שם תקופת ה-segment"


def test_drop_day_is_still_excluded():
    """רק drop מוחרג — הבדיקה מוודאת שלא שברנו את ההתנהגות הנכונה."""
    entries = W.load_calendar()
    d = dt.date(2026, 5, 22)                       # שבועות — drop
    if W.classify_date(d, entries, national_only=True).treatment != "drop":
        pytest.skip("הלוח אינו מסמן את התאריך כ-drop")
    days = [_day(d)]
    days[0].planned_raw = {(15, "L1"): 100}
    W.classify_days(days, entries)
    assert days[0].valid is False


# --------------------------------------------------------------------------
# מדרגות הפיצוי — אימות מול definitions.md §4
# --------------------------------------------------------------------------
@pytest.mark.parametrize("rate,expected", [
    (0.000, 0), (0.010, 0),
    (0.0101, 63), (0.015, 63),
    (0.0151, 93), (0.025, 93),
    (0.0251, 118), (0.030, 118),
    (0.0301, 143), (0.035, 143),
    (0.0351, 173), (0.500, 173),
])
def test_penalty_tariff_brackets(rate, expected):
    assert W.penalty_tariff(rate) == expected


def test_reconcile_caps_executed_at_planned():
    """בוצע לקו לעולם אינו עולה על המתוכנן."""
    agg = W.reconcile_day({(15, "L1"): 10}, {15: {"L1": 999}})
    assert agg[15]["executed"] == 10
