# ממשק חבילת ניסוי — סקירה בלבד

עותק מקור לשילוב במד־זמנים, מתאריך 30.9.2026. המקור שנבדק: `/Users/uriz/Documents/Codex/2026-09-21/new-chat/work/mad-zmanim/build`.

הכפתור „חבילת ניסוי לקלוד — לסקירה בלבד” זמין לאחר בדיקת הנסיעות הנבחרות. לחיצה מכינה קישור להורדת JSON מקומית; נדרשת לחיצה בקישור להורדת הקובץ. בחירה או חתך שהשתנו מבטלים את התוצאה ואת קישור ההורדה. אין פנייה ל-Claude מתוך הכפתור ואין הגשה לשירות או לתור.

החבילה מסומנת `package_kind: review_only`, `authorization: none`. היא שומרת את **כל** נסיעות המקור עם `before_arrival == after_arrival`; ההצעות לנסיעות הנבחרות נמצאות בנפרד ב־`candidates`. נשמרים tier וראיות, ללא קידום R ל־A. `operator`, `dataset_id`, `revision` ו־`service_dates` חסרים במקור הנוכחי ונשמרים כ־null עם רשימת חסרים. מספר קו נלקח מ־Sign בלבד, בלי השלמה באמצעות מק״ט. SHA מזהה נכס ואינו הרשאת שינוי או snapshot שרתי מהימן.

## קבצים ותלויות

- `experiment_review_package.js`: בנאי החבילה, CommonJS או `window.ExperimentReviewPackage`.
- `optibus_export_ui.js`: מקור ממשק מד־זמנים המלא, כולל נקודת השילוב בבחירת נסיעות.
- `optibus_update_trips.js`: מנוע המקור הנדרש לחישובי הממשק ולבדיקות.
- `tests/test_experiment_review_package.cjs`, `tests/test_selected_trips.cjs`: בדיקות בנאי ובקר בחירה עם DOM מצומצם ומפות סינתטיות בזיכרון.

הבדיקות משתמשות ב־Node.js בלבד, ללא חבילות npm, רשת או מפות גולמיות. הממשק המלא תלוי במעטפת מד־זמנים וב־globals שלה (`D`, `M`, `BR`, `DAYS`, `CELLS`, `state`, `usable`, `OptibusUpdateTrips`, `DutyCheckRecs` ושאר מודולי המעטפת). זו אינה אפליקציה עצמאית. לא הועתקו index.html, נתוני dashboard או מפות מקור.

## שילוב בבונה הקיים

יש להשוות לפני החלפה עם גרסת היעד. שלושת קובצי JS יושבים לצד מודולי `build` הקיימים. בתוך לולאת המודולים של `wrap_index.py`, מיד אחרי קריאת המודול ולפני בדיקות `</script` ושער הטקסט, מוסיפים prepend:

```python
module = (ROOT / "build" / name).read_text(encoding="utf-8")
if name == "optibus_export_ui.js":
    module = (ROOT / "build" / "experiment_review_package.js").read_text(encoding="utf-8") + "\n" + module
```

כך בנאי החבילה נטען לפני הממשק באותו marker קיים, ושני הקבצים עוברים את בדיקות העטיפה. אין צורך להוסיף marker ל־template. יש לבנות לתיקיית preview מקומית עם payload קיים ומאומת באמצעות הבונה הקיים; ההעתקה לריפו אינה בנייה או פרסום אתר.

## בדיקות ואימות

משורש הריפו:

```sh
node --test tools/travel_time_experiment_bridge/ui/tests/test_experiment_review_package.cjs tools/travel_time_experiment_bridge/ui/tests/test_selected_trips.cjs
```

25 בדיקות עברו מקומית גם מתוך העותק שבתיקייה זו. הבדיקות מכסות שימור המקור, כיוונים נפרדים, שעות 24+, חסרים, כפילויות, זהות, בחירה וביטול קישור בעת שינוי חתך. הן אינן בדיקת דפדפן מלאה או אישור כשירות באופטיבוס.

בדיקת הדפדפן שנמסרה בשיחת השילוב: הורדה פיזית בכרום של `Downloads/סקירת_ניסוי_0ddgJeKOma (1).json`, עם 1,592 נסיעות מקור ושני מועמדים, ללא שינוי בזמני המקור; SHA-256: `e6e14e1c2e2ff31e76a39738f117746eb3940d73e23c8331e675af3dfdc02eaf`. הורדה פיזית ב־IAB לא אומתה. זו ראיית בדיקה שנמסרה מסוכן השילוב, ולא הורדה חוזרת במסגרת העתקת הקבצים.

הפעלת Claude CLI על מטא־נתונים בלבד אומתה בנפרד בשיחת השילוב; היא אינה בדיקת הממשק ואינה מעידה על הפעלה באופטיבוס. חבילת UI זו אינה payload לחוזה `JobQueue.submit`; חיבור שינוי דורש registry שרתי, זהויות ותאריכים מאומתים וראיות `change_allowed`. האתר הציבורי לא השתנה במסגרת עבודה זו. לא בוצעו deploy, git add או commit.
