"""Endpoint catalog builder for Open Bus Stride API.

Fetches the OpenAPI spec from the live API and produces three artifacts:
  - docs/endpoint_catalog.csv
  - docs/endpoint_catalog.md  (Hebrew RTL markdown)
  - docs/endpoint_catalog.html (Hebrew RTL standalone page)

Usage:
    python -m src.endpoint_catalog
"""
from __future__ import annotations

import csv
import io
from pathlib import Path
from typing import Any

import requests

OPENAPI_URL = "https://open-bus-stride-api.hasadna.org.il/openapi.json"

# Human-readable Hebrew group names
_GROUP_HE: dict[str, str] = {
    "user cases": "מקרי שימוש",
    "siri": "נתוני SIRI (ביצוע)",
    "gtfs": "נתוני GTFS (תכנון)",
    "aggregations": "אגרגציות",
}

# Short analyst use-case notes per path prefix
_USE_CASES: dict[str, str] = {
    "/route_timetable": "לוח זמנים מתוכנן לתחנה/קו לתאריך נתון",
    "/stop_arrivals": "זמני הגעה בפועל לתחנה (SIRI)",
    "/rides_execution": "השוואת נסיעות מתוכננות מול ביצוע בפועל",
    "/siri_routes": "מסלולי SIRI (זיהוי קו בזמן אמת)",
    "/siri_rides": "נסיעות SIRI עם נתוני ביצוע",
    "/siri_stops": "תחנות SIRI",
    "/siri_ride_stops": "עצירות SIRI לנסיעה ספציפית",
    "/siri_vehicle_locations": "מיקומי רכב מ-SIRI",
    "/siri_snapshots": "סטטוס תמונות ETL",
    "/siri_velocity_aggregation": "מהירות ממוצעת לרכב/קו",
    "/gtfs_routes": "מסלולים מתוכננים (GTFS)",
    "/gtfs_stops": "תחנות מתוכננות (GTFS)",
    "/gtfs_rides": "נסיעות מתוכננות (GTFS)",
    "/gtfs_agencies": "רשימת מפעילים",
    "/gtfs_ride_stops": "עצירות נסיעה מתוכננת",
    "/gtfs_rides_agg": "סטטיסטיקות מאוחדות לנסיעות GTFS",
}


def _use_case_for(path: str) -> str:
    for prefix, note in _USE_CASES.items():
        if path.startswith(prefix):
            return note
    return ""


def _required_params(params: list[dict[str, Any]]) -> str:
    return ", ".join(p["name"] for p in params if p.get("required"))


def _optional_params(params: list[dict[str, Any]]) -> str:
    return ", ".join(p["name"] for p in params if not p.get("required"))


def fetch_catalog(
    openapi_url: str = OPENAPI_URL,
    timeout: int = 30,
) -> list[dict[str, str]]:
    """Fetch OpenAPI spec and return a flat list of endpoint records."""
    resp = requests.get(openapi_url, timeout=timeout)
    resp.raise_for_status()
    spec: dict[str, Any] = resp.json()

    rows: list[dict[str, str]] = []
    for path, path_item in spec.get("paths", {}).items():
        for method, operation in path_item.items():
            if method.upper() not in {"GET", "POST", "PUT", "DELETE", "PATCH"}:
                continue
            tags = operation.get("tags", ["other"])
            group_en = tags[0] if tags else "other"
            group_he = _GROUP_HE.get(group_en, group_en)
            params: list[dict[str, Any]] = operation.get("parameters", [])
            rows.append(
                {
                    "group_en": group_en,
                    "group_he": group_he,
                    "method": method.upper(),
                    "path": path,
                    "summary": operation.get("summary", ""),
                    "description": (operation.get("description") or "").split("\n")[0][:120],
                    "required_params": _required_params(params),
                    "optional_params": _optional_params(params),
                    "analyst_use_case": _use_case_for(path),
                }
            )
    return rows


def save_csv(rows: list[dict[str, str]], path: Path) -> None:
    """Write catalog rows to CSV."""
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def save_markdown(rows: list[dict[str, str]], path: Path) -> None:
    """Write RTL Hebrew Markdown catalog."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = [
        "<!-- RTL -->",
        "# קטלוג Endpoints — Open Bus Stride API",
        "",
        "> נוצר אוטומטית מ-OpenAPI spec. לכל endpoint מפורטים: שיטה, נתיב, תיאור, פרמטרים, ומקרה שימוש.",
        "",
    ]
    current_group = ""
    for row in sorted(rows, key=lambda r: (r["group_en"], r["path"])):
        if row["group_he"] != current_group:
            current_group = row["group_he"]
            lines += ["", f"## {current_group}", ""]
        lines.append(f"### `{row['method']} {row['path']}`")
        if row["summary"]:
            lines.append(f"**תיאור קצר:** {row['summary']}")
        if row["description"]:
            lines.append(f"> {row['description']}")
        if row["required_params"]:
            lines.append(f"- **פרמטרים חובה:** `{row['required_params']}`")
        if row["optional_params"]:
            lines.append(f"- **פרמטרים אופציונליים:** `{row['optional_params']}`")
        if row["analyst_use_case"]:
            lines.append(f"- **מקרה שימוש לאנליסט:** {row['analyst_use_case']}")
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def save_html(rows: list[dict[str, str]], path: Path) -> None:
    """Write standalone RTL Hebrew HTML catalog."""
    path.parent.mkdir(parents=True, exist_ok=True)

    # Build table rows
    tbody_rows = ""
    for row in sorted(rows, key=lambda r: (r["group_en"], r["path"])):
        badge_class = row["group_en"].replace(" ", "-")
        tbody_rows += (
            f"<tr>"
            f"<td><span class='badge badge-{badge_class}'>{row['group_he']}</span></td>"
            f"<td><code>{row['method']}</code></td>"
            f"<td><code>{row['path']}</code></td>"
            f"<td>{row['summary']}</td>"
            f"<td>{row['required_params']}</td>"
            f"<td>{row['optional_params']}</td>"
            f"<td>{row['analyst_use_case']}</td>"
            f"</tr>\n"
        )

    html = f"""<!doctype html>
<html lang="he" dir="rtl">
<head>
  <meta charset="utf-8" />
  <title>קטלוג Endpoints — Open Bus Stride API</title>
  <style>
    body {{ font-family: Arial, sans-serif; direction: rtl; margin: 24px; background: #f8f9fa; }}
    h1 {{ color: #1a1a2e; }}
    p.subtitle {{ color: #555; margin-top: -12px; }}
    table {{ border-collapse: collapse; width: 100%; background: #fff; border-radius: 8px;
             box-shadow: 0 1px 4px rgba(0,0,0,.1); }}
    th {{ background: #1a1a2e; color: #fff; padding: 10px 8px; text-align: right; font-size: .85rem; }}
    td {{ padding: 8px; border-bottom: 1px solid #e0e0e0; font-size: .82rem; vertical-align: top; }}
    tr:last-child td {{ border-bottom: none; }}
    tr:hover td {{ background: #f0f4ff; }}
    code {{ background: #eef; padding: 1px 4px; border-radius: 3px; font-size: .8rem; }}
    .badge {{ display: inline-block; padding: 2px 8px; border-radius: 12px;
              font-size: .75rem; font-weight: bold; color: #fff; }}
    .badge-user-cases  {{ background: #2e86de; }}
    .badge-siri        {{ background: #e84393; }}
    .badge-gtfs        {{ background: #27ae60; }}
    .badge-aggregations{{ background: #8e44ad; }}
    .badge-other       {{ background: #7f8c8d; }}
  </style>
</head>
<body>
  <h1>קטלוג Endpoints — Open Bus Stride API</h1>
  <p class="subtitle">נוצר אוטומטית מ-OpenAPI spec | סך הכל {len(rows)} endpoints</p>
  <table>
    <thead>
      <tr>
        <th>קבוצה</th>
        <th>שיטה</th>
        <th>נתיב</th>
        <th>תיאור</th>
        <th>פרמטרים חובה</th>
        <th>פרמטרים אופציונליים</th>
        <th>מקרה שימוש</th>
      </tr>
    </thead>
    <tbody>
{tbody_rows}    </tbody>
  </table>
</body>
</html>
"""
    path.write_text(html, encoding="utf-8")


def build_catalog(
    openapi_url: str = OPENAPI_URL,
    output_dir: str | Path = "docs",
) -> list[dict[str, str]]:
    """Full pipeline: fetch -> save CSV + Markdown + HTML."""
    rows = fetch_catalog(openapi_url)
    out = Path(output_dir)
    save_csv(rows, out / "endpoint_catalog.csv")
    save_markdown(rows, out / "endpoint_catalog.md")
    save_html(rows, out / "endpoint_catalog.html")
    return rows


if __name__ == "__main__":  # pragma: no cover
    records = build_catalog()
    print(f"Catalog built: {len(records)} endpoints written to docs/")
