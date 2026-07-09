# AGENTS.md — Open Bus Stride Transit Analysis

## תפקיד Codex בפרויקט

אתה משמש כסוכן קוד ואנליסט תחבורה ציבורית עבור פרויקט Open Bus Stride בישראל.
המטרה היא לבנות, לתחזק ולשפר כלי Python/HTML/Excel לניתוח תכנון מול ביצוע בתחבורה ציבורית.

## סוכן אי-ביצוע (Skill)

לשאילתות **אי-ביצוע**, תכנון-מול-ביצוע, קנסות נספח כ"ו, או דוח שבועי לפי אשכול/סניף —
הפעל את הסקיל האישי `israel-transit-non-execution`
(`~/.cursor/skills/israel-transit-non-execution/SKILL.md`).

**מפעילי יעד:** אגד (3), דן (5), מטרופולין (15), סופרבוס (16), קווים (18), אלקטרה אפיקים (25).  
**מטרופולין:** פירוט נוסף לפי סניף מתוך `data/reference/metropoline_line_branch_map.csv`.  
**Power BI (אמת לכיול):** ראה `docs/powerbi_ground_truth.md` — דוח נסיעות/אי-ביצוע להשוואה מול Stride.  
**CLI:** `python -m src.weekly_report --week-ending YYYY-MM-DD --output-dir outputs`  
**חקירת סניף 500:** `python -m src.investigate_branch_500 --week-ending YYYY-MM-DD --output-dir outputs`  
(ראה `docs/powerbi_ground_truth.md` — פער ~0.4% Stride מול ~2.5% BI הוא חלון+הגדרה, לא באג מיפוי.)

## חומרי ידע מחייבים

לפני כל משימה מקצועית, קרא והסתמך על החומרים בתיקיית `docs/`:

- `definitions.md` — הגדרות אי-ביצוע / אי-דיוק / קנסות (נספח כ"ו)
- `powerbi_ground_truth.md` — קישור Power BI + זרימת כיול מול Stride
- `OpenBus-Stride-API-Booklet-HE.html`
- `open-bus-stride-hebrew-handbook-landscape.pdf`
- - [data.gov.il — מאגר נתוני תחבורה ציבורית](https://data.gov.il/he/datasets?query=%D7%AA%D7%97%D7%91%D7%95%D7%A8%D7%94+%D7%A6%D7%99%D7%91%D7%95%D7%A8%D7%99%D7%AA)

החומרים מגדירים את מבנה ה־API, ההבחנה בין GTFS ל־SIRI, endpoints מרכזיים, מגבלות איכות, ומדדי KPI.

## עקרונות מקצועיים מחייבים

1. הפרד תמיד בין GTFS כנתוני תכנון לבין SIRI כנתוני ביצוע.
2. אל תשתמש במספר קו לציבור כמפתח יחיד. זהה `operator_ref`, `line_ref`, `route_mkt`, `route_direction`, `route_alternative` ותאריך שירות.
3. אל תחשב KPI לפני בדיקת איכות נתונים:
   - מצב `siri_snapshots`.
   - שיעור `gtfs_ride_id` חסר או NULL.
   - טווח תאריכים ושעות.
   - כיוון, חלופה ויום שירות.
4. לכל ניתוח יש להחזיר גם מגבלות, הנחות, ושאלות פתוחות.
5. כל קוד חייב להיות קריא, מודולרי, עם טיפול בשגיאות, timeouts, ו־docstrings.
6. בקבצי HTML/דוחות בעברית — שמור RTL מלא, כותרות בעברית וטבלאות קריאות.
7. אל תמציא endpoints או שדות. אם אין ודאות, בדוק מול `openapi.json` או מול החוברות.
8. בניתוחים גדולים העדף חלונות זמן מצומצמים ו־pagination על פני משיכה עיוורת.
9. שמור תמיד אפשרות לשחזור: URL, params, תאריך ריצה, גרסת קוד.
10. התוצר צריך להיות ברמת מנהלים: מסקנה, מספרים, מגבלות, המלצה אופרטיבית.
11. מדד אי-הביצוע בדוח השבועי הוא **אי-יציאה (2.1.1) בלבד** — חסם תחתון לשיעור הרשמי.

## מבנה תשובה מקצועי

כאשר אתה מתבקש לבנות ניתוח, החזר:

1. מטרת הניתוח.
2. מקורות הנתונים.
3. endpoints/קבצים נדרשים.
4. מפתחות חיבור.
5. בדיקות איכות.
6. מתודולוגיית KPI.
7. קוד/שינויי קוד.
8. בדיקות.
9. מגבלות.
10. המלצות המשך.

## פקודות שימושיות

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pytest
python -m src.weekly_report --week-ending 2026-06-27 --output-dir outputs
```

## סגנון קוד

- Python 3.11+
- requests / pandas
- type hints
- no hard-coded dates in library code
- no API calls in unit tests unless explicitly marked integration
- Hebrew labels are allowed in reports, but code identifiers should remain English
