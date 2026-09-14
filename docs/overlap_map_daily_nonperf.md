<div dir="rtl" align="right">

# מיפוי החפיפה: הריפו הזה מול הכלים היומיים של מודיעין-השוק

_דנטה, 13.9.2026, שלב 1 (מסמך בלבד; שום שינוי בקוד). הכלים היומיים: `tools/daily_nonperf.py` (2,746 שורות, גרסה 1.8.3) ו-`tools/nonperf_cumulative.py` (846 שורות) בריפו `optibus-mcp`, בעלים: סוכן אי-ביצוע ומודיעין-שוק. הכלל: אם הריפו הזה צריך משהו מהם - מייבאים, לא מעתיקים; ולהפך._

## 1. מה כל צד עושה (ברמת הפונקציה)

| נושא | `src/weekly_report.py` (כאן) | `tools/daily_nonperf.py` (שם) | `tools/nonperf_cumulative.py` (שם) |
|---|---|---|---|
| חלון-זמן | שבוע שמסתיים בשבת (`week_ending_saturday`) | יום D-1 עם נפילה ל-D-2 אם אין GTFS/SIRI מלא | מצטבר מקבצי ה-JSON היומיים |
| תכנון | `fetch_planned` - `GET /gtfs_rides_agg/group_by` (אגרגציה, קריאה אחת ליום, מפתח (operator_ref, line_ref) → total_planned_rides; **לא** נסיעות גולמיות) | `fetch_planned_agg` (אותה אגרגציה) + `fetch_rides_for_routes` (gtfs_rides/list פר-מסלול במקביל, ברמת-נסיעה) | לא מושך; קורא JSON יומי |
| ביצוע | `fetch_executed` - siri_rides פר מפעיל×יום, ספירות + רשימות | `fetch_siri_rows` מדורג 5000+offset, `build_siri_keys` | לא מושך |
| התאמה | `reconcile_day` - ספירות ברמת קו×יום (חסם תחתון 2.1.1) | `tolerance_match` GTFS↔SIRI לפי (line_ref, HH:MM) ±2 דק'; `shift_diagnosis` (מוסט/חסר ±15 דק') | - |
| בסיס-השוואה | חודשי/שבועי לפי לוח-החרגות | חציון 4 אותם ימי-שבוע נקיים (`baseline_candidates`, `analyze_operators`) | `classify_days` משלו |
| שערי-איכות | `detect_cliff` (פרופיל שעתי → צוק = פיד חלקי), `apply_double_planning_cap` | `snapshot_gaps`, `cross_operator_test` ≥15%, `suspect_rate_gate` (שער 8), `derive_status` ok/partial/no_data | - |
| לוח-החרגות | `src/exclusions_calendar.py` על `data/reference/exclusions_2026/` - לסיווג נקראים **01-04 בלבד** (drop/segment/keep); 05 (דריסות פר-סניף) ו-07 אינם נקראים בקוד, 06 ב-loader נפרד שאינו מחובר לשער (ראה CONTRACT.md) | `load_holidays` על קובץ-חגים משלו (`HOLIDAYS_FILE`) | `classify_days` משלו (סוג-יום מתאריך) |
| מיפוי אשכול | `load_clusters` (ClusterToLine.zip) + `cluster_for` → משטר-מכרז (`CLUSTER_REGIME`) | אין | אין |
| אורכי-GTFS | `load_gtfs_lengths` (shapes/stop_times מה-zip הארצי) | אין | `load_lengths` (shape_dist_traveled, חציון לנסיעה, מקובצי LEN.json) |
| קנסות | `compute_penalties` פר-משטר (אונו-אלעד 5/2021, שרון 04/2021, 24/2015, 07/2014), `tariff_from_table`, `measure_inaccuracy` | אין | אין |
| פלט | אקסל שבועי רב-מפעילים (`build_workbook`) | JSON + MD + XLSX יומיים בתקן-האקסל, push_line | אקסל מצטבר 4 סעיפים |
| קליינט | `src/open_bus_stride_client.py` (retries, מטא-דאטה) - **לא** בשימוש ב-weekly_report (יש לו SESSION משלו) | `StrideClient` פנימי משלו (מטמון, דירוג) | מטמון km מקומי |

## 2. איפה יש כפילות אמיתית (אותו דבר כתוב פעמיים)
1. **קליינט Stride** - שלושה מימושים: `open_bus_stride_client.py`, ה-SESSION של `weekly_report.py`, ו-`StrideClient` של daily_nonperf. שלושתם עם retries; רק הראשון עם מטא-דאטה של בקשה.
2. **סוג-יום ולוח-חגים** - `exclusions_calendar.py` (מקור-אמת מתועד; לסיווג 01-04, הדריסות פר-סניף ב-05 עדיין לא ממומשות) מול `load_holidays`/`day_type` של daily_nonperf ו-`classify_days` של nonperf_cumulative. **שני לוחות = סיכון:** יום שמוחרג במקום אחד ונספר במקום אחר.
3. **אורכי-מסלול מ-GTFS** - `load_gtfs_lengths` (כאן: haversine על נקודות ה-shape השכיח לכל route_id) מול `load_lengths` (מצטבר: `shape_dist_traveled` מ-stop_times, חציון לנסיעה, לפי מק"ט-כיוון-חלופה). שתי שיטות שונות על אותו GTFS; **איזו מדויקת יותר לא נבדק** - השוואה על מדגם מסלולים היא צעד נדרש לפני שבוחרים אחת.
4. **גילוי פיד-חלקי** - `detect_cliff` (כאן, פרופיל שעתי) מול `snapshot_gaps` + שערי-הסטטוס (שם). בנוסף החתימה שמצאתי ב-19.8 (יחס SIRI/תכנון יומי, [[stride-partial-feed-day-signature]]) לא ממומשת באף אחד מהם כפונקציה משותפת.
5. **תכנון-מול-ביצוע** - שני מנועים עם הגדרות שונות: weekly = ספירות ברמת קו×יום (חסם תחתון), daily = התאמה ברמת-נסיעה ±2 דק' עם אבחון-הסטה. אלה **לא** אותו מדד, ולכן לא מחליפים זה את זה - אבל הם צריכים אותה שכבת-קלט.

## 3. מה ייחודי לכל צד (לא כפילות)
- **כאן בלבד:** לוח-ההחרגות המתועד (מקור-אמת גם למד-הזמנים - ראה `data/reference/exclusions_2026/CONTRACT.md`), מיפוי אשכול→משטר-מכרז, מנוע-הקנסות לפי נספח כ"ו, שערי-האיכות של `line_reliability_analyzer` (זהות-קו, gtfs_ride_id, בריאות-ETL), קטלוג-ה-endpoints, דשבורד RTL לאמינות-קו.
- **שם בלבד:** ההתאמה ברמת-נסיעה עם אבחון-הסטה, בסיס 4 ימי-השבוע, שערי-הסטטוס והשורה-הדוחפת, המצטבר (מוצאים×שעות, ק"מ 2025 מול 2026), המסירה היומית במייל.

## 4. מועמדים לייבוא בשלב 2 (הצעה, לא ביצוע)
| רכיב | מקור-האמת המוצע | מי מייבא |
|---|---|---|
| קליינט Stride (retries + מטא-דאטה + מטמון) | `open_bus_stride_client.py` (להרחיב במטמון של daily) | weekly_report, daily_nonperf |
| לוח-החרגות + סוג-יום | `exclusions_calendar.py` | daily_nonperf (`load_holidays`), nonperf_cumulative (`classify_days`) |
| אשכול→משטר-מכרז | `weekly_report.load_clusters`/`cluster_for` (לחלץ למודול) | nonperf_cumulative (סעיף מוצאים/קווים לפי אשכול) |
| אורכי-מסלול מ-GTFS | להכריע אחרי השוואה על מדגם (haversine על shapes מול shape_dist_traveled; אין עדיין עדות מי מדויק יותר) ולאמץ מימוש אחד | weekly_report או nonperf_cumulative, לפי תוצאת ההשוואה |
| גלאי יום-פיד-חלקי | פונקציה משותפת חדשה (חתימת 19.8 + `detect_cliff`) | שני הכלים + מד-הזמנים (החרגת ימי-Stride) |
כל ייבוא = שינוי בכלי חי של בעלים אחר → סבב קודקס + הבעלים מבצע אצלו.

</div>
