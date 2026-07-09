<div dir="rtl">

# open-bus-stride-analysis

**ניתוח ביצועי תחבורה ציבורית בישראל — אוניברסלי, דינמי, ומבוסס נתונים.**

כלי Python מקצועי מעל [Open Bus Stride API](https://open-bus-stride-api.hasadna.org.il)
לניתוח **תכנון מול ביצוע** של כל מפעיל, כל קו, כל תקופה. שום דבר אינו קבוע מראש
בקוד: רשימת המפעילים, הקווים והנסיעות נשלפת **דינמית** מה-API — כך אותו קוד משרת
אוטובוסים, את רכבת ישראל, או קו בודד בעיר בודדת.

---

## מה זה?

`stride_analysis` הוא חבילת Python שנותנת תשתית אחידה ל:

- **גילוי** — אילו מפעילים וקווים קיימים (`list_operators`, `list_routes`).
- **שליפה** — נסיעות מתוכננות מול בפועל (`fetch_rides`), נתוני זמן-אמת (`fetch_siri`).
- **ניתוח** — אחוז אי-ביצוע, דייקנות, וקנסות לפי נספח כ"ו (`calc_execution`,
  `calc_punctuality`, `calc_penalties`).
- **השוואה** — שינוי בין תקופות (`compare_periods`).
- **דיווח** — דוחות Markdown בעברית (RTL) + תרשימים (`generate_report`, `charts`).

בעתיד: שכבת **בוט** ([stride_analysis/bot/](stride_analysis/bot/README.md)) שתאפשר
לשאול בשפה טבעית — _"מה הביצועים של דן בשבוע שעבר?"_, _"כמה נסיעות ביטלה רכבת ישראל
היום?"_.

---

## התקנה

```bash
git clone <repo-url> open-bus-stride-analysis
cd open-bus-stride-analysis
python -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env              # אופציונלי — לשינוי כתובת ה-API
```

דרישות: Python 3.11+.

---

## שימוש מהיר (CLI)

```bash
# מי המפעילים שקיימים? (נשלף חי מה-API)
python -m stride_analysis --list-operators

# מפעיל יחיד, טווח מפורש, עם דוח עברית
python -m stride_analysis --operator 5 --from 2026-05-24 --to 2026-05-30 --report

# כל המפעילים, השבוע שעבר, עם אומדן קנסות ודוח
python -m stride_analysis --all --last-week --report

# סינון לקו ציבורי מסוים
python -m stride_analysis --operator 15 --route 1 --from 2026-05-24 --to 2026-05-24
```

הפלטים נכתבים ל-`data/output/` (CSV + דוח Markdown).

---

## שימוש בקוד (Python)

```python
from stride_analysis.data.stride_client import StrideClient
from stride_analysis.analysis import calc_execution, calc_penalties, load_penalty_tables
from stride_analysis.reports.generator import generate_report

client = StrideClient()

# 1. גילוי — כל המפעילים (כולל רכבת ישראל), דינמי לחלוטין
operators = client.list_operators(date="2026-05-28")
for op in operators[:5]:
    print(op.operator_ref, op.name, op.route_count)

# 2. שליפה — נסיעות תכנון-מול-ביצוע (operator_ref=None ⇒ כל המפעילים)
rides = client.fetch_rides("2026-05-24", "2026-05-30", operator_ref=5)   # 5 = דן

# 3. ניתוח — ביצוע + קנסות
summary = calc_execution(rides, group_by=["operator_ref", "operator_name", "service_date"])
summary = calc_penalties(summary, load_penalty_tables())

# 4. דיווח — Markdown עברית
md = generate_report(summary, date_from="2026-05-24", date_to="2026-05-30",
                     title="דן", daily=summary)
print(md)
```

> ה-`operator_ref` בדוגמה הוא פרמטר בלבד — גלו את הקוד הנכון עם `--list-operators`
> או `client.list_operators()`. שום ref אינו מקודד בקוד.

---

## ארכיטקטורה

```
stride_analysis/
├── config.py              כתובת API, נתיבים, ספים, logging
├── data/
│   ├── stride_client.py   לקוח גנרי: list_operators, list_routes, fetch_rides, fetch_siri
│   ├── cache.py           cache מקומי אוטומטי (Parquet/JSON) — לא שולפים פעמיים
│   └── models.py          dataclasses: Operator, Route, Ride, ExecutionSummary
├── analysis/
│   ├── execution.py       planned / executed / missing / missing_pct (גנרי על כל DF)
│   ├── punctuality.py     late_pct, avg_delay (דורש SIRI — ראו מגבלות)
│   ├── penalties.py       פרסור אוטומטי של penalties_workbook.xlsx → ₪ למפעיל
│   └── compare.py         compare_periods — שינוי בין תקופות
├── reports/
│   ├── generator.py       generate_report(...) → Markdown עברית RTL
│   ├── charts.py          create_heatmap / create_bar / create_timeline
│   └── templates/         תבנית Jinja2 אופציונלית
├── cli.py                 ממשק שורת פקודה
└── bot/                   placeholder לבוט שפה-טבעית

data/
├── raw/         קבצי גלם (trips.csv, gtfs_rides_*.csv, _rides_execution_raw.csv)
├── cache/       cache אוטומטי של קריאות API  (git-ignored)
├── reference/   penalties_workbook.xlsx (נספח כ"ו)
└── output/      דוחות שנוצרו              (git-ignored)

notebooks/
├── quick_query.ipynb           שאילתה אינטראקטיבית: מפעיל + תאריכים → דוח
├── weekly_all_operators.ipynb  דוח שבועי לכל המפעילים + תרשימים
└── archive/onboard_survey.ipynb ניתוח סקר נוסעים היסטורי (trips.csv)
```

---

## מתודולוגיה ומגבלות

- **GTFS = תכנון, SIRI = ביצוע.** אי-ביצוע מחושב מהתאמת נסיעות מתוכננות לנסיעות
  בזמן-אמת דרך `/rides_execution/list`. נסיעה ללא התאמה נספרת כ"לא בוצעה".
- **דייקנות (איחור/הקדמה) אינה נגזרת מ-`/rides_execution/list`** — זמן היציאה
  בפועל שם מוצמד למתוכנן. לחישוב דייקנות אמיתי השתמשו ב-`fetch_siri()`.
  `calc_punctuality` מדווח כמה שורות היו מדידות ומחזיר `None` במקום אפס מטעה.
- **קנסות** מחושבים לפי נספח כ"ו, טבלה 1 (אי-ביצוע מדורג) בלבד — אומדן, ולא כולל
  קנסות קבועים/בקרה מדגמית/תלונות ציבור. הטבלאות נקראות מהוורקבוק בזמן ריצה.
- ימי שירות מקובצים לפי שעון ישראל (Asia/Jerusalem).
- כל דוח כולל סעיף **מגבלות** ו**שחזור** (URL, endpoints, מספר קריאות).

ראו [AGENTS.md](AGENTS.md) לעקרונות העבודה המלאים.

---

## אוטומציות מתוזמנות ולוח החרגות

ארבעה סוכנים מתוזמנים (GitHub Actions) רצים אוטומטית ופותחים PR עם נתונים מעודכנים:
משיכת מפעילים שבועית, אמינות-קו יומית לפי אשכול (רוטציה), נסועה רבעונית, וקטלוג API.

**מניעת הטיות:** [`src/exclusions_calendar.py`](src/exclusions_calendar.py) מסווג כל
יום שירות כ-`drop` / `segment` / `keep` / `normal` לפי
[לוח ההחרגות](data/reference/exclusions_2026/README.md). אמינות-הקו **מדלגת** על
ימי `drop` (חגים) ו**מחריגה** ימי `segment` (חוה"מ/חופש/עיד) מהממוצע השבועי;
המשיכה השבועית **מתייגת** אותם. כך חגים ותקופות מגזריות אינם מעוותים את התוצאות.

תיעוד מלא — לוחות זמנים, מטריצת כיסוי, ופערים ידועים: [docs/automation_and_exclusions.md](docs/automation_and_exclusions.md).

---

## בדיקות

```bash
pytest                      # בדיקות יחידה, ללא קריאה לרשת
```

---

## Roadmap

- [x] לקוח גנרי דינמי (מפעילים, קווים, נסיעות, SIRI) עם cache/retry/logging
- [x] ניתוח ביצוע + קנסות + השוואת תקופות
- [x] דוחות Markdown עברית + תרשימים
- [x] CLI
- [ ] דייקנות אמיתית מ-SIRI (איחור/הקדמה ברמת תחנה)
- [ ] ניתוח ברמת קו/כיוון/חלופה (לא רק מפעיל/יום)
- [ ] קנסות מלאים (טבלאות 2–5, תלונות ציבור)
- [ ] שכבת בוט שפה-טבעית ([bot/README.md](stride_analysis/bot/README.md))
- [x] תזמון אוטומטי (4 אוטומציות GitHub Actions) + לוח החרגות למניעת הטיות
- [ ] העשרת המניפסט בסניף + ייחודיות-קו (לחיבור טיפול ברמת קו)

---

## רכיבים קודמים

הריפו כולל גם כלים עצמאיים קודמים תחת [src/](src/) (אמינות קו, קטלוג endpoints,
דשבורד RTL) ו-[skills/](skills/) — ראו [docs/](docs/) לחומרי הרקע על ה-API.

## רישיון

ראו [LICENSE](LICENSE).

</div>
