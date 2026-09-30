# גשר ניסוי מד־זמנים — הכנה בלבד

הגשר מאמת חבילת נסיעות מלאה מול registry בבעלות מפעיל השירות, שומר תור מקומי ומכין חבילת מסירה. **השירות נעצר ב־`blocked_adapter`: החבילה הוכנה, אך ההפעלה באופטיבוס טרם חוברה.** אין העלאה או הרצה אוטומטית באופטיבוס.

המנוע הנוכחי מפיק מועמדים; אין להסיק מבחירה בממשק שהמקור, Days או תאריכי היעד אומתו. אין כאן registry אמיתי מאושר להפעלת שינוי. תיקייה זו אינה כוללת טוקנים, מסד תור, פלטי מודל גולמיים או נתוני נוסעים/תפעול.

## מודולים

- `implementation`: חוזה חבילה, בדיקת ראיות ותור SQLite. פריט פגום מועבר ל־`failed_integrity` ללא חסימת הפריטים התקינים הבאים.
- `service`: שרת על `127.0.0.1` בלבד, CLI להגשה/סטטוס, worker להכנה ודף הדגמה בעברית. הטוקן נקרא מקובץ פרטי; הלקוח אינו מספק snapshot.
- `runner`: סיכום מייעץ מוגבל באמצעות Claude CLI, שאינו משנה את מצב התור ואינו מפעיל אופטיבוס. הוא שולח digest וספירות בלבד. בשיחת Claude CLI שנבדקה לא נדרש מפתח API נוסף; נדרשת התחברות קיימת ל־Claude CLI. אין כאן הבטחה שכל התקנה מחוברת, ושום בדיקת יחידה אינה מבצעת שיחה חיה.
- `claude_adapter` ו־`ui`: מתאם קובץ אקסל וחבילת סקירה מהאתר, עם בדיקות ותיעוד נפרדים; המודולים לעיל אינם מפעילים אותם אוטומטית.

## בדיקות מקומיות

מתיקיית השורש של הריפו:

```sh
PYTHONPATH=tools/travel_time_experiment_bridge/implementation python3 -m unittest discover -s tools/travel_time_experiment_bridge/implementation/tests -v
python3 -m unittest discover -s tools/travel_time_experiment_bridge/service/tests -v
PYTHONPATH=tools/travel_time_experiment_bridge/runner python3 -m unittest discover -s tools/travel_time_experiment_bridge/runner -p 'test_*.py' -v
node --check tools/travel_time_experiment_bridge/service/demo.js
```

## שירות מקומי

פקודות מתוך שורש הריפו. החליפו את נתיבי `/private/path` בנתיבים פרטיים שבבעלותכם. תיקיית מצב: הרשאות 0700. קובצי token ו־registry: הרשאות 0600. אין להכניסם לריפו. חוזה registry והכנת הטוקן מתוארים ב־`service/README.md` וב־`implementation/README.md`.

```sh
python3 tools/travel_time_experiment_bridge/service/local_bridge.py serve --state-dir /private/path/state --registry /private/path/registry.json --token-file /private/path/token --port 8765 --allowed-origin http://127.0.0.1:8765
python3 tools/travel_time_experiment_bridge/service/local_bridge.py submit --payload /private/path/payload.json --token-file /private/path/token --port 8765
python3 tools/travel_time_experiment_bridge/service/local_bridge.py worker-once --state-dir /private/path/state --registry /private/path/registry.json --token-file /private/path/token
python3 tools/travel_time_experiment_bridge/service/local_bridge.py status --job-id JOB_ID_FROM_SUBMISSION --token-file /private/path/token --port 8765
```

דף ההדגמה נמצא ב־`http://127.0.0.1:8765/`. רק ה־Origin המקומי המדויק שהוגדר מותר; בהיעדר הגדרה, כל Origin נדחה. CLI עדיף לחיבור כלים. הטוקן אינו קובע הרשאות באופטיבוס.

ה־worker יוצר חבילה פרטית הכוללת את מלוא הנסיעות, ומסיים ב־`blocked_adapter`. מקור שהתיישן גורם ל־`failed`; פגם בשלמות נתון שמור גורם ל־`failed_integrity`. בשום מצב אין retry אוטומטי. לאחר קריסה, reconciliation מחייב עצירת workers ובדיקת מפעיל לפני קריאה ידנית ל־API השחזור.

## סיכום מייעץ אופציונלי

הפעלה זו שולחת ל־Claude מידע מצומצם ודורשת התחברות CLI קיימת. היא אינה חלק מה־worker ולא מופעלת בבדיקות:

```sh
python3 tools/travel_time_experiment_bridge/runner/claude_review.py --review-package /private/path/review-package.json --claude /absolute/path/to/claude --output /private/path/advisory.json
```

`review-package` הוא מעטפת `review_only` לפי חוזה ה־runner, ואינו קובץ `handoff.json` של השירות. הפלט מסומן `advisory_only` ומוחזק כטקסט לא מהימן לצורך עיון; הוא אינו הרשאה או הוכחה לביצוע.

## מתאם אקסל וראיות
`claude_adapter/` מכיל את מתאם קלוד ותיקוני שילוב קודקס. דורש openpyxl; אין רשת. הרצה ובדיקות מתוך התיקייה: `python3 -m unittest discover -s tests -v`. רק `prepare_update_trips.py` הוא ממשק CLI למפעיל; השירות אינו מפעיל אותו אוטומטית.

`ui/` מכיל מודול חבילת הסקירה, בקר הבחירה והתלות בחישוב UPDATE TRIPS; עיינו ב־README שם להטמעה בבונה האתר. הממשק **אינו** מייצר את חוזה ההפעלה של התור: review_only אינו change_allowed.

`evidence/` מכיל רק סיכומי בדיקת הורדה/Claude ומיפוי ה־API המקומי ללא סודות, נתוני נהגים או מפות. היעדר endpoint במפרט הזמין אינו הוכחה שאין אפשרות במוצר.

דוח סטטוס: `../../docs/travel-time-experiment-bridge-status-20260930.md`. אין כאן registry תפעולי. קבצים בעלי status=prepared נבדקו מקומית בלבד; סטטוס זה אינו אישור קליטה, שמירה או הרצה באופטיבוס.
