<div dir="rtl" align="right">

# Automation: ניטור קטלוג API

מטרה: לבנות מחדש את קטלוג ה־OpenAPI של Open Bus Stride, ולעדכן PR רק אם אחד מקובצי הקטלוג השתנה.

## לוח זמנים

Cron מומלץ:

```cron
0 5 * * *
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

BRANCH="automation/endpoint-catalog/${BRANCH_DATE}"

git fetch origin main
git checkout -B "$BRANCH" origin/main
python -m pip install -r requirements.txt

python -m src.endpoint_catalog
python -m pytest -q tests/test_endpoint_catalog.py

git add docs/endpoint_catalog.csv docs/endpoint_catalog.md docs/endpoint_catalog.html
if git diff --cached --quiet; then
  echo "Endpoint catalog unchanged; skipping PR."
  exit 0
fi

git commit -m "docs: update endpoint catalog ${BRANCH_DATE}"
git push -u origin "$BRANCH"

gh pr create \
  --base main \
  --head "$BRANCH" \
  --title "Endpoint catalog update: ${BRANCH_DATE}" \
  --body "Automated OpenAPI endpoint catalog refresh for ${BRANCH_DATE}."
```

</div>
