<div dir="rtl" align="right">

# Automation: נסועה רבעונית

מטרה: לבדוק האם data.gov.il פרסם משאב חדש או עודכן עבור נסועה בקווי אוטובוס, למשוך אותו, ולפתוח PR רק אם יש שינוי אמיתי בקובצי ה־CSV או במטא־דאטה הקטן.

## לוח זמנים

Cron מומלץ:

```cron
0 8 1 */3 *
```

## פקודות

```bash
set -euo pipefail

BRANCH_DATE="$(python - <<'PY'
from datetime import datetime
from zoneinfo import ZoneInfo

print(datetime.now(ZoneInfo("Asia/Jerusalem")).date().isoformat())
PY
)"

BRANCH="automation/quarterly-ridership/${BRANCH_DATE}"

git fetch origin main
git checkout -B "$BRANCH" origin/main
python -m pip install -r requirements.txt

set +e
python -m src.ridership_fetch --check-only
CHECK_STATUS=$?
set -e

if [ "$CHECK_STATUS" -eq 0 ]; then
  echo "Ridership resource unchanged; skipping PR."
  exit 0
fi

if [ "$CHECK_STATUS" -ne 1 ]; then
  echo "Ridership check failed with status ${CHECK_STATUS}."
  exit "$CHECK_STATUS"
fi

python -m src.ridership_fetch
python -m pytest -q tests/test_ridership_fetch.py

git add data/raw/ridership_*.csv
git add -f data/cache/ridership_meta.json

if git diff --cached --quiet; then
  echo "Ridership fetch produced no tracked changes; skipping PR."
  exit 0
fi

LATEST="$(ls -t data/raw/ridership_*.csv | head -1)"
PERIOD="$(basename "$LATEST" .csv | sed 's/^ridership_//')"

git commit -m "data: update ridership ${PERIOD}"
git push -u origin "$BRANCH"

gh pr create \
  --base main \
  --head "$BRANCH" \
  --title "Ridership update: ${PERIOD}" \
  --body "Automated data.gov.il ridership update for ${PERIOD}."
```

</div>
