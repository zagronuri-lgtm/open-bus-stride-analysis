"""Analysis layer: execution, punctuality, penalties, period comparison.

Every function here is generic: it operates on a DataFrame with the expected
columns and is agnostic to which operator, line, or period produced it.
"""

from __future__ import annotations

from stride_analysis.analysis.compare import compare_periods
from stride_analysis.analysis.execution import calc_execution
from stride_analysis.analysis.penalties import (
    PenaltyTables,
    calc_penalties,
    load_penalty_tables,
)
from stride_analysis.analysis.punctuality import calc_punctuality

__all__ = [
    "calc_execution",
    "calc_punctuality",
    "calc_penalties",
    "load_penalty_tables",
    "PenaltyTables",
    "compare_periods",
]
