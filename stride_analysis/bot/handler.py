"""Natural-language → query parameters (early heuristic prototype).

This is a deliberately small, dependency-free first pass: it maps a free-text
Hebrew/English question onto a :class:`ParsedQuery` (operator + date window +
intent) that the analysis pipeline can execute. It resolves operator names to
``operator_ref`` *dynamically* via the live operator list — no name→ref table is
hard-coded.

The eventual bot will likely replace these heuristics with an LLM tool-call
interface, but :class:`ParsedQuery` is the stable contract either way.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Iterable

from stride_analysis import config

_logger = config.get_logger("stride_analysis.bot")


@dataclass
class ParsedQuery:
    """Structured query extracted from a natural-language question."""

    intent: str = "execution"  # execution | punctuality | penalties | compare
    operator_ref: int | None = None
    operator_name: str | None = None
    route_short_name: str | None = None
    date_from: str | None = None
    date_to: str | None = None
    all_operators: bool = False
    unresolved: list[str] = field(default_factory=list)

    def is_actionable(self) -> bool:
        """True when enough is known to run an analysis."""
        return bool(self.date_from and self.date_to and (self.operator_ref or self.all_operators))


# Intent keywords (Hebrew + English).
_INTENT_KEYWORDS = {
    "penalties": ["קנס", "קנסות", "פיצוי", "penalt", "fine"],
    "punctuality": ["איחור", "דייקנות", "בזמן", "הקדמה", "punctual", "late", "delay"],
    "compare": ["השווה", "השוואה", "לעומת", "compare", "vs"],
    "execution": ["ביצוע", "בוטל", "ביטל", "בוצע", "נסיעות", "execution", "cancel", "missing"],
}


def _detect_intent(text: str) -> str:
    lowered = text.lower()
    for intent, words in _INTENT_KEYWORDS.items():
        if any(w in lowered for w in words):
            return intent
    return "execution"


def _detect_window(text: str) -> tuple[str | None, str | None]:
    """Extract a date window from relative phrases or explicit ISO dates."""
    iso = re.findall(r"\d{4}-\d{2}-\d{2}", text)
    if len(iso) >= 2:
        return iso[0], iso[1]
    if len(iso) == 1:
        return iso[0], iso[0]

    today = date.today()
    lowered = text.lower()
    if any(w in lowered for w in ["שבוע שעבר", "השבוע שעבר", "last week"]):
        days_since_sunday = (today.weekday() + 1) % 7
        this_sunday = today - timedelta(days=days_since_sunday)
        last_sunday = this_sunday - timedelta(days=7)
        return last_sunday.isoformat(), (last_sunday + timedelta(days=6)).isoformat()
    if any(w in lowered for w in ["אתמול", "yesterday"]):
        y = (today - timedelta(days=1)).isoformat()
        return y, y
    if any(w in lowered for w in ["היום", "today"]):
        return today.isoformat(), today.isoformat()
    if any(w in lowered for w in ["אמש", "last night"]):
        y = (today - timedelta(days=1)).isoformat()
        return y, y
    return None, None


def _detect_route(text: str) -> str | None:
    match = re.search(r"(?:קו|line)\s*([0-9]+[א-ת]?)", text, flags=re.IGNORECASE)
    return match.group(1) if match else None


def parse_question(
    text: str,
    *,
    known_operators: Iterable[tuple[int, str]] | None = None,
) -> ParsedQuery:
    """Parse a free-text question into a :class:`ParsedQuery`.

    Args:
        text: The user's question, Hebrew or English.
        known_operators: Optional iterable of ``(operator_ref, name)`` used to
            resolve an operator name to its ref. Pass
            ``[(o.operator_ref, o.name) for o in client.list_operators()]`` to
            keep resolution fully dynamic.

    Returns:
        A :class:`ParsedQuery`. Anything that could not be resolved is recorded
        in ``unresolved`` so the caller can ask a follow-up question.
    """
    query = ParsedQuery(intent=_detect_intent(text))
    query.date_from, query.date_to = _detect_window(text)
    query.route_short_name = _detect_route(text)

    lowered = text.lower()
    if any(w in lowered for w in ["כל המפעילים", "כולם", "all operators", "everyone"]):
        query.all_operators = True

    if known_operators and not query.all_operators:
        for ref, name in known_operators:
            if name and name in text:
                query.operator_ref = int(ref)
                query.operator_name = name
                break

    if not query.date_from:
        query.unresolved.append("date_window")
    if not (query.operator_ref or query.all_operators):
        query.unresolved.append("operator")
    if query.unresolved:
        _logger.debug("Unresolved fields for %r: %s", text, query.unresolved)
    return query
