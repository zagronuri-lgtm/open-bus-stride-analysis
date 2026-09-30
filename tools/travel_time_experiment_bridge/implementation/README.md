# חוזה משימות ותור ניסוי מקומי — v1

מימוש Python stdlib בלבד. אין רשת, shell runner, דפדפן או מתאם Optibus. התוצר המרבי של worker הוא `blocked_adapter`; אין סטטוס הצלחה אמיתית.

## API לאינטגרציה

```python
from bridge_contract import validate_job, synthetic_fixture, ContractError
from job_queue import JobQueue, QueueError

payload, server_snapshot = synthetic_fixture()
validated = validate_job(payload, server_snapshot)
# ValidatedJob(run_id: str, digest: str, payload_json: str, snapshot_json: str)
queue = JobQueue('/private/owned/directory/jobs.sqlite')
job_id = queue.submit(payload, server_snapshot)  # opaque UUID hex str
job = queue.claim_next()                       # dict | None
queue.transition(job_id, 'claimed', 'prepared') # None
queue.transition(job_id, 'prepared', 'blocked_adapter')
status = queue.get(job_id)                     # dict | None
history = queue.audit(job_id)                  # list[dict]
# Only after exclusive restart with all prior workers stopped:
count = queue.recover_inflight()               # int
```

החתימות: `validate_job(payload: dict, trusted_snapshot: dict) -> ValidatedJob`; `JobQueue(path: str)`; `submit(payload: dict, trusted_snapshot: dict) -> str`; `claim_next() -> dict | None`; `transition(job_id: str, expected_state: str, new_state: str) -> None`; `recover_inflight() -> int`; `get(job_id: str) -> dict | None`; `audit(job_id: str) -> list[dict]`.

ה-caller חייב לבחור את `trusted_snapshot` מתוך registry שרתי בבעלותו ולא לקבלו מהדפדפן. הספרייה בודקת התאמה לרשומה שנמסרה, ואינה מממשת אימות משתמש, חתימה קריפטוגרפית, עדכניות registry או הרשאה לפעולה חיצונית. SHA הוא שלמות בלבד. יש להגביל הרשאות לתיקיית מסד הנתונים; אין לכלול סודות באף שדה. שדות טקסט אינם המקום למסמכי מקור גולמיים. רשומת התור מכילה JSON מאומת של החבילה וה-snapshot; audit מכיל רק מזהה/מעברי מצב/זמן, ללא פרומפטים, credentials או חריגות גולמיות.

## מבנה מדויק

כל המפתחות חובה; מפתחות נוספים אסורים בכל רמה.

| שדה בחבילת הדפדפן | ערך |
|---|---|
| `schema_version` | המספר השלם 1 |
| `run_id` | מזהה ריצה לא ריק |
| `source` | בדיוק `dataset_id`, `schedule_id`, `revision`, `sha256`; כולם מחרוזות |
| `branch`, `day_type`, `season` | מחרוזות הקשר |
| `service_dates` | רשימה לא ריקה של תאריכי ISO ייחודיים |
| `engine_version`, `policy_version` | מחרוזות גרסה |
| `selection_trip_ids` | רשימת מזהים ייחודיים שנבחרו בדפדפן; אינה ראיה |
| `trips` | רשימה מלאה, כולל נסיעות שלא משתנות |

לכל נסיעה בחבילה בדיוק `id`, `operator`, `makat`, `line_number`, `direction`, `alternative` כמחרוזות, וכן `departure`, `before_arrival`, `after_arrival` כמספרים שלמים בטווח 0–4320. bool ו-float נדחים, לרבות NaN/Infinity. מזהה `id` חייב להיות ייחודי בכלל הרשימה; יתר מפתחות הזהות חייבים להתאים לשרת. טקסט מוגבל ל-512 תווים, ללא תווי בקרה.

ב-snapshot אותם שדות ראשיים **ללא** `run_id` ו-`selection_trip_ids`. בכל נסיעת snapshot אין `after_arrival`, ובמקומו אובייקט `evidence` עם בדיוק:

- `status`: אחד מ-`change_allowed`, `review`, `blocked`.
- `recommended_arrival`: זמן ההמלצה השרתי בדקות יום שירות.
- `verified_service_dates`: תאריכים שנבדקו, ברשימה לא ריקה וייחודית.
- `verified_day_type`: סוג היום שאומת.
- `reason`: תיאור קצר של הראיה; ללא סודות.

כל זהות המקור, ההקשר, התאריכים והגרסאות מושווים לשרת. אין הוספה או מחיקה של נסיעות, שינוי זהות, הזזת יציאה, משך שלילי או קיצור. שינוי דורש בחירה, `change_allowed`, זמן סוף זהה להמלצה השרתי, וכיסוי כל תאריכי היעד וסוג היום. מספר קו `line_number=277` בכיוון 1 קפוא, ללא תלות במק״ט (לדוגמה 10277). נסיעות ללא שינוי נשמרות גם אם הסטטוס הוא review/blocked.

הדוגמה `synthetic_fixture()` מחזירה `(payload, snapshot)` חדשים בכל קריאה. היא כוללת נסיעה יוצאת בדקה 1500 (25:00) שמוארכת מ-1540 ל-1545, ונסיעה נפרדת בקו 277/1 שנשמרת ללא שינוי. הבחירה היא רק `late`; ה-snapshot מכיל את מלוא המקור. זו דוגמה סינתטית, לא הוכחת כשירות נתונים אמיתיים.

## עמידות ומצבים

ה-digest מכסה את מלוא החבילה ואת מלוא ה-snapshot, כולל ראיות והמלצות. סדר נסיעות ורשימות תאריכים מנורמל לפני hashing. אותו run_id ואותו digest מחזירים אותו job_id. אותו run_id עם digest שונה נדחה, גם לאחר סיום. run_id שונה מייצר ריצה נפרדת.

`submit`, `claim_next`, `transition`, `recover_inflight` עובדים בעסקאות `BEGIN IMMEDIATE`. claim מאמת שוב את החבילה וה-digest השמורים לפני מסירתם. התור משתמש ב-SQLite synchronous=FULL; כל פעולת מצב ו-audit נשמרים באותה עסקה. התור דורש קובץ מתמשך; `:memory:` אינו נתמך משום שכל פעולה פותחת חיבור נפרד.

מסלול ההכנה התקין: `queued -> claimed -> prepared -> blocked_adapter`. ניתן לעבור מ-claimed או prepared ל-failed. `queued -> claimed` מתבצע רק דרך claim. שאר המעברים נדחים; אין succeeded או חזרה ל-queued. restart recovery מעביר claimed/prepared ל-reconciliation_required ומוסיף audit; אין retry. יש לקרוא recovery רק כשה-workers הקודמים עצרו, ולא במקביל לעבודה פעילה. אין פתרון reconciliation אוטומטי במימוש זה.

בנוסף, `claim_next` מעביר פריט שהנתונים השמורים שלו פגומים מ־`queued` למצב סופי `failed_integrity`: JSON לא תקין (כולל כפילויות וערכים לא סופיים), הפרת חוזה או אי־התאמה ב־digest/run_id. המצב ו־audit נשמרים אטומית, ללא טקסט חריגה גולמי; payload, snapshot, digest ו־run_id נשמרים ללא שינוי לחקירה. לאחר ההסגר מחפשים את הפריט התקין הבא. אם נשארו רק פריטים פגומים, מוחזר `None` וההסגר עדיין מבצע commit. אין retry או מעבר יציאה מהסגר, ו־recovery אינו משנה אותו. כשל מסד או audit מגלגל את העסקה לאחור ואינו מוסווה כפגם בנתונים.

## בדיקות

```sh
python3 -m unittest discover -s tests -v
```

26 בדיקות עברו: מקור/גרסאות/זהות, הוספה ומחיקה וכפילויות, זמנים לא תקינים וקיצורים, בחירה מול הרשאה, הקפאת 277/1, ראיות תאריך/יום, זיוף שדות, digest הקושר ראיות, הגשות ו-claims מקבילים, עמידות פתיחה מחדש, חסימת שינוי מסד, מעברים אסורים ושחזור ללא retry. נכללות שש בדיקות ייעודיות להסגר: המשך לפריט תקין, commit כאשר רק פגום נותר, סוגי פגימה ושימור נתונים, מצב סופי, claims מקבילים ו־rollback בכשל audit. זו בדיקת חוזה ותור מקומית בלבד; לא הופעל ניסוי אמיתי.
