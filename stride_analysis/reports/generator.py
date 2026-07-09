"""Generate Hebrew, RTL Markdown performance reports.

The public entry point is :func:`generate_report`, which takes already-computed
analysis frames (or computes them from a client) and renders an executive-grade
Markdown document: summary, per-operator breakdown, optional daily detail and
period comparison, plus an explicit limitations + provenance section (per the
project's AGENTS.md reporting standard).

Rendering is dependency-light: tables are emitted as GitHub-flavored Markdown by
a tiny built-in formatter, so no ``tabulate``/``jinja2`` is required. If
``jinja2`` *is* installed, the template in ``templates/`` can be used instead via
:func:`render_with_template`.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from stride_analysis import config
from stride_analysis.analysis.execution import execution_totals

_logger = config.get_logger("stride_analysis.report")

# Friendly Hebrew column headers for known metric columns.
_HE_HEADERS = {
    "operator_ref": "קוד מפעיל",
    "operator_name": "מפעיל",
    "agency_name": "מפעיל",
    "service_date": "תאריך",
    "route_short_name": "קו",
    "line_ref": "line_ref",
    "planned": "מתוכננות",
    "executed": "בוצעו",
    "missing": "לא בוצעו",
    "missing_pct": "% אי-ביצוע",
    "late_pct": "% איחור",
    "avg_delay_min": "עיכוב ממוצע (דק')",
    "nis_per_missing": "₪ לנסיעה חסרה",
    "penalty_nis": "קנס (₪)",
}


def _nfmt(value: Any) -> str:
    """Format numbers with thousands separators; pass through everything else."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return "—"
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    if isinstance(value, (int,)):
        return f"{value:,}"
    if isinstance(value, float):
        return f"{value:,.2f}"
    return str(value)


def df_to_markdown(df: pd.DataFrame, *, headers: dict[str, str] | None = None) -> str:
    """Render a DataFrame as a GitHub-flavored Markdown table (no deps)."""
    if df is None or df.empty:
        return "_אין נתונים._"
    headers = {**_HE_HEADERS, **(headers or {})}
    cols = list(df.columns)
    head = [headers.get(c, c) for c in cols]
    lines = ["| " + " | ".join(head) + " |", "| " + " | ".join(["---"] * len(cols)) + " |"]
    for _, row in df.iterrows():
        cells = [_nfmt(row[c]) for c in cols]
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def generate_report(
    execution: pd.DataFrame,
    *,
    date_from: str,
    date_to: str,
    title: str = "כלל המפעילים",
    daily: pd.DataFrame | None = None,
    comparison: pd.DataFrame | None = None,
    operators: str | None = None,
    limitations: str | None = None,
    request_count: int | None = None,
    lang: str = "he",
    out_path: Path | str | None = None,
) -> str:
    """Render a Markdown report from analysis frames.

    Args:
        execution: Per-operator execution summary (output of ``calc_execution``,
            ideally already passed through ``calc_penalties``). Must contain
            ``planned``/``executed``/``missing``/``missing_pct``.
        date_from / date_to: Reporting window (``YYYY-MM-DD``).
        title: Report subtitle (e.g. an operator name or "כלל המפעילים").
        daily: Optional per-day breakdown to include as a detail table.
        comparison: Optional output of ``compare_periods`` for a prior period.
        operators: Optional human list of operators covered.
        limitations: Override the default limitations paragraph.
        request_count: Number of API calls (for the provenance section).
        lang: Currently only ``"he"`` is implemented.
        out_path: If given, the Markdown is also written to this path.

    Returns:
        The Markdown document as a string.
    """
    if lang != "he":  # pragma: no cover - only Hebrew implemented today
        _logger.warning("lang=%r not implemented; defaulting to Hebrew", lang)

    by_operator = _per_operator_view(execution)
    totals = execution_totals(execution)
    totals["penalty_nis"] = (
        int(execution["penalty_nis"].sum()) if "penalty_nis" in execution.columns else None
    )

    headline = _headline(by_operator, totals)
    limitations = limitations or _default_limitations()
    generated_at = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M")

    parts: list[str] = []
    parts.append('<div dir="rtl">\n')
    parts.append(f"# דוח ביצועי תחבורה ציבורית — {title}\n")
    parts.append(f"**תקופה:** {date_from} עד {date_to}  ")
    parts.append(f"\n**נוצר:** {generated_at}  ")
    if operators:
        parts.append(f"\n**מפעילים:** {operators}  ")
    parts.append("\n\n---\n")

    parts.append("## תקציר מנהלים\n")
    parts.append(f"- **נסיעות מתוכננות:** {_nfmt(totals['planned'])}")
    parts.append(f"- **נסיעות שבוצעו:** {_nfmt(totals['executed'])}")
    parts.append(
        f"- **נסיעות שלא בוצעו:** {_nfmt(totals['missing'])} ({totals['missing_pct']}%)"
    )
    if totals.get("penalty_nis") is not None:
        parts.append(f"- **אומדן קנס אי-ביצוע:** ₪{_nfmt(totals['penalty_nis'])}")
    parts.append(f"\n{headline}\n")

    parts.append("\n---\n## פירוט לפי מפעיל\n")
    parts.append(df_to_markdown(by_operator))

    if daily is not None and not daily.empty:
        parts.append("\n\n---\n## פירוט יומי\n")
        parts.append(df_to_markdown(daily))

    if comparison is not None and not comparison.empty:
        parts.append("\n\n---\n## השוואה לתקופה קודמת\n")
        parts.append(df_to_markdown(comparison))

    parts.append("\n\n---\n## מגבלות והנחות\n")
    parts.append(limitations)

    parts.append("\n\n---\n## שחזור (Provenance)\n")
    parts.append(f"- **מקור נתונים:** Open Bus Stride API (`{config.BASE_URL}`)")
    parts.append("- **endpoints:** `/gtfs_routes/list`, `/rides_execution/list`")
    parts.append('- **טבלת קנסות:** נספח כ"ו — `penalties_workbook.xlsx`')
    if request_count is not None:
        parts.append(f"- **קריאות API:** {request_count}")
    parts.append("\n</div>\n")

    markdown = "\n".join(parts)
    if out_path is not None:
        path = Path(out_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(markdown, encoding="utf-8")
        _logger.info("wrote report → %s", path)
    return markdown


def render_with_template(context: dict[str, Any], *, template_name: str = "operator_report_he.md.j2") -> str:
    """Render a report via the Jinja2 template (requires ``jinja2``)."""
    try:
        from jinja2 import Environment, FileSystemLoader, select_autoescape
    except ImportError as exc:  # pragma: no cover
        raise ImportError("render_with_template requires the optional 'jinja2' package") from exc

    template_dir = Path(__file__).resolve().parent / "templates"
    env = Environment(
        loader=FileSystemLoader(str(template_dir)),
        autoescape=select_autoescape(enabled_extensions=("html",)),
    )
    env.filters["nfmt"] = _nfmt
    return env.get_template(template_name).render(**context)


# --------------------------------------------------------------------------- #
# Internals
# --------------------------------------------------------------------------- #
def _per_operator_view(execution: pd.DataFrame) -> pd.DataFrame:
    """Collapse an execution frame to one row per operator, ranked by penalty."""
    keys = [c for c in ("operator_ref", "operator_name") if c in execution.columns]
    if not keys:
        return execution.copy()
    agg_spec: dict[str, tuple[str, str]] = {
        "planned": ("planned", "sum"),
        "executed": ("executed", "sum"),
        "missing": ("missing", "sum"),
    }
    if "penalty_nis" in execution.columns:
        agg_spec["penalty_nis"] = ("penalty_nis", "sum")
    grouped = execution.groupby(keys, dropna=False).agg(**agg_spec).reset_index()
    grouped["missing_pct"] = (
        100.0 * grouped["missing"] / grouped["planned"].replace(0, pd.NA)
    ).fillna(0.0).round(2)
    sort_col = "penalty_nis" if "penalty_nis" in grouped.columns else "missing"
    cols = [*keys, "planned", "executed", "missing", "missing_pct"]
    if "penalty_nis" in grouped.columns:
        cols.append("penalty_nis")
    return grouped[cols].sort_values(sort_col, ascending=False).reset_index(drop=True)


def _headline(by_operator: pd.DataFrame, totals: dict[str, Any]) -> str:
    if by_operator.empty:
        return ""
    name_col = "operator_name" if "operator_name" in by_operator.columns else by_operator.columns[0]
    worst = by_operator.iloc[0]
    worst_name = worst.get(name_col, worst.iloc[0])
    bits = [
        f"בתקופה הנבדקת בוצעו {totals['missing_pct']}% פחות נסיעות מהמתוכנן.",
        f"המפעיל עם החשיפה הגבוהה ביותר לקנס הוא **{worst_name}** "
        f"({_nfmt(worst.get('missing'))} נסיעות שלא בוצעו, "
        f"{worst.get('missing_pct')}%).",
    ]
    return " ".join(bits)


def _default_limitations() -> str:
    return (
        "- נתוני אי-ביצוע מבוססים על התאמת נסיעות מתוכננות (GTFS) לנסיעות בפועל (SIRI) "
        "דרך `/rides_execution/list`. נסיעה ללא התאמה בזמן-אמת נספרת כ\"לא בוצעה\", "
        "אך ייתכנו פערי כיסוי ב-SIRI שמנפחים את אחוז אי-הביצוע.\n"
        "- **דייקנות (איחור/הקדמה) אינה נגזרת מ-`/rides_execution/list`** משום שזמן "
        "היציאה בפועל מוצמד לזמן המתוכנן. לחישוב דייקנות אמיתי יש להשתמש ב-`fetch_siri()`.\n"
        "- אומדן הקנס הוא הערכה לפי נספח כ\"ו (טבלה 1, אי-ביצוע מדורג) בלבד, "
        "ואינו כולל קנסות קבועים, בקרה מדגמית, או תוספות תלונות ציבור.\n"
        "- ימי שירות מקובצים לפי שעון ישראל (Asia/Jerusalem)."
    )
