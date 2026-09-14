<div dir="rtl" align="right">

# מקור קטלוג-ה-endpoints - תצלום מתוארך

_נוסף 14.9.2026 (C-031 סבב 2, P3-3). הקטלוג (`endpoint_catalog.csv/.md/.html`) נוצר מ-OpenAPI חי; בלי תצלום וגיבוב אי-אפשר להבחין בין שינוי ב-API לשינוי בקטלוג._

| פריט | ערך |
|---|---|
| מקור | `https://open-bus-stride-api.hasadna.org.il/openapi.json` |
| מועד המשיכה (date) | 2026-09-14 09:22:36 +0300 |
| sha256 של המפרט | `97b3366428b62260e39933f91dfe4e21fc5be1157610d34f80ca6071a513c65b` |
| גודל | 130,045 בייט |
| info.title / info.version | Open Bus Stride API / `6bda55d25d1b155383da485f09a607d086866a3f` |
| מספר נתיבים (paths) | 27 |
| תצלום שמור בריפו | `docs/openapi_snapshot_2026-09-14.json` (אותו sha) |
| פקודת הייצור | `python -m src.endpoint_catalog --openapi-url https://open-bus-stride-api.hasadna.org.il/openapi.json --output-dir docs` |

**הערה על הריצה של 13.9:** הקטלוג נוצר לראשונה ב-13.9 בלי תצלום. ב-14.9 הרצתי את הייצור מחדש מול המפרט החי ושמרתי תצלום: שלושת קובצי הקטלוג יצאו **זהים** (אין דיף בגיט), ושתי משיכות עוקבות של המפרט נתנו אותו sha - ולכן הקטלוג בגיט תואם למפרט שה-sha שלו רשום כאן.

**איך מאמתים בעתיד:** `shasum -a 256 docs/openapi_snapshot_2026-09-14.json` = ה-sha למעלה; להשוות למשיכה חדשה של `openapi.json` - sha שונה = ה-API השתנה, ואז מייצרים קטלוג חדש ומוסיפים שורה כאן עם תצלום חדש.

</div>
