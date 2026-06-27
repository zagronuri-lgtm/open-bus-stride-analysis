"""Load the 2026 exclusions calendar and classify service dates.

Source of truth: ``data/reference/exclusions_2026/`` (converted from the
``לוח_החרגות_2026`` Google Sheet). Each calendar row carries a *treatment*:

* ``drop``    — distorted, non-recurring day. Exclude entirely from analysis.
* ``segment`` — real but different recurring pattern. Analyse separately, never drop.
* ``keep``    — special in name only; no material effect. Keep as a normal day.

A date with no matching entry is ``normal``.

The runner uses national-scope classification (``היקף == 'ארצי'``) to decide
whether to skip a service date (``drop``) or tag it (``segment``). Branch-level
nuance (Elad on Jewish holidays, Muslim branches on Eid) lives in the regional
sheets and is consumed by the reporting agent, not this national gate.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

DEFAULT_EXCLUSIONS_DIR = Path("data/reference/exclusions_2026")
NATIONAL_SCOPE = "ארצי"
_PRECEDENCE = {"normal": 0, "keep": 1, "segment": 2, "drop": 3}

# Clusters whose planned service is materially shaped by the Muslim calendar
# (Eid/Ramadan ~50% planned reduction). Grounded in the branches/regional sheets:
# הנגב carries Rahat/Negev sector lines; שרון includes שרון בינעירוני 'מגזר'
# (Triangle: Tira/Taibe/...). Elad (Haredi) and Netanya Haredi lines are handled
# by national Jewish-holiday drops, not here.
MUSLIM_AFFECTED_CLUSTERS = frozenset({"הנגב", "שרון"})


class ExclusionsCalendarError(RuntimeError):
    """Raised when the exclusions calendar cannot be loaded."""


@dataclass(frozen=True)
class _Entry:
    start: date
    end: date
    treatment: str
    name: str
    scope: str
    hours_window: str
    source: str


@dataclass(frozen=True)
class DateClassification:
    """Treatment decision for a single service date."""

    treatment: str = "normal"  # normal | drop | segment | keep
    name: str | None = None
    source: str | None = None  # fixed | muslim | periods | oneoff | none
    scope: str | None = None
    hours_window: str | None = None

    @property
    def is_excluded(self) -> bool:
        """True when the date should be dropped from analysis entirely."""
        return self.treatment == "drop"

    @property
    def is_normal(self) -> bool:
        """True when the date can join normal averages (normal or keep)."""
        return self.treatment in ("normal", "keep")


def _parse_date(value: str | None) -> date | None:
    text = (value or "").strip()
    if not text:
        return None
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        # Placeholder rows such as 2026-00-00 (template events) are skipped.
        return None


def _load_sheet(
    path: Path,
    *,
    source: str,
    col_start: str,
    col_end: str | None,
    col_treat: str,
    col_name: str,
    col_scope: str,
    col_hours: str | None,
) -> list[_Entry]:
    if not path.exists():
        return []
    entries: list[_Entry] = []
    with open(path, newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            start = _parse_date(row.get(col_start))
            if start is None:
                continue
            end = _parse_date(row.get(col_end)) if col_end else None
            treatment = (row.get(col_treat) or "").strip()
            if treatment not in _PRECEDENCE or treatment == "normal":
                continue
            entries.append(
                _Entry(
                    start=start,
                    end=end or start,
                    treatment=treatment,
                    name=(row.get(col_name) or "").strip(),
                    scope=(row.get(col_scope) or "").strip(),
                    hours_window=((row.get(col_hours) or "").strip() if col_hours else ""),
                    source=source,
                )
            )
    return entries


def load_calendar(directory: str | Path = DEFAULT_EXCLUSIONS_DIR) -> list[_Entry]:
    """Load all dated calendar sheets into a flat list of entries."""
    directory = Path(directory)
    entries: list[_Entry] = []
    entries += _load_sheet(
        directory / "01_holidays_fixed_2026.csv",
        source="fixed",
        col_start="תאריך_התחלה",
        col_end="תאריך_סיום",
        col_treat="טיפול",
        col_name="שם",
        col_scope="היקף",
        col_hours="חלון_שעות",
    )
    entries += _load_sheet(
        directory / "02_holidays_muslim_2026.csv",
        source="muslim",
        col_start="תאריך_התחלה",
        col_end="תאריך_סיום",
        col_treat="טיפול",
        col_name="שם",
        col_scope="היקף",
        col_hours=None,
    )
    entries += _load_sheet(
        directory / "03_periods_vacations_2026.csv",
        source="periods",
        col_start="תאריך_התחלה",
        col_end="תאריך_סיום",
        col_treat="טיפול",
        col_name="שם",
        col_scope="היקף",
        col_hours=None,
    )
    entries += _load_sheet(
        directory / "04_oneoff_events.csv",
        source="oneoff",
        col_start="תאריך",
        col_end=None,
        col_treat="טיפול",
        col_name="שם",
        col_scope="אשכול/סניף_מושפע",
        col_hours="חלון_שעות",
    )
    if not entries:
        raise ExclusionsCalendarError(
            f"No exclusion entries loaded from {directory} (missing or empty calendar files)."
        )
    return entries


def classify_date(
    service_date: date,
    entries: list[_Entry] | None = None,
    *,
    directory: str | Path = DEFAULT_EXCLUSIONS_DIR,
    national_only: bool = True,
) -> DateClassification:
    """Classify a service date; on ties the most disruptive treatment wins.

    ``national_only`` restricts matching to nationwide rows (``היקף == 'ארצי'``),
    which is the correct gate for cluster-level runs that span many branches.
    """
    if entries is None:
        entries = load_calendar(directory)

    matches = [
        entry
        for entry in entries
        if entry.start <= service_date <= entry.end
        and (not national_only or entry.scope == NATIONAL_SCOPE)
    ]
    if not matches:
        return DateClassification(treatment="normal", source="none")

    best = max(matches, key=lambda entry: _PRECEDENCE[entry.treatment])
    return DateClassification(
        treatment=best.treatment,
        name=best.name,
        source=best.source,
        scope=best.scope,
        hours_window=best.hours_window or None,
    )


def classify_for_cluster(
    service_date: date,
    cluster: str | None,
    entries: list[_Entry] | None = None,
    *,
    directory: str | Path = DEFAULT_EXCLUSIONS_DIR,
) -> DateClassification:
    """Cluster-aware classification: national gate, escalated by sector calendars.

    National ``drop`` days close everything and win outright. Otherwise, for
    Muslim-sector clusters the Muslim calendar (Eid/Ramadan ``segment``) is also
    considered, and the most disruptive treatment wins. Returns the national
    result for clusters with no sector sensitivity.
    """
    if entries is None:
        entries = load_calendar(directory)

    national = classify_date(service_date, entries, national_only=True)
    if national.treatment == "drop":
        return national

    candidates = [national]
    if cluster in MUSLIM_AFFECTED_CLUSTERS:
        muslim_entries = [entry for entry in entries if entry.source == "muslim"]
        candidates.append(classify_date(service_date, muslim_entries, national_only=False))

    return max(candidates, key=lambda result: _PRECEDENCE[result.treatment])


def load_line_uniqueness_treatments(
    directory: str | Path = DEFAULT_EXCLUSIONS_DIR,
) -> dict[str, dict[str, str]]:
    """Map line-uniqueness category -> recommended treatment (sheet 06).

    Exposed for the reporting layer and for future manifest enrichment; the
    national/cluster gates above do not consume it because the line manifest
    currently lacks a line-uniqueness column.
    """
    path = Path(directory) / "06_line_uniqueness_treatment.csv"
    out: dict[str, dict[str, str]] = {}
    if not path.exists():
        return out
    with open(path, newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            key = (row.get("ייחודיות_קו") or "").strip()
            if not key:
                continue
            out[key] = {
                "treatment": (row.get("טיפול_מומלץ") or "").strip(),
                "sensitive_to": (row.get("רגיש_ל") or "").strip(),
                "note": (row.get("הערה") or "").strip(),
            }
    return out
