"""Placeholder for the conversational bot layer.

The bot turns natural-language questions ("מה הביצועים של דן בשבוע שעבר?") into
structured query parameters that drive the existing analysis pipeline. See
:mod:`stride_analysis.bot.handler` and ``bot/README.md`` for the design.
"""

from __future__ import annotations

from stride_analysis.bot.handler import ParsedQuery, parse_question

__all__ = ["ParsedQuery", "parse_question"]
