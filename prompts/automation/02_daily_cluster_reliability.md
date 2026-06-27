<div dir="rtl" align="right">

# Automation: אמינות קו ברוטציה לפי אשכול

מטרה: להריץ בכל יום עבודה את אשכול מטרופולין המתאים לפי תאריך שירות בישראל, לשמור סיכומי אמינות, ולפתוח PR רק אם נוצרו פלטים חדשים.

## לוח זמנים

Cron מומלץ להרצה בימים שני עד שישי בבוקר. הסקריפט מנתח את תאריך השירות של אתמול, ולכן זה מכסה תאריכי שירות ראשון עד חמישי. תאריך שירות חמישי מייצר סיכום שבועי מתוך סיכומי ראשון עד רביעי:

```cron
0 7 * * 1-5
```

## פקודות

```bash
set -euo pipefail

read SERVICE_DATE BRANCH_DATE <<< "$(python - <<'PY'
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

today = datetime.now(ZoneInfo("Asia/Jerusalem")).date()
service_date = today - timedelta(days=1)
print(service_date.isoformat(), today.isoformat())
PY
)"

BRANCH="automation/cluster-reliability/${SERVICE_DATE}"

git fetch origin main
git checkout -B "$BRANCH" origin/main
python -m pip install -r requirements.txt

python -m src.cluster_reliability_runner \
  --manifest data/reference/metropoline_line_ids.csv \
  --service-date "$SERVICE_DATE" \
  --output-dir data/output/reliability

python -m pytest -q tests/test_cluster_reliability_runner.py tests/test_line_reliability_analyzer.py

git add -f data/output/reliability
if git diff --cached --quiet; then
  echo "No cluster reliability changes; skipping PR."
  exit 0
fi

git commit -m "data: update cluster reliability for ${SERVICE_DATE}"
git push -u origin "$BRANCH"

gh pr create \
  --base main \
  --head "$BRANCH" \
  --title "Cluster reliability: ${SERVICE_DATE}" \
  --body "Automated Metropoline cluster reliability run for service date ${SERVICE_DATE}."
```

</div>
