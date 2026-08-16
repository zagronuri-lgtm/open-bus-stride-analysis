# -*- coding: utf-8 -*-
"""
בדיקות רגרסיה ל-src/weekly_report.py.

הבדיקות המרכזיות כאן מקבעות שני באגים שהתגלו בריצת הדוח של 9-15.8.2026
ותוקנו (מיפוי קו→מק"ט משבת בלבד; פסילת ימי segment). הן נכשלות על הקוד
שלפני התיקון ועוברות אחריו.

בנוסף: בדיקות מנוע-הקנסות פר-משטר-מכרז (TENDER_REGIMES) — מדרגות + קנס
קו-יום באונו-אלעד, תקופת-יום בשרון (חסם תחתון), קנס-לנסיעה במכרזים הישנים
(24/2015, 07/2014) ללא כלל 4.5%, ניתוב קו-בתדירות-נמוכה לסעיף המחמיר,
ואשכול לא-ממופה שאינו מתומחר בשקט. הנתיב האשכולי הישן (fixed_penalty)
נשאר מת לצמיתות.
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


def _rec(d, cluster, line, planned, executed, op=15):
    """LineDayRecord מינימלי לבדיקות מנוע-הקנסות."""
    return W.LineDayRecord(date=d, dow="ד", op=op, op_name="מטרופולין",
                           line=line, mkt="", cluster=cluster,
                           planned=planned, executed=executed,
                           nonexec=planned - executed)


def _t(d, h, m):
    """זמן-יציאה מקומי בודד (naive — כפי ש-fetch_executed מאחסן)."""
    return dt.datetime(d.year, d.month, d.day, h, m)


def _times(d, start_h, start_m, n, step_min):
    """n זמני-יציאה מקומיים במרווח אחיד של step_min דקות."""
    t0 = _t(d, start_h, start_m)
    return [t0 + dt.timedelta(minutes=step_min * i) for i in range(n)]


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
# הנתיב האשכולי הישן של הקנס הקבוע נשאר מת — גם לצד מנוע-המשטרים החדש
# (נספח כ"ו: הקנס הקבוע מוגדר ברמת קו-יום, לא אשכול-יום; הוא ממומש כעת
#  פר-משטר-מכרז ב-TENDER_REGIMES, ואסור שהנתיב האשכולי הישן יקום לתחייה.)
# --------------------------------------------------------------------------
def test_cluster_level_fixed_penalty_is_neutralized():
    """אשכול-יום מעל 4.5% מסומן over_threshold אך אינו מייצר קנס אשכולי קבוע —
    לא באשכול לא-ממופה ולא באשכול ממופה שבו המשטר החדש כן מתמחר רכיבים."""
    d = dt.date(2026, 8, 12)
    day = _day(d)
    day.sched_times[(15, "L1")] = _times(d, 6, 0, 100, 7)   # קו רגיל (מרווח 7 דק')
    recs = [
        _rec(d, "אשכול א", "L0", 100, 50),               # לא-ממופה
        _rec(d, "בקעת אונו אלעד", "L1", 100, 50),        # ממופה, מעל 4.5%
    ]
    rows = W.compute_penalties([day], recs)
    assert len(rows) == 2, "צפויה שורת קנס לכל אשכול-יום"
    assert all(p.over_threshold is True for p in rows), "חציית 4.5% עדיין מסומנת להדגשה"
    assert all(p.fixed_penalty == 0 for p in rows), \
        "הנתיב האשכולי של הקנס הקבוע חייב להישאר מנוטרל לצמיתות"
    mapped = next(p for p in rows if p.cluster == "בקעת אונו אלעד")
    assert mapped.fixed_total > 0, "המשטר החדש מתמחר ברמת קו-יום — לא דרך הנתיב הישן"


# --------------------------------------------------------------------------
# מנוע-קנסות פר-משטר-מכרז — TENDER_REGIMES
# --------------------------------------------------------------------------
def test_is_low_frequency_formula():
    """
    ההגדרה החוזית: "100% מנסיעות הקו ... הן בתדירות של 59 דקות ומעלה" ⇒
    המרווח-העוקב ה*מינימלי* בין כל זוג נסיעות סמוכות ≥ 59 דק'.
    מרווח ממוצע אינו שקול — ר' מקרה הצבירים למטה.
    """
    d = dt.date(2026, 8, 12)
    assert W.is_low_frequency(1, None) is True             # נסיעה מתוכננת אחת
    # n=2 — הפער היחיד מכריע
    assert W.is_low_frequency(2, [_t(d, 8, 0), _t(d, 8, 59)]) is True    # 59 = הסף
    assert W.is_low_frequency(2, [_t(d, 8, 0), _t(d, 8, 58)]) is False   # 58 < הסף
    # מקרה הצבירים: 06:00/06:15/06:30/18:00 — ממוצע 240 דק' אך מרווחים
    # עוקבים של 15 דק' ⇒ קו רגיל. נוסחת-הממוצע הישנה סיווגה אותו תדירות-נמוכה.
    assert W.is_low_frequency(
        4, [_t(d, 6, 0), _t(d, 6, 15), _t(d, 6, 30), _t(d, 18, 0)]) is False
    # כל המרווחים העוקבים ≥ 59 ⇒ תדירות נמוכה
    assert W.is_low_frequency(
        4, [_t(d, 6, 0), _t(d, 7, 0), _t(d, 9, 0), _t(d, 18, 0)]) is True
    assert W.is_low_frequency(3, _times(d, 8, 0, 3, 60)) is True
    assert W.is_low_frequency(3, _times(d, 8, 0, 3, 55)) is False
    # זמנים חלקיים: נצפו פחות מהמתוכנן — המרווח האמיתי יכול רק להיות קטן
    # יותר, סיווג תדירות-נמוכה אינו בטוח ⇒ None (מדווח, מטופל כקו רגיל)
    assert W.is_low_frequency(5, [_t(d, 6, 0), _t(d, 9, 0), _t(d, 12, 0)]) is None
    assert W.is_low_frequency(5, None) is None            # לא ניתן לקבוע — לא מנוחש
    assert W.is_low_frequency(5, []) is None
    assert W.is_low_frequency(0, None) is None


def test_is_low_frequency_midnight_crossing_undeterminable():
    """
    חציית-חצות: הזמנים נאספים מחלון UTC של יום קלנדרי, ולכן יציאות בחלון
    00:00-03:59 מקומי עלולות להשתייך ליום-השירות הקודם — המרווחים מזוהמים.
    אין כלל-שיוך ליום-שירות ⇒ None (שמרני: לעולם לא מסווג תדירות-נמוכה).
    """
    d = dt.date(2026, 8, 12)
    assert W.is_low_frequency(2, [_t(d, 0, 30), _t(d, 6, 0)]) is None
    assert W.is_low_frequency(3, [_t(d, 1, 0), _t(d, 5, 0), _t(d, 9, 0)]) is None
    assert W.is_low_frequency(2, [_t(d, 3, 59), _t(d, 9, 0)]) is None
    # מ-04:00 ואילך — מחוץ לחלון הלילה, הסיווג רגיל
    assert W.is_low_frequency(2, [_t(d, 4, 0), _t(d, 6, 0)]) is True   # מרווח 120


def test_ono_elad_regime_graded_plus_line_day():
    """אונו-אלעד 5/2021: מדרג אשכולי (173 ₪ מעל 3.5%) + 5,000 ₪ לקו-יום מעל 4.5%."""
    d = dt.date(2026, 8, 12)
    day = _day(d)
    day.sched_times[(15, "L1")] = _times(d, 6, 0, 100, 7)   # מרווח עוקב 7 דק'
    day.sched_times[(15, "L2")] = _times(d, 6, 0, 100, 7)
    recs = [_rec(d, "בקעת אונו אלעד", "L1", 100, 90),    # 10% בקו — מעל 4.5%
            _rec(d, "בקעת אונו אלעד", "L2", 100, 100)]   # תקין
    rows = W.compute_penalties([day], recs)
    assert len(rows) == 1
    p = rows[0]
    assert "5/2021" in p.regime
    assert p.rate == pytest.approx(0.05)                  # 10/200 אשכולי
    assert p.tariff == 173 and p.exposure == 10 * 173
    assert p.fixed_total == 5000, "קו-יום אחד מעל 4.5% — 5,000 ₪ (§2.2)"
    assert p.total == 10 * 173 + 5000
    assert any("2.2" in label for label, _ in p.components)
    assert p.fixed_penalty == 0


def test_ono_elad_graded_bracket_low_rate():
    """מדרגה 1.0-1.5% = 63 ₪ להפרה, ובלי קנס קו-יום מתחת ל-4.5%."""
    d = dt.date(2026, 8, 12)
    day = _day(d)
    day.sched_times[(15, "L1")] = _times(d, 5, 0, 1000, 1)   # מרווח עוקב 1 דק'
    recs = [_rec(d, "בקעת אונו אלעד", "L1", 1000, 988)]   # 1.2%
    p = W.compute_penalties([day], recs)[0]
    assert p.tariff == 63 and p.exposure == 12 * 63
    assert p.fixed_total == 0 and p.total == 12 * 63


def test_low_frequency_routes_to_strict_clause():
    """קו בתדירות נמוכה אינו פטור: מנותב ל-5,000 ₪ לכל נסיעה שלא בוצעה
    (§2.3 אונו / §2.2 שרון), ומוחרג מהקנס הקבוע של קו רגיל."""
    d = dt.date(2026, 8, 12)
    day = _day(d)
    day.sched_times[(15, "L2")] = _times(d, 6, 0, 5, 120)  # 5 נסיעות, מרווח 120 דק'
    recs = [_rec(d, "בקעת אונו אלעד", "L1", 1, 0),        # נסיעה מתוכננת אחת = תדירות נמוכה
            _rec(d, "בקעת אונו אלעד", "L2", 5, 3)]        # מרווח 120 דק' = תדירות נמוכה
    p = W.compute_penalties([day], recs)[0]
    # 3 נסיעות לא-בוצעו בקווי תדירות-נמוכה × 5,000
    assert p.fixed_total == 3 * 5000
    assert any("לא תדירה" in label for label, _ in p.components)
    assert not any("2.2 אי-ביצוע יומי" in label for label, _ in p.components), \
        "קו בתדירות נמוכה מוחרג מקנס הקו הרגיל"
    # המדרג האשכולי חל על כלל הנסיעות, כולל קווי תדירות נמוכה
    assert p.tariff == 173 and p.exposure == 3 * 173      # 3/6 = 50%


def test_sharon_regime_day_period_lower_bound():
    """שרון 04/2021: ביצוע < 95.5% ⇒ 500 ₪ לתקופת-יום (חסם תחתון של תקופה
    אחת), לא 5,000; ונסיעה-אחרונה מסומנת 'לא נמצא במקור' ולא מתומחרת."""
    d = dt.date(2026, 8, 12)
    day = _day(d)
    day.sched_times[(15, "L1")] = _times(d, 6, 0, 100, 7)
    recs = [_rec(d, "שרון", "L1", 100, 90)]               # 10% > 4.5%
    p = W.compute_penalties([day], recs)[0]
    assert "04/2021" in p.regime
    assert p.tariff == 173 and p.exposure == 10 * 173
    assert p.fixed_total == 500, "תקופת-יום אחת × 500 ₪ — חסם תחתון"
    assert any("95.5%" in label for label, _ in p.components)
    assert "2.3" in p.not_found and "לא נלכד" in p.not_found
    assert p.total == 10 * 173 + 500


@pytest.mark.parametrize("cluster,tender", [
    ("שרון חולון מרחבי", "24/2015"),
    ("הנגב", "07/2014"),
])
def test_old_tenders_flat_per_ride_no_45_rule(cluster, tender):
    """מכרזים ישנים: 2,000 ₪ לכל נסיעה שלא בוצעה — בלי כלל 4.5%, בלי מדרג
    ובלי ניתוב תדירות-נמוכה; מקדם ההכפלה לא נמצא ⇒ מסומן, לא מנוחש."""
    d = dt.date(2026, 8, 12)
    recs = [_rec(d, cluster, "L1", 100, 50),              # 50% — אין תוספת 5,000/500
            _rec(d, cluster, "L2", 1, 0)]                 # נסיעה בודדת — אין ניתוב 5,000
    p = W.compute_penalties([_day(d)], recs)[0]
    assert tender in p.regime
    assert p.tariff is None and p.exposure is None, "אין מדרג אשכולי במכרזים הישנים"
    assert p.fixed_total == 51 * 2000
    assert p.total == 51 * 2000
    assert p.over_threshold is True                       # הסימון נשאר (הדגשה בלבד)
    assert len(p.components) == 1 and "2,000" in p.components[0][0]
    assert "מקדם" in p.not_found


def test_old_tenders_ceiling_not_lower_bound():
    """המשטרים הישנים: הטריגר החוזי הוא אירוע מתועד — התמחור פר-נסיעת-SIRI
    הוא תקרת-חשיפה תיאורטית; מסגור 'חסם תחתון' אסור עליהם."""
    d = dt.date(2026, 8, 12)
    for cluster in ("שרון חולון מרחבי", "הנגב"):
        p = W.compute_penalties([_day(d)], [_rec(d, cluster, "L1", 100, 50)])[0]
        assert "תקרת-חשיפה תיאורטית" in p.not_found
        assert "אירוע מתועד" in p.not_found
        assert "חסם תחתון" not in p.not_found.replace("לא חסם תחתון", "")
    for regime in (W.TENDER_REGIMES["שרון חולון מרחבי"], W.TENDER_REGIMES["הנגב"]):
        assert "תקרת-חשיפה" in regime["trigger_note"]


def test_negev_tzafon_is_not_a_metropoline_regime():
    """'צפון הנגב' הוא אשכול של דן בדרום (130 שורות ב-ClusterToLine.zip),
    לא של מטרופולין — אסור שיהיה ממופה למשטר, והשורה מסומנת לא-ממופה."""
    assert "צפון הנגב" not in W.TENDER_REGIMES
    assert "הנגב" in W.TENDER_REGIMES                      # אשכול מטרופולין נשאר
    d = dt.date(2026, 8, 12)
    p = W.compute_penalties([_day(d)], [_rec(d, "צפון הנגב", "L1", 100, 50, op=5)])[0]
    assert p.regime == W.UNMAPPED_REGIME_LABEL
    assert p.tariff is None and p.total is None


def test_operator_guard_only_metropoline_priced():
    """שומר-מפעיל: משטרי TENDER_REGIMES מתמחרים רק שורות מטרופולין
    (operator_ref 15); שורת מפעיל אחר באותו שם-אשכול מסומנת כמו לא-ממופה."""
    d = dt.date(2026, 8, 12)
    day = _day(d)
    day.sched_times[(15, "L1")] = _times(d, 6, 0, 100, 7)
    recs = [_rec(d, "שרון", "L1", 100, 90),               # מטרופולין — מתומחר
            _rec(d, "שרון", "L9", 100, 90, op=5)]         # דן — לא מתומחר
    rows = W.compute_penalties([day], recs)
    assert len(rows) == 2
    metro = next(p for p in rows if p.op == 15)
    other = next(p for p in rows if p.op == 5)
    assert "04/2021" in metro.regime and metro.total is not None
    assert other.regime == W.UNMAPPED_REGIME_LABEL
    assert other.tariff is None and other.exposure is None
    assert other.fixed_total is None and other.total is None
    assert "מטרופולין" in other.not_found and "לא תומחרה" in other.not_found
    # הסיכום אינו מערבב את שתי השורות תחת שם-האשכול המשותף
    summary = W.summarize_penalties_by_cluster(rows)
    sharon_rows = [s for s in summary if s["cluster"] == "שרון"]
    assert len(sharon_rows) == 2
    assert {s["priced"] for s in sharon_rows} == {True, False}


def test_unmapped_cluster_not_priced_silently():
    """אשכול ללא משטר: משטר='לא-ממופה', הסכומים None (לא 0 בשקט) ומסומן."""
    d = dt.date(2026, 8, 12)
    recs = [_rec(d, "אשכול א", "L1", 100, 50)]
    p = W.compute_penalties([_day(d)], recs)[0]
    assert p.regime == W.UNMAPPED_REGIME_LABEL
    assert p.tariff is None and p.exposure is None
    assert p.fixed_total is None and p.total is None
    assert "לא תומחרה" in p.not_found
    assert p.over_threshold is True


def test_unknown_frequency_reported_not_guessed():
    """קו ≥2 נסיעות בלי זמני-יציאה: מטופל כקו רגיל והדבר מדווח בהערות."""
    d = dt.date(2026, 8, 12)
    recs = [_rec(d, "בקעת אונו אלעד", "L1", 10, 0)]       # אין sched_times
    p = W.compute_penalties([_day(d)], recs)[0]
    assert p.fixed_total == 5000, "טופל כקו רגיל מעל 4.5%"
    assert "זמני-יציאה" in p.not_found


def test_partial_times_treated_as_regular_and_reported():
    """זמנים חלקיים (נצפו < מתוכנן) במרווחים גדולים: לא מסווג תדירות-נמוכה —
    מטופל כקו רגיל (קנס 5,000 של קו-מעל-הסף, לא 5,000×נסיעה) ומדווח."""
    d = dt.date(2026, 8, 12)
    day = _day(d)
    day.sched_times[(15, "L1")] = _times(d, 6, 0, 3, 120)  # 3 זמנים בלבד מול 10 מתוכננות
    recs = [_rec(d, "בקעת אונו אלעד", "L1", 10, 3)]
    p = W.compute_penalties([day], recs)[0]
    assert p.fixed_total == 5000, "קו רגיל מעל 4.5% — לא 7×5,000 של תדירות-נמוכה"
    assert "לא ניתנת לקביעה" in p.not_found


def test_summarize_by_cluster_flags_unmapped():
    """סיכום פר-אשכול: ממופים עם סכומים, לא-ממופים priced=False."""
    d = dt.date(2026, 8, 12)
    day = _day(d)
    day.sched_times[(15, "L1")] = _times(d, 6, 0, 100, 7)
    recs = [_rec(d, "בקעת אונו אלעד", "L1", 100, 90),
            _rec(d, "אשכול א", "L9", 100, 90)]
    rows = W.compute_penalties([day], recs)
    summary = {s["cluster"]: s for s in W.summarize_penalties_by_cluster(rows)}
    ono = summary["בקעת אונו אלעד"]
    assert ono["priced"] is True and ono["total"] == 10 * 173 + 5000
    other = summary["אשכול א"]
    assert other["priced"] is False and other["total"] == 0
    assert other["nonexec"] == 10 and other["days_over"] == 1


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


# --------------------------------------------------------------------------
# load_clusters — סינון ToDate
# --------------------------------------------------------------------------
def _cluster_zip_bytes(rows):
    """zip סינתטי בפורמט ClusterToLine.txt (CSV עם BOM-פחות, כמו המקור)."""
    import io as _io, zipfile as _zipfile, csv as _csv
    buf = _io.StringIO()
    writer = _csv.DictWriter(buf, fieldnames=[
        "OperatorName", "OfficeLineId", "OperatorLineId", "ClusterName",
        "FromDate", "ToDate"])
    writer.writeheader()
    for r in rows:
        writer.writerow(r)
    zbuf = _io.BytesIO()
    with _zipfile.ZipFile(zbuf, "w") as z:
        z.writestr("ClusterToLine.txt", buf.getvalue())
    return zbuf.getvalue()


def test_load_clusters_drops_expired_records(monkeypatch):
    """רשומה עם ToDate שחלף נזרקת — ואינה תופסת ב-setdefault את מקומה של
    הרשומה הפעילה; ToDate בלתי-פריק אינו גורם לזריקה שקטה."""
    rows = [
        {"OperatorName": "מטרופולין", "OfficeLineId": "10111",
         "OperatorLineId": "1", "ClusterName": "אשכול ישן",
         "FromDate": "01/01/2012", "ToDate": "25/06/2026"},   # פג תוקף
        {"OperatorName": "מטרופולין", "OfficeLineId": "10111",
         "OperatorLineId": "1", "ClusterName": "אשכול פעיל",
         "FromDate": "26/06/2026", "ToDate": "01/01/2200"},
        {"OperatorName": "מטרופולין", "OfficeLineId": "10222",
         "OperatorLineId": "2", "ClusterName": "אשכול בלי תוקף",
         "FromDate": "", "ToDate": ""},                        # לא-פריק — נשמר
    ]
    monkeypatch.setattr(W, "_zip_bytes", lambda *a, **k: _cluster_zip_bytes(rows))
    out = W.load_clusters(today=dt.date(2026, 8, 16))
    assert out["10111"] == "אשכול פעיל", "רשומה שפג תוקפה אינה תופסת את המקום"
    assert out["10222"] == "אשכול בלי תוקף"


# --------------------------------------------------------------------------
# build_workbook — בדיקת-עשן ללא רשת
# --------------------------------------------------------------------------
def test_build_workbook_smoke(tmp_path):
    """בנייה מנתונים סינתטיים אל tmp_path ופתיחה-מחדש ב-openpyxl.
    measure_inaccuracy עוקף עם dict מוכן — אין קריאות רשת."""
    openpyxl = pytest.importorskip("openpyxl")
    sun, sat = dt.date(2026, 8, 9), dt.date(2026, 8, 15)
    d = dt.date(2026, 8, 12)                               # רביעי
    days = [W.DayData(sun + dt.timedelta(days=i)) for i in range(7)]
    target = next(x for x in days if x.date == d)
    target.sched_times[(15, "L1")] = _times(d, 6, 0, 100, 7)

    def full_rec(cluster, line, planned, executed, op, op_name, kmr):
        return W.LineDayRecord(
            date=d, dow="ד", op=op, op_name=op_name, line=line, mkt="10111",
            cluster=cluster, planned=planned, executed=executed,
            nonexec=planned - executed, km_per_ride=kmr,
            planned_km=planned * kmr, executed_km=executed * kmr,
            nonexec_km=(planned - executed) * kmr)

    recs = [full_rec("בקעת אונו אלעד", "L1", 100, 90, 15, "מטרופולין", 12.0),
            full_rec("אשכול א", "L9", 50, 45, 3, "אגד", 8.5)]
    op_agg = W.aggregate_operator(days, recs)
    penalties = W.compute_penalties(days, recs)
    assert len(penalties) == 2, "שורת קנס ממופה + שורה לא-ממופה"
    inaccuracy = {"status": "אי-דיוק לא מדיד",
                  "detail": "בדיקת-עשן — dict מוכן, ללא רשת",
                  "sample": "", "n": 0, "diffs": 0}
    path = tmp_path / "smoke.xlsx"
    W.build_workbook((sun, sat), days, recs, op_agg, penalties, inaccuracy,
                     str(path))

    wb = openpyxl.load_workbook(str(path))
    expected = {"סיכום מנהלים", "יומי לפי מפעיל", "לפי אשכול", "חשיפת קנסות",
                "השוואת P95", "אי-דיוק", "ימים חריגים", "גלם — קו×יום",
                "ק\"מ — קו×יום", "מתודולוגיה"}
    assert expected <= set(wb.sheetnames)
    ws = wb["חשיפת קנסות"]
    # מיון (תאריך, מפעיל, אשכול): שורה 4 = אגד (לא-ממופה), שורה 5 = מטרופולין
    assert ws.cell(4, 12).value == "לא-ממופה"
    assert ws.cell(5, 12).value == "=J5+K5", "סה\"כ (L) = נוסחה =J+K"
    assert isinstance(ws.cell(5, 11).value, (int, float)), "K נכתב כערך"
