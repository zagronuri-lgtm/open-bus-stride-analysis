"""
דוח אי-ביצוע שבועי לתחבורה ציבורית בישראל — מרובה מפעילים.
מודד את רכיב אי-היציאה (2.1.1) בלבד = חסם תחתון לשיעור אי-הביצוע הרשמי
(ראה docs/definitions.md; תואם ללוגיקת executed≤planned ב-stride_analysis.analysis.execution).
הרצה:
  python -m src.weekly_report --week-ending 2026-06-27 --output-dir outputs
אם לא מצוין week-ending: השבוע שהסתיים אתמול (ראשון..שבת).
"""
from __future__ import annotations
import argparse, datetime as dt, math, statistics, io, zipfile, csv, collections
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import requests

from src.exclusions_calendar import (
    DateClassification,
    classify_date,
    load_calendar,
)
from src.metropoline_branches import (
    METROPOLINE_OPERATOR_REF,
    UNASSIGNED_BRANCH,
    branch_for_mkt,
    load_branch_map,
)

API = "https://open-bus-stride-api.hasadna.org.il"
GTFS_ZIP = "https://gtfs.mot.gov.il/gtfsfiles/israel-public-transportation.zip"
CLUSTER_ZIP = "https://gtfs.mot.gov.il/gtfsfiles/ClusterToLine.zip"

OPERATORS = {  # operator_ref -> שם (ששת מפעילי היעד)
    3: "אגד", 5: "דן", 15: "מטרופולין", 16: "סופרבוס",
    18: "קווים", 25: "אלקטרה אפיקים",
}

# ספים (definitions.md / נספח כ"ו)
THRESH_SERVICE = 0.021      # מעל 2.1% = חריגה מרמת שירות
THRESH_FUNDAMENTAL = 0.025  # מעל 2.5% = הפרה יסודית
THRESH_PRECISION = 0.045    # אי-דיוק מעל 4.5% = חריגה

# מדרגות פיצוי לכל הפרה (₪) לפי שיעור אי-ביצוע יומי באשכול
def penalty_tariff(rate: float) -> int:
    if rate <= 0.01:  return 0
    if rate <= 0.015: return 63
    if rate <= 0.025: return 93
    if rate <= 0.03:  return 118
    if rate <= 0.035: return 143
    return 173

# בסיס P95 ליום חול (חושב 10.6.2026): (נסיעות, ק"מ)
P95 = {
    3:  (22475, 628021), 5:  (12112, 194884), 15: (12818, 328707),
    16: (11388, 219985), 18: (13778, 289334), 25: (8061, 155322),
}

# קנס קבוע לאשכול-יום שאי-הביצוע בו חורג מ-4.5% (סעיף נספח כ"ו)
CLUSTER_DAY_FIXED_PENALTY = 5000
CLUSTER_DAY_FIXED_THRESHOLD = THRESH_PRECISION  # 4.5%

# ימי חול בישראל (Python weekday(): Mon=0 .. Sun=6) — ראשון עד חמישי
WEEKDAY_WORK = frozenset({6, 0, 1, 2, 3})   # א, ב, ג, ד, ה
WEEKDAY_FRIDAY = 4                            # ו
WEEKDAY_SATURDAY = 5                          # ש
HEB_DOW = {6: "א", 0: "ב", 1: "ג", 2: "ד", 3: "ה", 4: "ו", 5: "ש"}

# ספי זיהוי ימים חריגים
REDUCED_PLAN_RATIO = 0.75    # מתוכנן < 75% מהחציון => יום מופחת
STRIKE_EXEC_RATIO = 0.90     # ביצוע < 90% מהרגיל => חריג ביצוע (שביתה/אירוע)
DOUBLE_PLAN_RATIO = 1.7      # מתוכנן >= פי 1.7 מהחציון => כפל תכנון GTFS
SIRI_FAULT_RATE = 0.50       # ביצוע/מתוכנן נמוך מ-50% מהרגיל אצל רוב המפעילים => חשד תקלת SIRI

SESSION = requests.Session()
SESSION.trust_env = False  # התעלם מ-HTTP(S)_PROXY של הסביבה (סנדבוקס Cursor)
SESSION.headers.update({"Accept": "application/json"})

# שרת ה-GTFS של משרד התחבורה מחזיר דף HTML אם נשלח Accept: application/json —
# הורדות ה-zip חייבות לבטל את הכותרת הזו.
ZIP_HEADERS = {"Accept": "*/*"}


# ---------- עזר: תאריכים וחלון UTC ----------
def week_ending_saturday(today: dt.date) -> tuple[dt.date, dt.date]:
    """מחזיר (ראשון, שבת) של השבוע שהסתיים אתמול. today=ראשון -> ראשון שעבר..שבת אמש."""
    # אתמול
    yest = today - dt.timedelta(days=1)
    # מצא את השבת האחרונה <= אתמול (פייתון: Sunday=6)
    # נרצה טווח ראשון..שבת. אם today ראשון, אתמול שבת.
    sat = yest
    while sat.weekday() != 5:  # 5 = Saturday
        sat -= dt.timedelta(days=1)
    sun = sat - dt.timedelta(days=6)
    return sun, sat

def is_idt(d: dt.date) -> bool:
    """קיץ ישראל (DST) — אפריל עד אוקטובר בקירוב. 6/2026 = קיץ (UTC+3)."""
    return 3 < d.month < 11 or (d.month == 3) or (d.month == 10 and d.day < 25)

def utc_window(d: dt.date) -> tuple[str, str]:
    """חלון UTC ליום מקומי. קיץ UTC+3: 21:00Z יום קודם .. 20:59:59Z. חורף UTC+2: 22:00Z."""
    off = 3 if is_idt(d) else 2
    start = dt.datetime(d.year, d.month, d.day) - dt.timedelta(hours=off)
    end = start + dt.timedelta(days=1) - dt.timedelta(seconds=1)
    fmt = "%Y-%m-%dT%H:%M:%SZ"
    return start.strftime(fmt), end.strftime(fmt)


# ---------- 1. מתוכנן לכל קו-יום ----------
def fetch_planned(d: dt.date) -> dict[tuple[int, str], int]:
    """GET /gtfs_rides_agg/group_by — קריאה אחת ליום. מפתח (operator_ref, line_ref) -> total_planned_rides."""
    ds = d.isoformat()
    r = SESSION.get(f"{API}/gtfs_rides_agg/group_by", params={
        "date_from": ds, "date_to": ds, "group_by": "operator_ref,line_ref",
    }, timeout=120)
    r.raise_for_status()
    out = {}
    for row in r.json():
        op = row.get("operator_ref"); line = str(row.get("line_ref"))
        if op in OPERATORS:
            out[(op, line)] = out.get((op, line), 0) + int(row.get("total_planned_rides") or 0)
    return out


# ---------- 2. בוצע (SIRI) לכל מפעיל-יום + 3. דה-דופ ----------
def fetch_executed(op: int, d: dt.date) -> dict[str, int]:
    """משיכת siri_rides במנות, דה-דופ לפי journey_ref. מחזיר line_ref -> נסיעות שבוצעו."""
    t_from, t_to = utc_window(d)
    seen_nonzero = set()        # journey_ref שלא מסתיים ב-0 — נספר פעם אחת
    counts = collections.Counter()  # line_ref -> count
    offset, limit = 0, 10000
    while True:
        r = SESSION.get(f"{API}/siri_rides/list", params={
            "siri_route__operator_refs": op,
            "scheduled_start_time_from": t_from,
            "scheduled_start_time_to": t_to,
            "limit": limit, "offset": offset,
        }, timeout=180)
        r.raise_for_status()
        rows = r.json()
        if not rows:
            break
        for row in rows:
            jr = str(row.get("journey_ref") or "")
            line = str(row.get("siri_route__line_ref"))
            if jr.endswith("-0"):
                counts[line] += 1                 # כל שורה נספרת
            else:
                if jr in seen_nonzero:
                    continue
                seen_nonzero.add(jr)
                counts[line] += 1                 # פעם אחת בלבד
        if len(rows) < limit:
            break
        offset += limit
    return dict(counts)


# ---------- 4. הצלבה ברמת קו-יום ----------
def reconcile_day(planned: dict, executed_by_op: dict) -> dict:
    """
    לכל (op, line): בוצע = min(SIRI, מתוכנן). אי-ביצוע = מתוכנן - בוצע.
    מחזיר אגרגציה לפי מפעיל: {op: {'planned':..,'executed':..}}
    """
    agg = {op: {"planned": 0, "executed": 0} for op in OPERATORS}
    for (op, line), p in planned.items():
        ex = executed_by_op.get(op, {}).get(line, 0)
        done = min(ex, p)
        agg[op]["planned"] += p
        agg[op]["executed"] += done
    return agg


# ---------- 6. ק"מ מ-GTFS ----------
def haversine(a, b) -> float:
    R = 6371.0088
    lat1, lon1, lat2, lon2 = map(math.radians, [a[0], a[1], b[0], b[1]])
    dlat, dlon = lat2 - lat1, lon2 - lon1
    h = math.sin(dlat/2)**2 + math.cos(lat1)*math.cos(lat2)*math.sin(dlon/2)**2
    return 2 * R * math.asin(math.sqrt(h))

def _open_zip_from_url_or_local(url: str, local_names: list[str], timeout: int = 600) -> zipfile.ZipFile:
    """מוריד zip מהרשת; אם נכשל — טוען מקובץ מקומי ב-data/reference/."""
    ref = Path(__file__).resolve().parent.parent / "data" / "reference"
    try:
        content = SESSION.get(url, timeout=timeout, headers=ZIP_HEADERS).content
        return zipfile.ZipFile(io.BytesIO(content))
    except Exception as exc:
        for name in local_names:
            local = ref / name
            if local.exists():
                print(f"[!] הורדה נכשלה ({exc.__class__.__name__}); משתמש ב-{local.name}")
                return zipfile.ZipFile(local)
        raise


def load_gtfs_lengths() -> dict[str, float]:
    """
    מוריד את ה-GTFS הארצי, מחשב אורך כל route_id (ק"מ) לפי ה-shape השכיח בטריפים שלו.
    הצלבה: route_id == line_ref. מחזיר line_ref(str) -> אורך_ק"מ.
    """
    z = _open_zip_from_url_or_local(
        GTFS_ZIP, ["israel-public-transportation.zip"], timeout=600
    )

    def read(name):
        # קבצי ה-GTFS של משרד התחבורה כוללים BOM — utf-8-sig מסיר אותו,
        # אחרת שם העמודה הראשונה מקבל תחילית '﻿' (KeyError על shape_id/route_id).
        with z.open(name) as f:
            return list(csv.DictReader(io.TextIOWrapper(f, "utf-8-sig")))

    # shapes: shape_id -> [(lat,lon)] ממוין לפי shape_pt_sequence
    shape_pts = collections.defaultdict(list)
    for row in read("shapes.txt"):
        shape_pts[row["shape_id"]].append(
            (int(row["shape_pt_sequence"]), float(row["shape_pt_lat"]), float(row["shape_pt_lon"]))
        )
    shape_len = {}
    for sid, pts in shape_pts.items():
        pts.sort()
        total = sum(haversine((pts[i][1], pts[i][2]), (pts[i+1][1], pts[i+1][2]))
                    for i in range(len(pts)-1))
        shape_len[sid] = total

    # trips: route_id -> Counter(shape_id) כדי לבחור shape שכיח
    route_shapes = collections.defaultdict(collections.Counter)
    for row in read("trips.txt"):
        if row.get("shape_id"):
            route_shapes[row["route_id"]][row["shape_id"]] += 1

    route_len = {}
    for rid, ctr in route_shapes.items():
        common_shape = ctr.most_common(1)[0][0]
        route_len[rid] = shape_len.get(common_shape, 0.0)
    return route_len  # ק"מ לנסיעה אחת בקו


# ---------- 5. זיהוי: קו -> אשכול ו-קו -> route_mkt ----------
def _strip_zeros(code: str) -> str:
    """מסיר אפסים מובילים לצורך השוואת OfficeLineId == route_mkt."""
    s = str(code or "").strip().lstrip("0")
    return s or "0"


def load_clusters() -> dict[str, str]:
    """
    מוריד את ClusterToLine.zip ומחזיר מיפוי route_mkt(ללא אפסים מובילים) -> שם אשכול.
    OfficeLineId הוא ה-route_mkt. רשומות פעילות בלבד (ToDate בעתיד).
    אם ההורדה נכשלת — נופל ל-data/reference/ClusterToLine.zip.
    """
    z = _open_zip_from_url_or_local(CLUSTER_ZIP, ["ClusterToLine.zip"], timeout=300)
    name = z.namelist()[0]
    out: dict[str, str] = {}
    with z.open(name) as f:
        for row in csv.DictReader(io.TextIOWrapper(f, "utf-8-sig")):
            mkt = _strip_zeros(row.get("OfficeLineId"))
            cluster = (row.get("ClusterName") or "").strip()
            if mkt and cluster:
                out.setdefault(mkt, cluster)   # ההתאמה הראשונה (קלאסטר ראשי) מנצחת
    return out


def fetch_line_to_mkt(
    d: dt.date,
    date_to: dt.date | None = None,
) -> dict[tuple[int, str], str]:
    """מיפוי (operator_ref, line_ref) -> route_mkt דרך /gtfs_routes/list.

    חשוב: לא להסתמך על שבת בלבד — בשבת חסרים קווי חול, ואז route_mkt/אשכול/סניף
    נשארים ריקים. ברירת מחדל: יום בודד; מומלץ להעביר טווח ראשון..שבת.
    """
    end = date_to or d
    out: dict[tuple[int, str], str] = {}
    for op in OPERATORS:
        offset, limit = 0, 5000
        while True:
            r = SESSION.get(f"{API}/gtfs_routes/list", params={
                "date_from": d.isoformat(), "date_to": end.isoformat(),
                "operator_refs": op, "limit": limit, "offset": offset,
            }, timeout=120)
            r.raise_for_status()
            rows = r.json()
            if not rows:
                break
            for row in rows:
                key = (op, str(row.get("line_ref")))
                mkt = _strip_zeros(row.get("route_mkt"))
                if mkt:
                    out.setdefault(key, mkt)
            if len(rows) < limit:
                break
            offset += limit
    return out


def cluster_for(op: int, line: str, line_mkt: dict, clusters: dict) -> str:
    """אשכול של קו, או 'לא משויך' אם אין התאמה."""
    mkt = line_mkt.get((op, line))
    if not mkt:
        return "לא משויך"
    return clusters.get(mkt, "לא משויך")


# ---------- פרופיל שעתי לאימות תקלת SIRI ----------
def fetch_hourly_profile(op: int, d: dt.date) -> dict[int, int]:
    """מספר נסיעות SIRI לפי שעת scheduled_start_time מקומית. לזיהוי 'נפילת מצוק' לאפס."""
    t_from, t_to = utc_window(d)
    off = 3 if is_idt(d) else 2
    hours = collections.Counter()
    offset, limit = 0, 10000
    while True:
        r = SESSION.get(f"{API}/siri_rides/list", params={
            "siri_route__operator_refs": op,
            "scheduled_start_time_from": t_from, "scheduled_start_time_to": t_to,
            "limit": limit, "offset": offset,
        }, timeout=180)
        r.raise_for_status()
        rows = r.json()
        if not rows:
            break
        for row in rows:
            ts = row.get("scheduled_start_time")
            if not ts:
                continue
            # ISO עם offset; נמיר לשעה מקומית
            t = dt.datetime.fromisoformat(ts.replace("Z", "+00:00"))
            local = t + dt.timedelta(hours=off) if t.tzinfo is None else \
                    (t.astimezone(dt.timezone.utc) + dt.timedelta(hours=off))
            hours[local.hour] += 1
        if len(rows) < limit:
            break
        offset += limit
    return dict(hours)


def detect_cliff(profile: dict[int, int], active_from: int = 5, active_to: int = 22) -> int | None:
    """
    מזהה 'נפילת מצוק' לאפס: שעה שממנה ואילך (בתוך טווח פעילות) אין כמעט נסיעות,
    בעוד שלפניה היו. מחזיר את שעת הנפילה, או None אם אין.
    """
    active = [h for h in range(active_from, active_to + 1)]
    if sum(profile.get(h, 0) for h in active) == 0:
        return active_from  # אין נסיעות כלל בטווח הפעיל
    for h in active:
        before = sum(profile.get(x, 0) for x in range(active_from, h))
        after = sum(profile.get(x, 0) for x in range(h, active_to + 1))
        if before >= 50 and after == 0:
            return h
    return None


# ---------- מבני נתונים ----------
class DayData:
    """נתוני יום בודד: מתוכנן/בוצע ברמת (op,line), סיווג, ודגלים."""
    def __init__(self, date_: dt.date):
        self.date = date_
        self.dow = date_.weekday()
        self.is_workday = self.dow in WEEKDAY_WORK
        self.planned_raw: dict[tuple[int, str], int] = {}   # לפני קאפ כפל-תכנון
        self.planned: dict[tuple[int, str], int] = {}       # אחרי קאפ
        self.executed: dict[int, dict[str, int]] = {}       # op -> {line: בוצע}
        self.calendar: DateClassification | None = None     # סיווג מלוח החגים
        self.classification = "תקין"                         # תקין/מופחת/חריג-ביצוע/לא-תקף/החרגה
        self.reason = ""
        self.strike_ops: set[int] = set()                   # מפעילים עם חריג ביצוע נקודתי
        self.valid = True                                   # נכלל בממוצעים ובחשיפה
        self.percentages = True                             # האם להציג אחוזים בכלל

    def exec_for(self, op: int, line: str) -> int:
        return self.executed.get(op, {}).get(line, 0)


def fetch_day(d: dt.date, line_mkt: dict) -> DayData:
    """מושך מתוכנן + בוצע (כל המפעילים במקביל) ליום אחד."""
    day = DayData(d)
    day.planned_raw = fetch_planned(d)
    with ThreadPoolExecutor(max_workers=8) as ex:
        futs = {op: ex.submit(fetch_executed, op, d) for op in OPERATORS}
        day.executed = {op: f.result() for op, f in futs.items()}
    return day


# ---------- 5. זיהוי ימים חריגים ----------
def _op_planned_total(day: DayData, source: str = "planned_raw") -> dict[int, int]:
    """סך מתוכנן לכל מפעיל ביום."""
    src = getattr(day, source)
    tot = collections.Counter()
    for (op, _line), p in src.items():
        tot[op] += p
    return dict(tot)


def _op_executed_total(day: DayData) -> dict[int, int]:
    tot = {}
    for op in OPERATORS:
        tot[op] = sum(day.executed.get(op, {}).values())
    return tot


def apply_double_planning_cap(workdays: list[DayData]) -> int:
    """
    כפל תכנון GTFS: לכל קו, אם מתוכנן ביום >= פי 1.7 מחציון ימי החול -> הצב חציון.
    מיושם אחיד על כל ימי החול. מעדכן day.planned (מאותחל מ-planned_raw).
    מחזיר מספר התאמות שבוצעו.
    """
    # אסוף לכל (op,line) את רשימת המתוכנן על פני ימי החול
    per_line: dict[tuple[int, str], list[int]] = collections.defaultdict(list)
    for day in workdays:
        for key, p in day.planned_raw.items():
            per_line[key].append(p)
    medians = {key: statistics.median(vals) for key, vals in per_line.items() if vals}

    fixes = 0
    for day in workdays:
        day.planned = dict(day.planned_raw)
        for key, p in day.planned_raw.items():
            med = medians.get(key, 0)
            if med > 0 and p >= DOUBLE_PLAN_RATIO * med:
                day.planned[key] = int(round(med))
                fixes += 1
    # ימים שאינם ימי-חול: planned = planned_raw ללא קאפ (דפוס שונה ממילא)
    return fixes


def classify_days(days: list[DayData], calendar_entries) -> None:
    """
    מסווג כל יום: לוח חגים -> מופחת -> חריג-ביצוע (פר-מפעיל) -> תקלת SIRI (חוצת-מפעילים).
    מעדכן day.classification/reason/valid/percentages/strike_ops.
    """
    workdays = [d for d in days if d.is_workday]

    # קאפ כפל-תכנון (גם לימים שאינם חול — planned=planned_raw)
    apply_double_planning_cap(workdays)
    for d in days:
        if not d.planned:
            d.planned = dict(d.planned_raw)

    # חציון מתוכנן/בוצע פר-מפעיל על ימי החול (בסיס להשוואות)
    plan_by_op: dict[int, list[int]] = collections.defaultdict(list)
    exec_by_op: dict[int, list[int]] = collections.defaultdict(list)
    for d in workdays:
        pt, et = _op_planned_total(d, "planned"), _op_executed_total(d)
        for op in OPERATORS:
            plan_by_op[op].append(pt.get(op, 0))
            exec_by_op[op].append(et.get(op, 0))
    plan_med = {op: statistics.median(v) if v else 0 for op, v in plan_by_op.items()}
    exec_med = {op: statistics.median(v) if v else 0 for op, v in exec_by_op.items()}

    n_ops = len(OPERATORS)
    majority = n_ops // 2 + 1

    for d in days:
        # 1) לוח חגים ארצי
        d.calendar = classify_date(d.date, calendar_entries, national_only=True)
        if d.calendar.treatment == "drop":
            d.classification = "החרגה (לוח)"
            d.reason = f"לוח החרגות: {d.calendar.name}"
            d.valid = False
            continue
        if d.calendar.treatment == "segment":
            d.classification = "דפוס מיוחד (לוח)"
            d.reason = f"לוח החרגות: {d.calendar.name} — דפוס שונה, מוצג בנפרד"
            d.valid = False

        if not d.is_workday:
            # שישי/שבת: דפוס בסיס שונה — מוצג בנפרד, לא בממוצעי יום-חול
            if d.classification == "תקין":
                d.classification = "שישי/שבת (דפוס נפרד)"
                d.reason = f"יום {HEB_DOW[d.dow]} — דפוס בסיס שונה"
            d.valid = False
            continue

    # רק על ימי חול שעדיין 'תקין' נריץ זיהוי מופחת/שביתה/SIRI
    candidate = [d for d in workdays if d.classification == "תקין"]

    # 2) יום מופחת מתוכנן (חג/ערב חג/צום שלא תויג בלוח) — מול תקלת נתוני-תכנון.
    #    יום מופחת אמיתי: גם המתוכנן וגם הביצוע נמוכים. פער נתוני-תכנון: המתוכנן
    #    כמעט אפס אך הביצוע תקין (gtfs_rides_agg לא אוכלס לאותו תאריך) — לא אי-ביצוע.
    for d in candidate:
        pt, et = _op_planned_total(d, "planned"), _op_executed_total(d)
        reduced = sum(1 for op in OPERATORS
                      if plan_med.get(op, 0) > 0 and pt.get(op, 0) < REDUCED_PLAN_RATIO * plan_med[op])
        if reduced < majority:
            continue
        exec_normal = sum(1 for op in OPERATORS
                          if exec_med.get(op, 0) > 0 and et.get(op, 0) >= STRIKE_EXEC_RATIO * exec_med[op])
        if exec_normal >= majority:
            d.classification = "לא תקף (תכנון חסר)"
            d.reason = (f"מתוכנן < 75% מהחציון אצל {reduced}/{n_ops} מפעילים אך הביצוע תקין "
                        f"({exec_normal}/{n_ops}) — פער נתוני-תכנון ב-gtfs_rides_agg, לא אי-ביצוע")
            d.valid = False
            d.percentages = False
        else:
            d.classification = "מופחת (תכנון)"
            d.reason = f"מתוכנן ובוצע < מהחציון אצל רוב המפעילים ({reduced}/{n_ops}) — יום שירות מופחת"
            d.valid = False

    # 3) תקלת SIRI חוצת-מפעילים: ביצוע נמוך חריג אצל רוב המפעילים בו-זמנית
    for d in [x for x in candidate if x.classification == "תקין"]:
        et = _op_executed_total(d)
        low = 0
        for op in OPERATORS:
            if exec_med.get(op, 0) > 0 and et.get(op, 0) < SIRI_FAULT_RATE * exec_med[op]:
                low += 1
        if low >= majority:
            # אימות: פרופיל שעתי ל-3 מפעילים גדולים — נפילת מצוק לאפס אצל כולם
            sample_ops = sorted(OPERATORS, key=lambda o: -plan_med.get(o, 0))[:3]
            cliffs = {op: detect_cliff(fetch_hourly_profile(op, d.date)) for op in sample_ops}
            if all(c is not None for c in cliffs.values()):
                d.classification = "לא תקף (תקלת SIRI)"
                hrs = ", ".join(f"{HEB_DOW[d.dow]}:{OPERATORS[o]}≈{cliffs[o]}:00" for o in sample_ops)
                d.reason = (f"ביצוע נמוך אצל {low}/{n_ops} מפעילים + נפילת מצוק שעתית "
                            f"({hrs}) — פער נתונים, לא אי-ביצוע")
                d.valid = False
                d.percentages = False
                continue

    # 4) חריג ביצוע נקודתי (שביתה/אירוע פר-מפעיל): תכנון רגיל, ביצוע < 90% מהרגיל
    for d in workdays:
        if not d.valid:
            continue
        pt, et = _op_planned_total(d, "planned"), _op_executed_total(d)
        for op in OPERATORS:
            plan_ok = plan_med.get(op, 0) == 0 or pt.get(op, 0) >= REDUCED_PLAN_RATIO * plan_med[op]
            if plan_ok and exec_med.get(op, 0) > 0 and et.get(op, 0) < STRIKE_EXEC_RATIO * exec_med[op]:
                d.strike_ops.add(op)
        if d.strike_ops:
            names = ", ".join(OPERATORS[o] for o in sorted(d.strike_ops))
            note = f"חריג ביצוע (שביתה/אירוע): {names} — מוחרגים מהממוצע"
            d.reason = (d.reason + " | " + note) if d.reason else note


# ---------- 6+7. הצלבה ברמת (op, line, cluster, day) + אגרגציות ----------
class LineDayRecord:
    __slots__ = ("date", "dow", "op", "op_name", "line", "mkt", "cluster",
                 "branch", "planned", "executed", "nonexec", "km_per_ride",
                 "planned_km", "executed_km", "nonexec_km")

    def __init__(self, **kw):
        for k in self.__slots__:
            setattr(self, k, kw.get(k))


def build_records(days: list[DayData], line_mkt: dict, clusters: dict,
                  route_len: dict, branch_map: dict | None = None) -> list[LineDayRecord]:
    """רשומת (op,line,cluster[,branch],day) עם בוצע=min(SIRI,מתוכנן), אי-ביצוע, וק"מ.

    ``branch`` ממולא למטרופולין בלבד מתוך מיפוי הסניפים; לשאר המפעילים — מחרוזת ריקה.
    """
    branch_map = branch_map or {}
    recs: list[LineDayRecord] = []
    for d in days:
        for (op, line), p in d.planned.items():
            if op not in OPERATORS:
                continue
            ex = min(d.exec_for(op, line), p)
            kmr = route_len.get(line, 0.0)
            mkt = line_mkt.get((op, line), "")
            branch = ""
            if op == METROPOLINE_OPERATOR_REF:
                branch = branch_for_mkt(mkt, branch_map)
            recs.append(LineDayRecord(
                date=d.date, dow=HEB_DOW[d.dow], op=op, op_name=OPERATORS[op],
                line=line, mkt=mkt,
                cluster=cluster_for(op, line, line_mkt, clusters),
                branch=branch,
                planned=p, executed=ex, nonexec=p - ex, km_per_ride=round(kmr, 3),
                planned_km=p * kmr, executed_km=ex * kmr, nonexec_km=(p - ex) * kmr,
            ))
    return recs


def branch_sensitivity_windows(
    recs: list[LineDayRecord],
    days: list[DayData],
    branch: str = "500",
) -> list[dict]:
    """רגישות % אי-ביצוע לסניף מטרופולין לפי חלונות זמן.

    מחזיר רשימת dict עם keys: label, planned, executed, missing, pct, note.
    משמש לגיליון «סניף 500 — רגישות» ולכיול מול BI (~2–3%).
    """
    valid_dates = {d.date for d in days if d.valid and d.is_workday}
    strike = {(d.date, op) for d in days for op in d.strike_ops}
    branch_recs = [
        r for r in recs
        if r.op == METROPOLINE_OPERATOR_REF and (r.branch or "") == branch
    ]
    windows = [
        (
            "א׳–ג׳ תקפים (כמו סיכום)",
            [r for r in branch_recs if r.date in valid_dates and (r.date, r.op) not in strike],
            "ברירת מחדל בדוח — אחרי classify_days / חופש גדול",
        ),
        (
            "א׳–ה׳ כולל segment",
            [r for r in branch_recs if r.date.weekday() in WEEKDAY_WORK],
            "כולל ד׳–ה׳ שסווגו חופש גדול",
        ),
        (
            "כל השבוע",
            list(branch_recs),
            "כולל ו׳–ש׳",
        ),
    ]
    out = []
    for label, subset, note in windows:
        planned = sum(r.planned for r in subset)
        executed = sum(r.executed for r in subset)
        missing = planned - executed
        out.append({
            "label": label,
            "planned": planned,
            "executed": executed,
            "missing": missing,
            "pct": (missing / planned) if planned else 0.0,
            "note": note,
        })
    return out


def aggregate_operator(days: list[DayData], recs: list[LineDayRecord]) -> dict:
    """
    אגרגציה פר-מפעיל על ימי חול תקפים בלבד, תוך החרגת מפעילים בחריג-ביצוע נקודתי.
    מחזיר op -> {planned, executed, nonexec, planned_km, executed_km, nonexec_km, valid_days}
    """
    valid_dates = {d.date for d in days if d.valid and d.is_workday}
    strike = {(d.date, op) for d in days for op in d.strike_ops}
    out = {op: collections.Counter() for op in OPERATORS}
    vdays = {op: set() for op in OPERATORS}
    for r in recs:
        if r.date not in valid_dates or (r.date, r.op) in strike:
            continue
        c = out[r.op]
        c["planned"] += r.planned; c["executed"] += r.executed; c["nonexec"] += r.nonexec
        c["planned_km"] += r.planned_km; c["executed_km"] += r.executed_km
        c["nonexec_km"] += r.nonexec_km
        vdays[r.op].add(r.date)
    return {op: {**dict(out[op]), "valid_days": len(vdays[op])} for op in OPERATORS}


# ---------- 2. חשיפת קנסות (נספח כ"ו) ----------
class PenaltyRow:
    __slots__ = ("date", "dow", "op", "op_name", "cluster",
                 "planned", "executed", "nonexec", "rate", "tariff",
                 "exposure", "over_threshold", "fixed_penalty")

    def __init__(self, **kw):
        for k in self.__slots__:
            setattr(self, k, kw.get(k))


def compute_penalties(days: list[DayData], recs: list[LineDayRecord]) -> list[PenaltyRow]:
    """
    חשיפה לכל מפעיל×אשכול×יום-חול-תקף: תעריף לפי שיעור אי-ביצוע יומי באשכול,
    חשיפה = אי-בוצע × תעריף; + 5,000 ₪ קבוע אם השיעור > 4.5%.
    מחריג ימים לא-תקפים ומפעילים בחריג-ביצוע נקודתי.
    """
    valid_dates = {d.date for d in days if d.valid and d.is_workday}
    strike = {(d.date, op) for d in days for op in d.strike_ops}
    bucket: dict[tuple, collections.Counter] = collections.defaultdict(collections.Counter)
    dow = {d.date: HEB_DOW[d.dow] for d in days}
    for r in recs:
        if r.date not in valid_dates or (r.date, r.op) in strike:
            continue
        b = bucket[(r.date, r.op, r.cluster)]
        b["planned"] += r.planned; b["executed"] += r.executed; b["nonexec"] += r.nonexec

    rows: list[PenaltyRow] = []
    for (date_, op, cluster), b in sorted(bucket.items(), key=lambda kv: (kv[0][0], kv[0][1], kv[0][2])):
        planned = b["planned"]
        if planned == 0:
            continue
        rate = b["nonexec"] / planned
        tariff = penalty_tariff(rate)
        over = rate > CLUSTER_DAY_FIXED_THRESHOLD
        rows.append(PenaltyRow(
            date=date_, dow=dow.get(date_, ""), op=op, op_name=OPERATORS[op], cluster=cluster,
            planned=planned, executed=b["executed"], nonexec=b["nonexec"],
            rate=rate, tariff=tariff, exposure=b["nonexec"] * tariff,
            over_threshold=over, fixed_penalty=CLUSTER_DAY_FIXED_PENALTY if over else 0,
        ))
    return rows


# ---------- 4. אי-דיוק — דגימת קו גדול ----------
def measure_inaccuracy(days: list[DayData], recs: list[LineDayRecord]) -> dict:
    """
    דוגם את הקו הגדול ביותר (לפי מתוכנן) ביום חול תקף, מושך rides_execution/list,
    ובודק אם actual_start_time שונה מ-planned_start_time. אם זהה בכל השורות -> לא מדיד.
    """
    valid_dates = {d.date for d in days if d.valid and d.is_workday}
    cand = [r for r in recs if r.date in valid_dates and r.planned > 0]
    if not cand:
        return {"status": "אין נתונים", "detail": "לא נמצאו ימי חול תקפים לדגימה"}
    big = max(cand, key=lambda r: r.planned)
    sample = f"{OPERATORS[big.op]} קו {big.line} ({big.date.isoformat()})"

    try:
        r = SESSION.get(f"{API}/rides_execution/list", params={
            "date_from": big.date.isoformat(), "date_to": big.date.isoformat(),
            "operator_ref": str(big.op), "line_ref": str(big.line),
            "order_by": "planned_start_time asc", "limit": 5000,
        }, timeout=120)
        r.raise_for_status()
        rows = r.json()
    except Exception as exc:  # noqa: BLE001 — דגימת אי-דיוק לא תפיל את הדוח
        return {
            "status": "לא נבדק",
            "detail": f"rides_execution נכשל עבור {sample}: {type(exc).__name__}: {exc}",
            "sample": sample, "n": 0, "diffs": 0,
        }

    n = len(rows)
    diffs = sum(1 for x in rows
                if x.get("actual_start_time") and x.get("planned_start_time")
                and x["actual_start_time"] != x["planned_start_time"])
    sample = f"{sample}, {n} נסיעות"
    if n == 0:
        return {"status": "אין נתונים", "detail": f"אין רשומות rides_execution ל-{sample}",
                "sample": sample, "n": n, "diffs": 0}
    if diffs == 0:
        return {"status": "אי-דיוק לא מדיד",
                "detail": ("actual_start_time == planned_start_time בכל השורות "
                           "(שדה מנוון, מצב 6/2026) — לא ניתן למדוד איחור/הקדמה"),
                "sample": sample, "n": n, "diffs": 0}
    return {"status": "מדיד חלקית",
            "detail": f"{diffs}/{n} נסיעות עם זמן יציאה בפועל שונה מהמתוכנן",
            "sample": sample, "n": n, "diffs": diffs}


# ============================================================================
#                       5. ייצור xlsx (openpyxl)
# ============================================================================
def build_workbook(week: tuple[dt.date, dt.date], days: list[DayData],
                   recs: list[LineDayRecord], op_agg: dict,
                   penalties: list[PenaltyRow], inaccuracy: dict, path: str) -> None:
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.chart import BarChart, Reference
    from openpyxl.utils import get_column_letter

    sun, sat = week
    ARIAL = "Arial"
    HDR_FILL = PatternFill("solid", fgColor="1F4E78")
    HDR_FONT = Font(name=ARIAL, bold=True, color="FFFFFF", size=11)
    TITLE_FONT = Font(name=ARIAL, bold=True, size=14, color="1F4E78")
    BASE_FONT = Font(name=ARIAL, size=10)
    WARN_FILL = PatternFill("solid", fgColor="FCE4D6")
    BAD_FILL = PatternFill("solid", fgColor="F8CBAD")
    OK_FILL = PatternFill("solid", fgColor="E2EFDA")
    thin = Side(style="thin", color="BFBFBF")
    BORDER = Border(left=thin, right=thin, top=thin, bottom=thin)
    CENTER = Alignment(horizontal="center", vertical="center")
    PCT = "0.0%"; NUM = "#,##0"; KM = "#,##0"; SHK = '#,##0" ₪"'

    wb = Workbook()
    wb.remove(wb.active)

    def new_sheet(title: str) -> "object":
        ws = wb.create_sheet(title)
        ws.sheet_view.rightToLeft = True   # RTL לעברית
        return ws

    def header_row(ws, headers, row=1):
        for j, h in enumerate(headers, start=1):
            c = ws.cell(row=row, column=j, value=h)
            c.font = HDR_FONT; c.fill = HDR_FILL; c.alignment = CENTER; c.border = BORDER
        ws.freeze_panes = ws.cell(row=row + 1, column=1)

    def style_body(ws, first_data_row, ncols):
        for row in ws.iter_rows(min_row=first_data_row, max_col=ncols):
            for c in row:
                if c.font is None or c.font.name != ARIAL:
                    c.font = BASE_FONT
                c.border = BORDER

    def autosize(ws, widths):
        for j, w in enumerate(widths, start=1):
            ws.column_dimensions[get_column_letter(j)].width = w

    valid_workdays = [d for d in days if d.valid and d.is_workday]
    week_label = f"{sun.isoformat()} – {sat.isoformat()}"

    # ---- גיליון 1: סיכום מנהלים ----
    ws = new_sheet("סיכום מנהלים")
    ws.merge_cells("A1:H1")
    t = ws.cell(1, 1, f"דוח אי-ביצוע שבועי — אי-יציאה (2.1.1) | שבוע {week_label}")
    t.font = TITLE_FONT; t.alignment = CENTER
    ws.merge_cells("A2:H2")
    ws.cell(2, 1, "המדד מודד אי-יציאה בלבד = חסם תחתון לשיעור אי-הביצוע הרשמי (נספח כ\"ו)").font = \
        Font(name=ARIAL, italic=True, size=9, color="808080")
    headers = ["מפעיל", "מתוכנן (נסיעות)", "בוצע (נסיעות)", "% ביצוע", "% אי-ביצוע",
               "אי-ביצוע ק\"מ", "% אי-ביצוע ק\"מ", "ימי-חול תקפים"]
    header_row(ws, headers, row=4)
    r0 = 5
    for i, op in enumerate(OPERATORS):
        a = op_agg[op]; rr = r0 + i
        ws.cell(rr, 1, OPERATORS[op])
        ws.cell(rr, 2, a.get("planned", 0)).number_format = NUM
        ws.cell(rr, 3, a.get("executed", 0)).number_format = NUM
        # נוסחאות חיות
        ws.cell(rr, 4, f"=IF(B{rr}=0,0,C{rr}/B{rr})").number_format = PCT
        ws.cell(rr, 5, f"=IF(B{rr}=0,0,1-C{rr}/B{rr})").number_format = PCT
        ws.cell(rr, 6, round(a.get("nonexec_km", 0))).number_format = KM
        pkm = a.get("planned_km", 0)
        ws.cell(rr, 7, f"=IF({pkm}=0,0,F{rr}/{pkm})").number_format = PCT
        ws.cell(rr, 8, a.get("valid_days", 0)).alignment = CENTER
        # צביעת חריגה מסף שירות
        cell = ws.cell(rr, 5)
        nonexec = a.get("nonexec", 0); planned = a.get("planned", 0)
        rate = (nonexec / planned) if planned else 0
        cell.fill = BAD_FILL if rate > THRESH_FUNDAMENTAL else (WARN_FILL if rate > THRESH_SERVICE else OK_FILL)
    last = r0 + len(OPERATORS) - 1
    tot = last + 1
    ws.cell(tot, 1, "סך הכל").font = Font(name=ARIAL, bold=True)
    ws.cell(tot, 2, f"=SUM(B{r0}:B{last})").number_format = NUM
    ws.cell(tot, 3, f"=SUM(C{r0}:C{last})").number_format = NUM
    ws.cell(tot, 4, f"=IF(B{tot}=0,0,C{tot}/B{tot})").number_format = PCT
    ws.cell(tot, 5, f"=IF(B{tot}=0,0,1-C{tot}/B{tot})").number_format = PCT
    ws.cell(tot, 6, f"=SUM(F{r0}:F{last})").number_format = KM
    for j in range(1, 9):
        ws.cell(tot, j).fill = HDR_FILL; ws.cell(tot, j).font = Font(name=ARIAL, bold=True, color="FFFFFF")
    style_body(ws, 5, 8); autosize(ws, [16, 16, 16, 10, 12, 14, 14, 14])
    # גרף % אי-ביצוע פר-מפעיל
    chart = BarChart(); chart.type = "col"; chart.title = "% אי-ביצוע לפי מפעיל"
    chart.y_axis.numFmt = "0.0%"; chart.height = 8; chart.width = 18
    data = Reference(ws, min_col=5, min_row=4, max_row=last)
    cats = Reference(ws, min_col=1, min_row=r0, max_row=last)
    chart.add_data(data, titles_from_data=True); chart.set_categories(cats)
    ws.add_chart(chart, f"J4")

    # ---- גיליון 2: אי-ביצוע יומי לפי מפעיל ----
    ws = new_sheet("יומי לפי מפעיל")
    ws.merge_cells("A1:I1"); ws.cell(1, 1, "אי-ביצוע יומי לפי מפעיל (% — נסיעות)").font = TITLE_FONT
    day_cols = [d for d in days]
    headers = ["מפעיל"] + [f"{HEB_DOW[d.dow]} {d.date.strftime('%d/%m')}" for d in day_cols]
    header_row(ws, headers, row=3)
    # מתוכנן/בוצע פר (op, יום)
    pe: dict[tuple[int, dt.date], list[int]] = collections.defaultdict(lambda: [0, 0])
    for r in recs:
        pe[(r.op, r.date)][0] += r.planned; pe[(r.op, r.date)][1] += r.executed
    for i, op in enumerate(OPERATORS):
        rr = 4 + i; ws.cell(rr, 1, OPERATORS[op]).font = Font(name=ARIAL, bold=True)
        for j, d in enumerate(day_cols, start=2):
            p, e = pe[(op, d.date)]
            cell = ws.cell(rr, j)
            if not d.percentages:
                cell.value = "—"; cell.alignment = CENTER
            elif p == 0:
                cell.value = "—"; cell.alignment = CENTER
            else:
                rate = 1 - e / p
                cell.value = rate; cell.number_format = PCT
                if not d.valid or op in d.strike_ops:
                    cell.fill = WARN_FILL  # יום/מפעיל מוחרג — מוצג אך מסומן
                elif rate > THRESH_FUNDAMENTAL:
                    cell.fill = BAD_FILL
    style_body(ws, 4, len(headers)); autosize(ws, [16] + [11] * len(day_cols))
    note_r = 4 + len(OPERATORS) + 1
    ws.cell(note_r, 1, "כתום = יום/מפעיל מוחרג מהממוצע;  אדום = מעל 2.5% (הפרה יסודית);  — = יום לא-תקף/ללא תכנון").font = \
        Font(name=ARIAL, italic=True, size=9, color="808080")

    # ---- גיליון 3: אי-ביצוע לפי אשכול ----
    ws = new_sheet("לפי אשכול")
    ws.merge_cells("A1:F1"); ws.cell(1, 1, "אי-ביצוע לפי אשכול (ימי-חול תקפים)").font = TITLE_FONT
    header_row(ws, ["אשכול", "מפעיל ראשי", "מתוכנן", "בוצע", "% ביצוע", "% אי-ביצוע"], row=3)
    cl: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    cl_op: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    valid_dates = {d.date for d in valid_workdays}
    strike = {(d.date, op) for d in days for op in d.strike_ops}
    for r in recs:
        if r.date not in valid_dates or (r.date, r.op) in strike:
            continue
        cl[r.cluster]["planned"] += r.planned; cl[r.cluster]["executed"] += r.executed
        cl_op[r.cluster][r.op_name] += r.planned
    rr = 4
    for cluster in sorted(cl, key=lambda c: -cl[c]["planned"]):
        b = cl[cluster]; main_op = cl_op[cluster].most_common(1)[0][0] if cl_op[cluster] else ""
        ws.cell(rr, 1, cluster); ws.cell(rr, 2, main_op)
        ws.cell(rr, 3, b["planned"]).number_format = NUM
        ws.cell(rr, 4, b["executed"]).number_format = NUM
        ws.cell(rr, 5, f"=IF(C{rr}=0,0,D{rr}/C{rr})").number_format = PCT
        ws.cell(rr, 6, f"=IF(C{rr}=0,0,1-D{rr}/C{rr})").number_format = PCT
        rate = (1 - b["executed"] / b["planned"]) if b["planned"] else 0
        ws.cell(rr, 6).fill = BAD_FILL if rate > THRESH_FUNDAMENTAL else (WARN_FILL if rate > THRESH_SERVICE else OK_FILL)
        rr += 1
    style_body(ws, 4, 6); autosize(ws, [22, 14, 14, 14, 10, 12])

    # ---- גיליונות מטרופולין לפי סניף ----
    metro_recs = [
        r for r in recs
        if r.op == METROPOLINE_OPERATOR_REF
        and r.date in valid_dates
        and (r.date, r.op) not in strike
    ]

    # סיכום לפי סניף
    ws = new_sheet("מטרופולין לפי סניף")
    ws.merge_cells("A1:H1")
    ws.cell(1, 1, "מטרופולין — אי-ביצוע לפי סניף (ימי-חול תקפים)").font = TITLE_FONT
    ws.merge_cells("A2:H2")
    ws.cell(2, 1, "מיפוי מק\"ט→סניף מתוך data/reference/metropoline_line_branch_map.csv").font = \
        Font(name=ARIAL, italic=True, size=9, color="808080")
    header_row(ws, ["סניף", "מתוכנן", "בוצע", "אי-בוצע", "% ביצוע", "% אי-ביצוע",
                    "אי-ביצוע ק\"מ", "קווים (מק\"ט)"], row=4)
    br_agg: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    br_mkts: dict[str, set] = collections.defaultdict(set)
    for r in metro_recs:
        bname = r.branch or UNASSIGNED_BRANCH
        br_agg[bname]["planned"] += r.planned
        br_agg[bname]["executed"] += r.executed
        br_agg[bname]["nonexec"] += r.nonexec
        br_agg[bname]["nonexec_km"] += r.nonexec_km
        if r.mkt:
            br_mkts[bname].add(r.mkt)
    rr = 5
    for bname in sorted(br_agg, key=lambda x: -br_agg[x]["planned"]):
        b = br_agg[bname]
        ws.cell(rr, 1, bname)
        ws.cell(rr, 2, b["planned"]).number_format = NUM
        ws.cell(rr, 3, b["executed"]).number_format = NUM
        ws.cell(rr, 4, b["nonexec"]).number_format = NUM
        ws.cell(rr, 5, f"=IF(B{rr}=0,0,C{rr}/B{rr})").number_format = PCT
        ws.cell(rr, 6, f"=IF(B{rr}=0,0,D{rr}/B{rr})").number_format = PCT
        ws.cell(rr, 7, round(b["nonexec_km"])).number_format = KM
        ws.cell(rr, 8, len(br_mkts[bname])).number_format = NUM
        rate = (b["nonexec"] / b["planned"]) if b["planned"] else 0
        ws.cell(rr, 6).fill = BAD_FILL if rate > THRESH_FUNDAMENTAL else (
            WARN_FILL if rate > THRESH_SERVICE else OK_FILL)
        rr += 1
    if rr > 5:
        last = rr - 1
        tot = rr
        ws.cell(tot, 1, "סך מטרופולין").font = Font(name=ARIAL, bold=True)
        for col, letter in [(2, "B"), (3, "C"), (4, "D"), (7, "G")]:
            ws.cell(tot, col, f"=SUM({letter}5:{letter}{last})").number_format = NUM if col != 7 else KM
        ws.cell(tot, 5, f"=IF(B{tot}=0,0,C{tot}/B{tot})").number_format = PCT
        ws.cell(tot, 6, f"=IF(B{tot}=0,0,D{tot}/B{tot})").number_format = PCT
        for j in range(1, 9):
            ws.cell(tot, j).fill = HDR_FILL
            ws.cell(tot, j).font = Font(name=ARIAL, bold=True, color="FFFFFF")
    style_body(ws, 5, 8); autosize(ws, [18, 12, 12, 12, 10, 12, 14, 12])

    # מטריצה סניף × יום
    ws = new_sheet("מטרופולין סניף×יום")
    ws.merge_cells("A1:I1")
    ws.cell(1, 1, "מטרופולין — % אי-ביצוע לפי סניף × יום").font = TITLE_FONT
    day_cols = [d for d in days]
    headers = ["סניף"] + [f"{HEB_DOW[d.dow]} {d.date.strftime('%d/%m')}" for d in day_cols] + ["שבוע"]
    header_row(ws, headers, row=3)
    pe_br: dict[tuple[str, dt.date], list[int]] = collections.defaultdict(lambda: [0, 0])
    pe_br_week: dict[str, list[int]] = collections.defaultdict(lambda: [0, 0])
    for r in metro_recs:
        bname = r.branch or UNASSIGNED_BRANCH
        pe_br[(bname, r.date)][0] += r.planned
        pe_br[(bname, r.date)][1] += r.executed
        pe_br_week[bname][0] += r.planned
        pe_br_week[bname][1] += r.executed
    # גם ימים לא-תקפים להצגה (מסומנים)
    for r in recs:
        if r.op != METROPOLINE_OPERATOR_REF:
            continue
        if r.date in valid_dates and (r.date, r.op) not in strike:
            continue  # כבר נספר ב-metro_recs
        bname = r.branch or UNASSIGNED_BRANCH
        pe_br[(bname, r.date)][0] += r.planned
        pe_br[(bname, r.date)][1] += r.executed
    branch_names = sorted(
        {b for (b, _) in pe_br} | set(pe_br_week),
        key=lambda x: -(pe_br_week.get(x, [0])[0]),
    )
    for i, bname in enumerate(branch_names):
        rr = 4 + i
        ws.cell(rr, 1, bname).font = Font(name=ARIAL, bold=True)
        for j, d in enumerate(day_cols, start=2):
            p, e = pe_br[(bname, d.date)]
            cell = ws.cell(rr, j)
            if not d.percentages or p == 0:
                cell.value = "—"; cell.alignment = CENTER
            else:
                rate = 1 - e / p
                cell.value = rate; cell.number_format = PCT
                if not d.valid or METROPOLINE_OPERATOR_REF in d.strike_ops:
                    cell.fill = WARN_FILL
                elif rate > THRESH_FUNDAMENTAL:
                    cell.fill = BAD_FILL
                elif rate > THRESH_SERVICE:
                    cell.fill = WARN_FILL
        # סיכום שבוע (ימי חול תקפים בלבד)
        wp, we = pe_br_week.get(bname, [0, 0])
        cell = ws.cell(rr, 2 + len(day_cols))
        if wp == 0:
            cell.value = "—"; cell.alignment = CENTER
        else:
            rate = 1 - we / wp
            cell.value = rate; cell.number_format = PCT
            cell.fill = BAD_FILL if rate > THRESH_FUNDAMENTAL else (
                WARN_FILL if rate > THRESH_SERVICE else OK_FILL)
    style_body(ws, 4, len(headers))
    autosize(ws, [18] + [11] * len(day_cols) + [10])
    note_r = 4 + len(branch_names) + 1
    ws.cell(note_r, 1,
            "כתום = יום/מפעיל מוחרג או חריגה מעל 2.1%;  אדום = מעל 2.5%;  — = יום לא-תקף/ללא תכנון"
            ).font = Font(name=ARIAL, italic=True, size=9, color="808080")

    # סניף × אשכול
    ws = new_sheet("מטרופולין סניף×אשכול")
    ws.merge_cells("A1:G1")
    ws.cell(1, 1, "מטרופולין — אי-ביצוע לפי סניף × אשכול (ימי-חול תקפים)").font = TITLE_FONT
    header_row(ws, ["סניף", "אשכול", "מתוכנן", "בוצע", "אי-בוצע", "% ביצוע", "% אי-ביצוע"], row=3)
    sc: dict[tuple[str, str], collections.Counter] = collections.defaultdict(collections.Counter)
    for r in metro_recs:
        key = (r.branch or UNASSIGNED_BRANCH, r.cluster or UNASSIGNED_BRANCH)
        sc[key]["planned"] += r.planned
        sc[key]["executed"] += r.executed
        sc[key]["nonexec"] += r.nonexec
    rr = 4
    for (bname, cluster) in sorted(sc, key=lambda k: (-sc[k]["planned"], k[0], k[1])):
        b = sc[(bname, cluster)]
        ws.cell(rr, 1, bname); ws.cell(rr, 2, cluster)
        ws.cell(rr, 3, b["planned"]).number_format = NUM
        ws.cell(rr, 4, b["executed"]).number_format = NUM
        ws.cell(rr, 5, b["nonexec"]).number_format = NUM
        ws.cell(rr, 6, f"=IF(C{rr}=0,0,D{rr}/C{rr})").number_format = PCT
        ws.cell(rr, 7, f"=IF(C{rr}=0,0,E{rr}/C{rr})").number_format = PCT
        rate = (b["nonexec"] / b["planned"]) if b["planned"] else 0
        ws.cell(rr, 7).fill = BAD_FILL if rate > THRESH_FUNDAMENTAL else (
            WARN_FILL if rate > THRESH_SERVICE else OK_FILL)
        rr += 1
    style_body(ws, 4, 7); autosize(ws, [18, 22, 12, 12, 12, 10, 12])

    # ---- רגישות סניף 500 (כיול מול BI 2–3%) ----
    ws = new_sheet("סניף 500 — רגישות")
    ws.merge_cells("A1:F1")
    ws.cell(1, 1, "סניף 500 — רגישות חלונות מול עוגן Power BI (~2.5%)").font = TITLE_FONT
    ws.merge_cells("A2:F2")
    ws.cell(
        2, 1,
        "הסיכום הראשי משתמש בא׳–ג׳ תקפים בלבד; כאן מוצגים גם חלונות רחבים יותר לכיול. "
        "Stride=אי-יציאה 2.1.1 בלבד (חסם תחתון)."
    ).font = Font(name=ARIAL, italic=True, size=9, color="808080")
    header_row(ws, ["חלון", "מתוכנן", "בוצע", "אי-בוצע", "% אי-ביצוע", "הערה"], row=4)

    all_metro_500 = [r for r in recs if r.op == METROPOLINE_OPERATOR_REF and r.branch == "500"]
    window_defs = branch_sensitivity_windows(recs, days, branch="500")
    # עוגני BI שמורים (כיול 08/07) — לא נשלפים חיים בכל הרצה
    bi_anchor_pct = 0.0253
    rr = 5
    for wdef in window_defs:
        p, e, n = wdef["planned"], wdef["executed"], wdef["missing"]
        ws.cell(rr, 1, wdef["label"])
        ws.cell(rr, 2, p).number_format = NUM
        ws.cell(rr, 3, e).number_format = NUM
        ws.cell(rr, 4, n).number_format = NUM
        cell = ws.cell(rr, 5, wdef["pct"])
        cell.number_format = PCT
        rate = wdef["pct"]
        cell.fill = BAD_FILL if rate > THRESH_FUNDAMENTAL else (
            WARN_FILL if rate > THRESH_SERVICE else OK_FILL)
        ws.cell(rr, 6, wdef["note"]).font = Font(name=ARIAL, italic=True, size=9, color="808080")
        rr += 1
    ws.cell(rr, 1, "עוגן BI אגרגט סניף 500")
    ws.cell(rr, 5, bi_anchor_pct).number_format = PCT
    ws.cell(rr, 5).fill = WARN_FILL
    ws.cell(rr, 6, "מכיול 08/07 (METROPOLIN_BRANCHES_AGG=2.53%); יום 05/07 היה 5.31%=BI")
    rr += 2
    ws.cell(rr, 1, "פירוט יומי — סניף 500").font = Font(name=ARIAL, bold=True, color="1F4E78")
    rr += 1
    header_row(ws, ["תאריך", "יום", "סיווג", "תקף?", "מתוכנן", "אי-בוצע", "%"], row=rr)
    day_hdr = rr
    rr += 1
    pe500: dict[dt.date, list[int]] = collections.defaultdict(lambda: [0, 0])
    for r in all_metro_500:
        pe500[r.date][0] += r.planned
        pe500[r.date][1] += r.executed
    for d in days:
        p, e = pe500.get(d.date, [0, 0])
        ws.cell(rr, 1, d.date.isoformat())
        ws.cell(rr, 2, HEB_DOW[d.dow]).alignment = CENTER
        ws.cell(rr, 3, d.classification)
        ws.cell(rr, 4, "כן" if (d.valid and d.is_workday) else "לא").alignment = CENTER
        ws.cell(rr, 5, p).number_format = NUM
        ws.cell(rr, 6, p - e).number_format = NUM
        cell = ws.cell(rr, 7)
        if p == 0:
            cell.value = "—"
            cell.alignment = CENTER
        else:
            rate = 1 - e / p
            cell.value = rate
            cell.number_format = PCT
            if not (d.valid and d.is_workday):
                cell.fill = WARN_FILL
            elif rate > THRESH_FUNDAMENTAL:
                cell.fill = BAD_FILL
            elif rate > THRESH_SERVICE:
                cell.fill = WARN_FILL
        rr += 1
    style_body(ws, 5, 6)
    style_body(ws, day_hdr + 1, 7)
    autosize(ws, [28, 12, 12, 12, 12, 55])

    # ---- גיליון 4: חשיפת קנסות ----
    ws = new_sheet("חשיפת קנסות")
    ws.merge_cells("A1:K1")
    ws.cell(1, 1, "חשיפת קנסות לפי מפעיל×אשכול×יום (נספח כ\"ו, צמוד מדד 2/2012)").font = TITLE_FONT
    headers = ["תאריך", "יום", "מפעיל", "אשכול", "מתוכנן", "אי-בוצע", "% אי-ביצוע",
               "תעריף ₪/הפרה", "חשיפה משתנה ₪", "קנס קבוע ₪", "סה\"כ ₪"]
    header_row(ws, headers, row=3)
    rr = 4
    for p in penalties:
        ws.cell(rr, 1, p.date.isoformat()); ws.cell(rr, 2, p.dow).alignment = CENTER
        ws.cell(rr, 3, p.op_name); ws.cell(rr, 4, p.cluster)
        ws.cell(rr, 5, p.planned).number_format = NUM
        ws.cell(rr, 6, p.nonexec).number_format = NUM
        ws.cell(rr, 7, f"=IF(E{rr}=0,0,F{rr}/E{rr})").number_format = PCT
        ws.cell(rr, 8, p.tariff).number_format = SHK
        ws.cell(rr, 9, f"=F{rr}*H{rr}").number_format = SHK
        # קנס קבוע חי לפי סף 4.5%
        ws.cell(rr, 10, f"=IF(G{rr}>{CLUSTER_DAY_FIXED_THRESHOLD},{CLUSTER_DAY_FIXED_PENALTY},0)").number_format = SHK
        ws.cell(rr, 11, f"=I{rr}+J{rr}").number_format = SHK
        if p.over_threshold:
            for j in range(1, 12):
                ws.cell(rr, j).fill = BAD_FILL
        rr += 1
    if rr > 4:
        last = rr - 1
        ws.cell(rr, 4, "סך חשיפה").font = Font(name=ARIAL, bold=True)
        ws.cell(rr, 9, f"=SUM(I4:I{last})").number_format = SHK
        ws.cell(rr, 10, f"=SUM(J4:J{last})").number_format = SHK
        ws.cell(rr, 11, f"=SUM(K4:K{last})").number_format = SHK
        for j in range(1, 12):
            ws.cell(rr, j).fill = HDR_FILL; ws.cell(rr, j).font = Font(name=ARIAL, bold=True, color="FFFFFF")
    style_body(ws, 4, 11); autosize(ws, [11, 5, 14, 20, 11, 11, 11, 13, 14, 12, 13])

    # ---- גיליון 5: השוואת P95 ----
    ws = new_sheet("השוואת P95")
    ws.merge_cells("A1:H1")
    ws.cell(1, 1, "אחוז שירות שלא הופעל מול בסיס P95 (יום-חול ממוצע)").font = TITLE_FONT
    headers = ["מפעיל", "בסיס P95 נסיעות", "ממוצע יומי בוצע", "% שלא הופעל (נסיעות)",
               "בסיס P95 ק\"מ", "ממוצע יומי בוצע ק\"מ", "% שלא הופעל (ק\"מ)"]
    header_row(ws, headers, row=3)
    rr = 4
    for op in OPERATORS:
        a = op_agg[op]; vd = max(a.get("valid_days", 0), 1)
        p95_rides, p95_km = P95[op]
        avg_rides = a.get("executed", 0) / vd
        avg_km = a.get("executed_km", 0) / vd
        ws.cell(rr, 1, OPERATORS[op])
        ws.cell(rr, 2, p95_rides).number_format = NUM
        ws.cell(rr, 3, round(avg_rides)).number_format = NUM
        ws.cell(rr, 4, f"=IF(B{rr}=0,0,1-C{rr}/B{rr})").number_format = PCT
        ws.cell(rr, 5, p95_km).number_format = KM
        ws.cell(rr, 6, round(avg_km)).number_format = KM
        ws.cell(rr, 7, f"=IF(E{rr}=0,0,1-F{rr}/E{rr})").number_format = PCT
        rr += 1
    style_body(ws, 4, 7); autosize(ws, [16, 16, 16, 16, 16, 18, 16])
    ws.cell(rr + 1, 1, "% שלא הופעל = 1 − בוצע/P95.  P95 = אחוזון 95 של יום-חול (חושב 10.6.2026).").font = \
        Font(name=ARIAL, italic=True, size=9, color="808080")

    # ---- גיליון 6: אי-דיוק ----
    ws = new_sheet("אי-דיוק")
    ws.merge_cells("A1:D1"); ws.cell(1, 1, "אי-דיוק (סעיף 2.5) — דגימה").font = TITLE_FONT
    rows6 = [
        ("סטטוס", inaccuracy.get("status", "")),
        ("קו שנדגם", inaccuracy.get("sample", "")),
        ("מספר נסיעות בדגימה", inaccuracy.get("n", "")),
        ("נסיעות עם זמן יציאה בפועל שונה", inaccuracy.get("diffs", "")),
        ("פירוט", inaccuracy.get("detail", "")),
    ]
    for i, (k, v) in enumerate(rows6, start=3):
        ws.cell(i, 1, k).font = Font(name=ARIAL, bold=True)
        ws.cell(i, 2, v).font = BASE_FONT
    autosize(ws, [26, 80])

    # ---- גיליון 7: ימים חריגים / החרגות ----
    ws = new_sheet("ימים חריגים")
    ws.merge_cells("A1:E1"); ws.cell(1, 1, "סיווג ימים בשבוע הדיווח").font = TITLE_FONT
    header_row(ws, ["תאריך", "יום", "סיווג", "נכלל בממוצע?", "סיבה / הערה"], row=3)
    rr = 4
    for d in days:
        ws.cell(rr, 1, d.date.isoformat()); ws.cell(rr, 2, HEB_DOW[d.dow]).alignment = CENTER
        ws.cell(rr, 3, d.classification)
        ws.cell(rr, 4, "כן" if (d.valid and d.is_workday and not d.strike_ops) else "חלקי" if d.strike_ops and d.valid else "לא").alignment = CENTER
        ws.cell(rr, 5, d.reason or "—")
        if not d.valid:
            for j in range(1, 6):
                ws.cell(rr, j).fill = WARN_FILL
        rr += 1
    style_body(ws, 4, 5); autosize(ws, [12, 5, 22, 14, 60])

    # ---- גיליון 8: נתוני גלם — מתוכנן מול בוצע לפי קו ----
    ws = new_sheet("גלם — קו×יום")
    header_row(ws, ["תאריך", "יום", "מפעיל", "אשכול", "סניף", "route_mkt", "line_ref",
                    "מתוכנן", "בוצע", "אי-בוצע", "% אי-ביצוע"], row=1)
    rr = 2
    for r in sorted(recs, key=lambda x: (x.date, x.op, -x.nonexec)):
        ws.cell(rr, 1, r.date.isoformat()); ws.cell(rr, 2, r.dow).alignment = CENTER
        ws.cell(rr, 3, r.op_name); ws.cell(rr, 4, r.cluster)
        ws.cell(rr, 5, r.branch or ("—" if r.op != METROPOLINE_OPERATOR_REF else UNASSIGNED_BRANCH))
        ws.cell(rr, 6, r.mkt); ws.cell(rr, 7, r.line)
        ws.cell(rr, 8, r.planned).number_format = NUM
        ws.cell(rr, 9, r.executed).number_format = NUM
        ws.cell(rr, 10, r.nonexec).number_format = NUM
        ws.cell(rr, 11, f"=IF(H{rr}=0,0,J{rr}/H{rr})").number_format = PCT
        rr += 1
    autosize(ws, [11, 5, 14, 18, 16, 10, 10, 9, 9, 9, 11])

    # ---- גיליון 9: ק"מ — מתוכנן מול בוצע ----
    ws = new_sheet("ק\"מ — קו×יום")
    header_row(ws, ["תאריך", "יום", "מפעיל", "line_ref", "ק\"מ/נסיעה",
                    "מתוכנן ק\"מ", "בוצע ק\"מ", "אי-בוצע ק\"מ"], row=1)
    rr = 2
    for r in sorted(recs, key=lambda x: (x.date, x.op, -x.nonexec_km)):
        if r.km_per_ride == 0:
            continue
        ws.cell(rr, 1, r.date.isoformat()); ws.cell(rr, 2, r.dow).alignment = CENTER
        ws.cell(rr, 3, r.op_name); ws.cell(rr, 4, r.line)
        ws.cell(rr, 5, r.km_per_ride).number_format = "0.0"
        ws.cell(rr, 6, round(r.planned_km, 1)).number_format = KM
        ws.cell(rr, 7, round(r.executed_km, 1)).number_format = KM
        ws.cell(rr, 8, round(r.nonexec_km, 1)).number_format = KM
        rr += 1
    autosize(ws, [11, 5, 14, 10, 11, 13, 13, 13])

    # ---- גיליון 10: מתודולוגיה והגדרות ----
    ws = new_sheet("מתודולוגיה")
    ws.merge_cells("A1:B1"); ws.cell(1, 1, "מתודולוגיה, הגדרות וסייגים").font = TITLE_FONT
    lines = [
        ("מקור הגדרות", "נספח כ\"ו (פיצויים מוסכמים) להסכם ההפעלה הסטנדרטי, משרד התחבורה."),
        ("מה נמדד", "אי-יציאה (2.1.1) בלבד: נסיעה מתוכננת (GTFS) ללא נסיעת SIRI תואמת."),
        ("חסם תחתון", "השיעור הרשמי גבוה יותר — כולל גם איחור חמור (2.1.2), הקדמה חמורה (2.1.3) וחפיפה (2.1.4)."),
        ("מתוכנן", "/gtfs_rides_agg/group_by לפי operator_ref,line_ref — total_planned_rides."),
        ("בוצע", "/siri_rides — דה-דופ לפי journey_ref (journey שמסתיים ב-\"-0\" נספר בנפרד); בוצע ≤ מתוכנן לכל קו."),
        ("הצלבת אשכול", "line_ref → route_mkt (/gtfs_routes על כל השבוע, לא שבת בלבד) → OfficeLineId ב-ClusterToLine.zip."),
        ("הצלבת סניף (מטרופולין)", "route_mkt → סניף מתוך data/reference/metropoline_line_branch_map.csv; קו ללא התאמה → 'לא משויך'."),
        ("סניף 500 מול BI", "בסיכום הראשי ~0.4% (א׳–ג׳ תקפים) מול עוגן BI ~2.5%. ראה גיליון «סניף 500 — רגישות»: הפער = חלון (חופש גדול) + הגדרה 2.1.1 בלבד; המיפוי (20 מק״ט) יציב. ביום 05/07 היה התאמה 5.31%=BI."),
        ("מפעילי יעד", "אגד, דן, מטרופולין, סופרבוס, קווים, אלקטרה אפיקים."),
        ("ק\"מ", "אורך ה-shape השכיח לכל route_id ב-GTFS הארצי (Haversine)."),
        ("ימי חול", "ראשון–חמישי בלבד. שישי/שבת — דפוס בסיס שונה, מוצגים בנפרד."),
        ("יום מופחת", f"מתוכנן < {int(REDUCED_PLAN_RATIO*100)}% מחציון יום-חול אצל רוב המפעילים — מוחרג מהממוצע."),
        ("חריג ביצוע", f"תכנון רגיל אך ביצוע מפעיל < {int(STRIKE_EXEC_RATIO*100)}% מהרגיל — המפעיל מוחרג ליום זה."),
        ("תקלת SIRI", "ביצוע נמוך חוצה-מפעילים + נפילת מצוק שעתית לאפס — סומן 'לא תקף', ללא אחוזים, הוחרג מהכול."),
        ("כפל תכנון GTFS", f"מתוכנן קו ≥ פי {DOUBLE_PLAN_RATIO} מחציון יום-חול — הוצב חציון (אחיד על כל ימי החול)."),
        ("ספי שירות", "אי-ביצוע > 2.1% חריגה משירות; > 2.5% הפרה יסודית; אי-דיוק > 4.5% חריגה."),
        ("תעריפי קנס", "מדורג לפי שיעור יומי באשכול: 0/63/93/118/143/173 ₪ להפרה; +5,000 ₪ קבוע מעל 4.5%."),
        ("אי-דיוק", inaccuracy.get("detail", "")),
    ]
    rr = 3
    for k, v in lines:
        ws.cell(rr, 1, k).font = Font(name=ARIAL, bold=True, color="1F4E78")
        c = ws.cell(rr, 2, v); c.font = BASE_FONT; c.alignment = Alignment(wrap_text=True, vertical="top")
        rr += 1
    autosize(ws, [20, 100])
    ws.column_dimensions["B"].width = 100

    wb.save(path)


# ============================================================================
#                                main
# ============================================================================
def run(week_ending: dt.date | None, output_dir: str) -> str:
    import os
    today = dt.date.today()
    if week_ending:
        sat = week_ending
        while sat.weekday() != 5:
            sat -= dt.timedelta(days=1)
        sun = sat - dt.timedelta(days=6)
    else:
        sun, sat = week_ending_saturday(today)
    week = (sun, sat)
    dates = [sun + dt.timedelta(days=i) for i in range(7)]
    print(f"[i] שבוע דיווח: {sun} .. {sat}")

    # מקורות עזר (פעם אחת)
    print("[i] טוען לוח החרגות, אשכולות (ClusterToLine), מיפוי סניף מטרופולין, ואורכי GTFS…")
    calendar_entries = load_calendar()
    clusters = load_clusters()
    branch_map = load_branch_map()
    # טווח מלא של השבוע — לא שבת בלבד (בשבת חסרים קווי חול → "לא משויך")
    line_mkt = fetch_line_to_mkt(sun, sat)
    route_len = load_gtfs_lengths()
    metro_mapped = sum(1 for (op, _), mkt in line_mkt.items() if op == METROPOLINE_OPERATOR_REF and mkt)
    print(f"[i] אשכולות: {len(set(clusters.values()))}, קווי-mkt ממופים: {len(line_mkt)} "
          f"(מטרופולין עם mkt: {metro_mapped}), "
          f"סניפי מטרופולין: {len(branch_map)}, קווים עם אורך: {len(route_len)}")

    # משיכת ימים
    print("[i] מושך מתוכנן + בוצע (SIRI) לכל יום…")
    days: list[DayData] = []
    for d in dates:
        day = fetch_day(d, line_mkt)
        days.append(day)
        print(f"    {HEB_DOW[d.weekday()]} {d}: "
              f"מתוכנן={sum(day.planned_raw.values())}, "
              f"בוצע={sum(sum(v.values()) for v in day.executed.values())}")

    # סיווג ימים חריגים
    print("[i] מסווג ימים חריגים…")
    classify_days(days, calendar_entries)
    for d in days:
        flag = "" if (d.valid and d.is_workday) else f"  ← {d.classification}"
        print(f"    {HEB_DOW[d.dow]} {d.date}: {d.classification}{flag}")

    # רשומות, אגרגציה, קנסות, אי-דיוק
    recs = build_records(days, line_mkt, clusters, route_len, branch_map=branch_map)
    op_agg = aggregate_operator(days, recs)
    penalties = compute_penalties(days, recs)
    inaccuracy = measure_inaccuracy(days, recs)
    print(f"[i] רשומות קו×יום: {len(recs)} | שורות קנס: {len(penalties)} | "
          f"אי-דיוק: {inaccuracy.get('status')}")

    # ייצור xlsx
    os.makedirs(output_dir, exist_ok=True)
    path = os.path.join(output_dir, f"דוח_שבועי_אי_ביצוע_{sat.isoformat()}.xlsx")
    print("[i] בונה xlsx…")
    build_workbook(week, days, recs, op_agg, penalties, inaccuracy, path)
    print(f"[✓] נשמר: {path}")
    return path


def main() -> None:
    ap = argparse.ArgumentParser(description="דוח אי-ביצוע שבועי לתח\"צ בישראל (אי-יציאה 2.1.1)")
    ap.add_argument("--week-ending", type=lambda s: dt.date.fromisoformat(s),
                    default=None, help="תאריך סיום שבוע (שבת). ברירת מחדל: השבוע שהסתיים אתמול.")
    ap.add_argument("--output-dir", default="outputs", help="תיקיית פלט ל-xlsx.")
    args = ap.parse_args()
    path = run(args.week_ending, args.output_dir)
    print(path)


if __name__ == "__main__":
    main()
