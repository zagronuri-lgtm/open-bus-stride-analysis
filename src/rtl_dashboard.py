"""Standalone RTL HTML dashboard for line reliability analysis.

Reads a reliability CSV produced by analyze_line_reliability() and renders
a self-contained Hebrew RTL HTML page with:
  - KPI summary cards
  - Planned vs actual rides bar chart (Chart.js via CDN)
  - Full rides-execution detail table

Usage:
    python -m src.rtl_dashboard --csv outputs/line_reliability_*.csv
    python -m src.rtl_dashboard --csv path/to/file.csv --out path/to/output.html
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import pandas as pd

# Column presence is optional; dashboard degrades gracefully
_ACTUAL_COL = "actual_start_time"
_PLANNED_COL = "planned_start_time"
_GTFS_ID_COL = "gtfs_ride_id"


def _kpi_from_df(df: pd.DataFrame) -> dict[str, Any]:
    """Derive KPI dict from a rides-execution DataFrame."""
    planned = len(df)
    actual_mask = df.get(_ACTUAL_COL, pd.Series([None] * len(df))).notna()
    matched_mask = df.get(_GTFS_ID_COL, pd.Series([None] * len(df))).notna()
    actual = int(actual_mask.sum())
    matched = int((actual_mask & matched_mask).sum())
    missing = planned - actual
    execution_rate = round(actual / planned * 100, 1) if planned else 0.0
    match_rate = round(matched / planned * 100, 1) if planned else 0.0
    return {
        "planned_rides": planned,
        "actual_rides": actual,
        "matched_rides": matched,
        "missing_rides": missing,
        "execution_rate_pct": execution_rate,
        "match_rate_pct": match_rate,
    }


def _hour_series(df: pd.DataFrame) -> tuple[list[str], list[int], list[int]]:
    """Return (hours, planned_counts, actual_counts) for the chart."""
    if _PLANNED_COL not in df.columns:
        return [], [], []
    df2 = df.copy()
    df2["_hour"] = pd.to_datetime(df2[_PLANNED_COL], errors="coerce").dt.hour
    planned_by_hour = df2.groupby("_hour").size()
    if _ACTUAL_COL in df2.columns:
        actual_by_hour = df2[df2[_ACTUAL_COL].notna()].groupby("_hour").size()
    else:
        actual_by_hour = pd.Series(dtype=int)
    all_hours = sorted(set(planned_by_hour.index) | set(actual_by_hour.index))
    labels = [f"{h:02d}:00" for h in all_hours]
    planned_vals = [int(planned_by_hour.get(h, 0)) for h in all_hours]
    actual_vals = [int(actual_by_hour.get(h, 0)) for h in all_hours]
    return labels, planned_vals, actual_vals


def _kpi_cards_html(kpi: dict[str, Any]) -> str:
    cards = [
        ("נסיעות מתוכננות", kpi["planned_rides"], "#2e86de"),
        ("נסיעות בוצעו", kpi["actual_rides"], "#27ae60"),
        ("נסיעות תואמות GTFS", kpi["matched_rides"], "#8e44ad"),
        ("נסיעות חסרות", kpi["missing_rides"], "#e74c3c"),
        ("אחוז ביצוע", f"{kpi['execution_rate_pct']}%", "#f39c12"),
        ("אחוז התאמה", f"{kpi['match_rate_pct']}%", "#16a085"),
    ]
    html = '<div class="kpi-grid">'
    for label, value, color in cards:
        html += (
            f'<div class="kpi-card" style="border-top: 4px solid {color}">'
            f'<div class="kpi-value">{value}</div>'
            f'<div class="kpi-label">{label}</div>'
            f"</div>"
        )
    html += "</div>"
    return html


def build_dashboard(
    csv_path: str | Path,
    output_path: str | Path | None = None,
    title: str = "דוח אמינות קו",
) -> Path:
    """Build RTL HTML dashboard from a reliability CSV and write to file.

    Returns the path of the written HTML file.
    """
    csv_path = Path(csv_path)
    df = pd.read_csv(csv_path)

    if output_path is None:
        output_path = csv_path.with_suffix(".dashboard.html")
    output_path = Path(output_path)

    kpi = _kpi_from_df(df)
    labels, planned_vals, actual_vals = _hour_series(df)
    table_html = df.to_html(index=False, classes="data-table", border=0, na_rep="—")
    kpi_html = _kpi_cards_html(kpi)

    chart_data = json.dumps(
        {
            "labels": labels,
            "planned": planned_vals,
            "actual": actual_vals,
        }
    )

    html = f"""<!doctype html>
<html lang="he" dir="rtl">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{title}</title>
  <script src="https://cdn.jsdelivr.net/npm/chart.js@4/dist/chart.umd.min.js"></script>
  <style>
    *, *::before, *::after {{ box-sizing: border-box; }}
    body {{ font-family: Arial, sans-serif; direction: rtl; margin: 0; background: #f0f2f5; color: #222; }}
    header {{ background: #1a1a2e; color: #fff; padding: 18px 24px; }}
    header h1 {{ margin: 0; font-size: 1.4rem; }}
    header p {{ margin: 4px 0 0; font-size: .85rem; opacity: .75; }}
    main {{ padding: 24px; max-width: 1200px; margin: auto; }}
    section {{ background: #fff; border-radius: 10px; padding: 20px 24px;
               box-shadow: 0 1px 4px rgba(0,0,0,.08); margin-bottom: 24px; }}
    section h2 {{ margin-top: 0; font-size: 1.1rem; color: #1a1a2e; border-bottom: 2px solid #eee;
                  padding-bottom: 8px; margin-bottom: 16px; }}
    .kpi-grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(160px, 1fr)); gap: 16px; }}
    .kpi-card {{ background: #fafafa; border-radius: 8px; padding: 16px 12px; text-align: center; }}
    .kpi-value {{ font-size: 2rem; font-weight: bold; }}
    .kpi-label {{ font-size: .8rem; color: #666; margin-top: 4px; }}
    .chart-wrap {{ position: relative; height: 280px; }}
    .data-table {{ width: 100%; border-collapse: collapse; font-size: .82rem; }}
    .data-table th {{ background: #1a1a2e; color: #fff; padding: 8px; text-align: right; }}
    .data-table td {{ padding: 7px 8px; border-bottom: 1px solid #eee; }}
    .data-table tr:last-child td {{ border-bottom: none; }}
    .data-table tr:hover td {{ background: #f5f8ff; }}
    .data-table td:empty::after, .data-table td:has(.missing)  {{ color: #aaa; }}
  </style>
</head>
<body>
  <header>
    <h1>{title}</h1>
    <p>קובץ נתונים: {csv_path.name}</p>
  </header>
  <main>
    <section>
      <h2>KPI סיכום</h2>
      {kpi_html}
    </section>
    <section>
      <h2>תכנון מול ביצוע לפי שעה</h2>
      <div class="chart-wrap">
        <canvas id="hourChart"></canvas>
      </div>
    </section>
    <section>
      <h2>פירוט נסיעות</h2>
      {table_html}
    </section>
  </main>
  <script>
  (function() {{
    var d = {chart_data};
    if (!d.labels.length) return;
    new Chart(document.getElementById('hourChart'), {{
      type: 'bar',
      data: {{
        labels: d.labels,
        datasets: [
          {{ label: 'נסיעות מתוכננות', data: d.planned,
             backgroundColor: 'rgba(46,134,222,.55)', borderColor: '#2e86de', borderWidth: 1 }},
          {{ label: 'נסיעות בוצעו',       data: d.actual,
             backgroundColor: 'rgba(39,174, 96,.65)', borderColor: '#27ae60', borderWidth: 1 }}
        ]
      }},
      options: {{
        responsive: true, maintainAspectRatio: false,
        plugins: {{ legend: {{ position: 'bottom' }} }},
        scales: {{
          x: {{ title: {{ display: true, text: 'שעה' }} }},
          y: {{ beginAtZero: true, title: {{ display: true, text: 'מספר נסיעות' }} }}
        }}
      }}
    }});
  }})();
  </script>
</body>
</html>
"""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html, encoding="utf-8")
    return output_path


def _cli() -> None:  # pragma: no cover
    parser = argparse.ArgumentParser(description="Build RTL HTML dashboard from reliability CSV")
    parser.add_argument("--csv", required=True, help="Path to reliability CSV")
    parser.add_argument("--out", default=None, help="Output HTML path (default: <csv>.dashboard.html)")
    parser.add_argument("--title", default="דוח אמינות קו", help="Dashboard title")
    args = parser.parse_args()
    out = build_dashboard(args.csv, args.out, args.title)
    print(f"Dashboard written to: {out}")


if __name__ == "__main__":  # pragma: no cover
    _cli()
