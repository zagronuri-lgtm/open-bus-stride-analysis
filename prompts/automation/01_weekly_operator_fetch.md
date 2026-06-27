<div dir="rtl" align="right">

# Automation: משיכה שבועית מפעילים

מטרה: להריץ משיכת ביצוע שבועית לכל המפעילים, לשמור CSV חדש, ולפתוח PR רק אם נוצר שינוי.

## לוח זמנים

Cron מומלץ:

```cron
0 6 * * 0
```

## פקודות

```bash
set -euo pipefail

read DATE_FROM DATE_TO BRANCH_DATE <<< "$(python - <<'PY'
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

today = datetime.now(ZoneInfo("Asia/Jerusalem")).date()
date_to = today - timedelta(days=1)
date_from = date_to - timedelta(days=6)
print(date_from.isoformat(), date_to.isoformat(), today.isoformat())
PY
)"

BRANCH="automation/operator-weekly-fetch/${BRANCH_DATE}"
OUT="data/raw/operator_weekly_${DATE_FROM}_${DATE_TO}.csv"

git fetch origin main
git checkout -B "$BRANCH" origin/main
python -m pip install -r requirements.txt

python -m src.operator_weekly_fetch \
  --date-from "$DATE_FROM" \
  --date-to "$DATE_TO" \
  --out "$OUT"

python -m pytest -q tests/test_open_bus_stride_client.py

git add "$OUT"
if git diff --cached --quiet; then
  echo "No weekly operator fetch changes; skipping PR."
  exit 0
fi

git commit -m "data: update weekly operator fetch ${DATE_FROM} to ${DATE_TO}"
git push -u origin "$BRANCH"

gh pr create \
  --base main \
  --head "$BRANCH" \
  --title "Weekly operator fetch: ${DATE_FROM} to ${DATE_TO}" \
  --body "Automated weekly Open Bus Stride operator fetch for ${DATE_FROM} to ${DATE_TO}."
```

</div>
