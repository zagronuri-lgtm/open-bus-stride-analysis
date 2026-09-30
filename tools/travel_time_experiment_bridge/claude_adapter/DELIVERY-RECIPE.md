# מתכון מסירה לקלוד ב-UI — מקובץ מוכן לניסוי מזוהה (מבוסס על הצעדים שבוצעו 30.9.2026)

כל צעד: מה עושים, איך מאמתים, מה מוכח. "אדם" = צעד שנחסם למסווג ההרשאות בסשן Claude Code או שאין לו כלי; אינו הופך לדרישת אישור חדשה — זה תיאור מצב.

| # | צעד | ביצוע | אימות | מעמד |
|---|---|---|---|---|
| 0 | הקפאת המקור | `GET /api/v2/schedule/<origin>?needStats=true&needPreferences=true` → JSON חתום | חתימת נסיעות + sha העדפות + stats | מוכח |
| 1 | עותק מבודד | תפריט החץ ליד שם המפה → SAVE TO NEW DATASET → שם → SAVE | `GET /project/<p>/datasetProps` (דאטהסט חדש), `fetch('/app/schedulesetFolderContent?dataset_props_id=…')` מתוך הדף → unsavedId/savedId; readback API זהה למקור ב-100% | מוכח; **אדם** מקליד/לוחץ (מסווג) |
| 2 | תבנית | ⋯ → EXPORT → EXPORT TRIPS בעותק → קובץ ב-~/Downloads; `sha256` → מניפסט | 1,870 שורות Trips, Ids == API, 0 אי-התאמות זמן | מוכח |
| 3 | הכנה | `prepare(validated_job, template, manifest, out)` (רכיב זה) | `report_<run_id>.json`: status=prepared, diff = {Trips.Arrival: N, StopTimes.Time: N, dropped} | מוכח על התבנית האמיתית (502/502) |
| 4 | העלאה | Update schedule → UPDATE TRIPS → `<input class="hidden-upload update-trips-from-file">` | `POST /app/schedules/updateTrips` → 201; readback API: N שינויים בדיוק, 0 מחיקות/תוספות, Departure ללא שינוי, distances ללא שינוי | מוכח עם קובץ ידני; **אדם** בוחר קובץ בחלונית המובנית; ב-Claude in Chrome: `find "file input"` → `file_upload` (מוכח 12.8 על קטלוגים, לא על update-trips) |
| 5 | SAVE | כפתור Apply Changes | `PUT /app/schedules/<id>` 204; `schedulesetFolderContent.lastOpWasSave=true` + savedId חדש; readback של ה-savedId | מוכח |
| 6 | בדיקת העדפות | `?needPreferences=true`: אם קיים `vehicle_block_duration` ומריצים רכבים → להשבית בפאנל (Expanded View → Vehicle Block Duration → play-pause → SAVE & CLOSE → SAVE) | דיף העדפות ב-API: רק `enabled=false` | מוכח; בלי YAML (מדיניות) |
| 7 | הרצה | גלגל ליד OPTIMIZE → duties/vehicles/allow-unscheduled לפי התצורה המאושרת → OPTIMIZE | "Running Tasks: 1"; `request_parameters` ב-API | מוכח; דורש אישור ריצה מפורש בכל פעם |
| 8 | ניטור | דגימת API כל 3 דק׳ + לוח המשימות (≡ → Task List) כל 30 דק׳; HOLD על תקיעה (23% Creating input files = חתימת VBD) או "Task timed out"; **אין retry אוטומטי** | `general_stats.total_optimization_time` מופיע בסיום | מוכח (96 דק׳ ל-1,870 נסיעות) |
| 9 | קריאה חוזרת והשוואה | SAVE → readback הגרסה השמורה; כל המזהים, 100% שיוך, stack, unfeasible/168-flags, PVR/בלוקים/סידורים/ק"מ/שעות/עלות מול baseline ובקרה; ניתוח סטאק לנסיעות | טבלה חתומה + JSON ראיות | מוכח |
| 10 | הוכחת מקור | readback המקור: חתימה/העדפות/stats זהים ל-#0 | — | מוכח |

מגבלות ידועות: ריצה שנכשלת נעלמת מהפוטר תוך ~דקה (לקרוא HISTORY); הקלסיפייר חוסם צעדי כתיבה בדיאלוגים (1, 4) — פתרון: כלל הרשאה מפורש מאורי או אדם בשער; אין API כתיבה למפות; הגדרות ריצה נשמרות עם הגרסה; DISCARD מחזיר גם אותן.
