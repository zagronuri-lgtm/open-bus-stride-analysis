# Open Bus Stride — Codex Integration Pack

חבילת עבודה לחיבור חוברות Open Bus Stride API ל־OpenAI Codex.

## מה יש כאן

- `AGENTS.md` — הוראות פרויקט עבור Codex.
- `skills/open-bus-transit-analysis/SKILL.md` — Skill ייעודי לניתוח תחבורה ציבורית בישראל.
- `docs/` — שתי חוברות העבודה שהועלו: HTML + PDF.
- `src/open_bus_stride_client.py` — לקוח Python בסיסי ובטוח ל־Open Bus Stride API.
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

## עקרון עבודה

כל ניתוח חייב להתחיל בזיהוי השאלה התחבורתית, בחירת מקור הנתונים, בדיקת איכות, ורק אז חישוב KPI.
אין לנחש נתונים. אם חסר נתון — לציין זאת במפורש.
