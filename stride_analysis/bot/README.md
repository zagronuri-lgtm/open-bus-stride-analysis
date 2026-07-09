# bot/ — תכנון שכבת הבוט

מטרה: לאפשר לשאול בשפה טבעית שאלות כמו —

- "מה הביצועים של דן בשבוע שעבר?"
- "כמה נסיעות ביטלה רכבת ישראל היום?"
- "השווה את מטרופולין החודש מול החודש הקודם"
- "מה הקנס הצפוי לקווים על אי-ביצוע אתמול?"

ולקבל תשובה מבוססת-נתונים מ-Open Bus Stride.

## ארכיטקטורה מתוכננת

```
שאלה חופשית
   │
   ▼
parse_question()  ──►  ParsedQuery{intent, operator_ref, date_from, date_to, ...}
   │                         │  (שמות מפעילים נפתרים דינמית מ-list_operators)
   ▼                         ▼
StrideClient.fetch_rides() ──► calc_execution / calc_punctuality / calc_penalties
   │
   ▼
generate_report()  ──►  תשובה קצרה + טבלה (Markdown/טקסט)
```

נקודת החוזה היציבה היא `ParsedQuery` ([handler.py](handler.py)). הפרסור הנוכחי
הוא היוריסטי (regex + מילות מפתח) וחסר-תלויות, כדי לאפשר בדיקות מהירות. בהמשך
הוא יוחלף ב-LLM tool-calling, אך `ParsedQuery` יישאר אותו דבר.

## שלבים

1. **Prototype (קיים)** — `parse_question()` היוריסטי שמזהה intent, חלון תאריכים,
   מפעיל (לפי רשימת מפעילים חיה), וקו.
2. **Executor** — פונקציה שממירה `ParsedQuery` להרצת הפייפליין ומחזירה תשובה
   קצרה. (TODO)
3. **LLM NLU** — להחליף את ההיוריסטיקות ב-tool-calling מול מודל, עם
   `list_operators()` כ-tool לפתרון שמות. (TODO)
4. **ערוצים** — מתאמים ל-Telegram / Slack / WhatsApp סביב ה-Executor. (TODO)
5. **Guardrails** — תמיד להחזיר מגבלות והנחות (כפי ש-`generate_report` עושה),
   ולסרב לשאלות מחוץ לתחום הנתונים.

## דוגמה (היום)

```python
from stride_analysis.bot import parse_question
from stride_analysis.data.stride_client import StrideClient

client = StrideClient()
known = [(o.operator_ref, o.name) for o in client.list_operators()]
q = parse_question("מה הביצועים של דן בשבוע שעבר?", known_operators=known)
# ParsedQuery(intent='execution', operator_ref=5, date_from=..., date_to=...)
assert q.is_actionable()
```

## עקרונות

- אף שם מפעיל לא ממופה ל-`operator_ref` בקוד — תמיד פותרים מול ה-API.
- כל תשובה כוללת מקור, חלון זמן, ומגבלות.
- אם חסר מידע (`ParsedQuery.unresolved`) — שואלים שאלת המשך, לא מנחשים.
