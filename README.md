# Open Bus Stride — Codex Integration Pack

חבילת עבודה לחיבור חוברות Open Bus Stride API ל־OpenAI Codex.

## מה יש כאן

- `AGENTS.md` — הוראות פרויקט עבור Codex.
- `skills/open-bus-transit-analysis/SKILL.md` — Skill ייעודי לניתוח תחבורה ציבורית בישראל.
- `docs/` — שתי חוברות העבודה שהועלו: HTML + PDF.
- `src/open_bus_stride_client.py` — לקוח Python בסיסי ובטוח ל־Open Bus Stride API.
- `src/line_reliability_analyzer.py` — כלי אמינות קו מוקשח עם בדיקות איכות לפני KPI.
- `src/endpoint_catalog.py` — יצירת קטלוג endpoints מתוך `openapi.json` חי.
- `src/rtl_dashboard.py` — יצירת דשבורד HTML יחיד בעברית RTL מקובץ reliability CSV.
- `prompts/` — משימות מוכנות להרצה ב־Codex.
- `tests/` — בדיקות בסיסיות ללא קריאה לרשת.

## שימוש מומלץ

1. צור Repository חדש ב־GitHub, למשל `open-bus-stride-analysis`.
2. העלה את כל התיקייה הזו ל־Repository.
3. חבר את GitHub ל־ChatGPT/Codex.
4. פתח Codex על ה־Repository.
5. בקש ממנו לבצע אחת מהמשימות שבתיקיית `prompts/`.

## התקנה מקומית

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pytest
```

## יצירת קטלוג Endpoints

הסקריפט מוריד את `openapi.json` החי של Open Bus Stride ומייצר שלושה קבצים:

```bash
python -m src.endpoint_catalog \
  --openapi-url https://open-bus-stride-api.hasadna.org.il/openapi.json \
  --output-dir docs
```

פלט צפוי:

- `docs/endpoint_catalog.csv`
- `docs/endpoint_catalog.md`
- `docs/endpoint_catalog.html`

כל רשומה כוללת: group, method, path, required params, optional params, response schema, and analyst use case.

## ניתוח אמינות קו

הכלי מריץ planned vs actual לפי `service_date`, `operator_ref` ו־`line_ref`, ומחזיר CSV + HTML RTL עם KPI, בדיקות איכות, ומטא־דאטה לשחזור. אם לא מספקים `line_ref`, הזיהוי חייב להיות חד־משמעי לאחר סינון לפי `route_mkt`, `route_direction` ו־`route_alternative`; אחרת הריצה תיעצר עם רשימת מועמדים במקום לבחור אוטומטית.

פקודת דוגמה עם `line_ref` מפורש:

```bash
python -m src.line_reliability_analyzer \
  --service-date 2026-05-15 \
  --operator-ref 3 \
  --route-short-name 18 \
  --line-ref 3644 \
  --hour-from 5 \
  --hour-to 10 \
  --output-dir outputs
```

פקודת דוגמה לזיהוי לפי מאפייני קו, כאשר אין `line_ref` ידוע מראש:

```bash
python -m src.line_reliability_analyzer \
  --service-date 2026-05-15 \
  --operator-ref 3 \
  --route-short-name 18 \
  --route-mkt 10018 \
  --route-direction 1 \
  --route-alternative "#" \
  --output-dir outputs
```

הפלט כולל `data_quality` עבור `siri_snapshots`, שיעור `gtfs_ride_id` חסר, תקינות חלון תאריך/שעה, וזהות כיוון/חלופה.

## יצירת דשבורד RTL

הדשבורד מקבל CSV שנוצר מכלי אמינות הקו ומייצר HTML יחיד, ללא backend, עם KPI, גרף תכנון מול ביצוע לפי שעה, טבלת נסיעות, ואזור מגבלות/איכות נתונים.

```bash
python -m src.rtl_dashboard \
  --csv outputs/line_reliability_2026-05-15_op_3_line_3644.csv \
  --out outputs/line_reliability_dashboard.html \
  --title "דוח אמינות קו"
```

אם לא מעבירים `--out`, הקובץ ייכתב ליד ה־CSV בשם `<csv-name>.dashboard.html`.

## השוואת מפת Optibus מול בסיס GTFS

הכלי `src/map_vs_gtfs_baseline.py` משווה תיקיית ייצוא של מפת Optibus (`trips.csv`, `routes.csv`, `deadheads.csv`) מול ה־GTFS של המפעיל בתאריך שירות יעד, דרך Stride API. הפלט: קבצי CSV + JSON סיכום.

מה הכלי בודק:

- **מטריצת כיסוי** — מק"ט × כיוון × חלופה: ספירת יציאות במפה מול GTFS ופערים.
- **התאמת יציאות דקה־בדקה** — המרת UTC לשעון ישראל דרך `zoneinfo` (נכון גם לקיץ וגם לחורף, ללא היסט קשיח), כולל נסיעות אחרי חצות. אפשר סובלנות בדקות עם `--match-tolerance-min`.
- **פערי זמני נסיעה** — משך נסיעה במפה מול המשך המתוכנן ב־GTFS, לפי route_key.
- **זיהוי סנפשוט מיושן** — מק"טים במפה שאינם ב־GTFS בתאריך היעד, ומק"טים חדשים ב־GTFS שאינם במפה, עם דמיון קצוות לפי `route_long_name` לאיתור החלפות מק"ט.
- **בדיקות קטלוג ריקות** — רגלי אפס־זמן עם מרחק ממשי, התפלגות מהירויות וסימון מהירויות חריגות, ואסימטריית pull-in/pull-out לפי חניון.

```bash
python -m src.map_vs_gtfs_baseline \
  --map-export-dir /path/to/map_export \
  --service-date 2026-07-10 \
  --operator-ref 34 \
  --output-dir outputs
```

כל פונקציות ההשוואה טהורות (מקבלות DataFrames), כך שאפשר להזרים `gtfs_routes`/`gtfs_rides` מוכנים ל־`run_analysis` ולעבוד גם ללא רשת.

## עקרון עבודה

כל ניתוח חייב להתחיל בזיהוי השאלה התחבורתית, בחירת מקור הנתונים, בדיקת איכות, ורק אז חישוב KPI.
אין לנחש נתונים. אם חסר נתון — לציין זאת במפורש.
