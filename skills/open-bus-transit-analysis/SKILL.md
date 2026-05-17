---
name: open-bus-transit-analysis
description: Use this skill when working on Israeli public transport analysis with Open Bus Stride API, GTFS, SIRI, data.gov.il, route reliability, planned vs actual, headway, commercial speed, stop arrivals, and operational KPI.
---

# Open Bus Transit Analysis Skill

## מתי להשתמש

השתמש בסקיל זה כאשר המשימה קשורה ל:

- Open Bus Stride API
- GTFS תכנון מול SIRI ביצוע
- ניתוח קווים, תחנות, נסיעות, מפעילים, כיוונים וחלופות
- KPI כגון ביצוע נסיעות, איחורים, headway, מהירות מסחרית, צווארי בקבוק ואמינות
- data.gov.il או מקורות משלימים לתחבורה ציבורית בישראל

## זרימת עבודה מחייבת

1. הגדר את שאלת התחבורה.
2. קבע האם מקור הנתונים הוא GTFS, SIRI, user cases, aggregations או מקור חיצוני.
3. זהה מפתחות: תאריך שירות, operator_ref, line_ref, route_mkt, כיוון, חלופה, journey_ref, gtfs_ride_id.
4. בדוק איכות נתונים לפני KPI.
5. חשב KPI רק לאחר סינון מתאים.
6. החזר מסקנה ניהולית ולא רק טבלה.

## כללי זהירות

- route_short_name אינו מפתח מספיק.
- GTFS הוא יומי; אל תערבב ימים בלי כוונה.
- SIRI תלוי בקליטה ובמצב snapshot.
- actual arrival אינו תמיד אירוע עצירה מפורש.
- אם endpoint אגרגטיבי נכשל, עבור למשיכה מפורקת ובנה אגרגציה בעצמך.

## תוצרים רצויים

- Python module
- Notebook
- HTML dashboard RTL
- Excel workbook
- Executive memo בעברית
- Validation report
