"""Monetary penalties from the official MOT compensation annex (נספח כ"ו).

The penalty rules live in ``data/reference/penalties_workbook.xlsx``. Rather than
hard-coding rates in Python (they change between contracts), this module *parses*
the workbook at runtime into banded lookup tables:

  * **Non-execution (Table 1)** — ₪ per missing ride, graded by the daily
    non-execution rate (missing %).
  * **Lateness (Table 3)** — ₪ per late ride, graded by the lateness rate.
  * **Earliness (Table 4)** — ₪ per early ride, graded by the earliness rate.

``calc_penalties`` then applies the non-execution table to an execution summary,
producing ₪ per slice (operator/day/…). It is fully generic — any execution
DataFrame with ``missing`` and ``missing_pct`` columns works.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from stride_analysis import config

_logger = config.get_logger("stride_analysis.penalties")

# Sheet that holds the graded electronic-control penalty tables.
_PENALTY_SHEET = "קנסות אי-ביצוע"

# Header markers that identify each graded table's first column.
_TABLE_MARKERS = {
    "non_execution": "שיעור אי-ביצוע",
    "lateness": "שיעור איחור",
    "earliness": "שיעור הקדמה",
}


@dataclass
class BandTable:
    """A graded ₪-per-event table: each row is [from_pct, to_pct) → amount."""

    name: str
    bands: pd.DataFrame  # columns: from_pct, to_pct, amount_nis

    def amount_for(self, pct: float) -> float:
        """Return the ₪-per-event amount for a given rate (in percent)."""
        if pct is None or pd.isna(pct):
            return 0.0
        for _, row in self.bands.iterrows():
            lo, hi = row["from_pct"], row["to_pct"]
            # Inclusive lower bound, exclusive upper — except the final band,
            # whose upper bound (100%) is inclusive.
            if lo <= pct < hi or (hi >= 100.0 and pct >= lo):
                return float(row["amount_nis"])
        return 0.0


@dataclass
class PenaltyTables:
    """Parsed penalty tables from the workbook."""

    non_execution: BandTable | None = None
    lateness: BandTable | None = None
    earliness: BandTable | None = None
    source: str = ""
    extras: dict[str, object] = field(default_factory=dict)


def load_penalty_tables(
    workbook: Path | str | None = None,
) -> PenaltyTables:
    """Parse the penalties workbook into banded lookup tables.

    Args:
        workbook: Path to ``penalties_workbook.xlsx``. Defaults to
            :data:`config.PENALTIES_WORKBOOK`.

    Returns:
        A :class:`PenaltyTables`. Missing tables are left as ``None`` with a
        warning rather than raising, so a partial workbook still works.
    """
    path = Path(workbook) if workbook else config.PENALTIES_WORKBOOK
    if not path.exists():
        raise FileNotFoundError(f"Penalties workbook not found: {path}")

    try:
        raw = pd.read_excel(path, sheet_name=_PENALTY_SHEET, header=None)
    except ValueError as exc:  # sheet missing
        raise ValueError(
            f"Sheet '{_PENALTY_SHEET}' not found in {path.name}: {exc}"
        ) from exc

    tables = PenaltyTables(source=str(path))
    for key, marker in _TABLE_MARKERS.items():
        band = _parse_band_table(raw, marker, key)
        setattr(tables, key, band)
        if band is None:
            _logger.warning("Penalty table '%s' (marker '%s') not found", key, marker)
        else:
            _logger.info("Parsed penalty table '%s' with %d bands", key, len(band.bands))
    return tables


def calc_penalties(
    execution: pd.DataFrame,
    penalty_tables: PenaltyTables | None = None,
    *,
    missing_col: str = "missing",
    missing_pct_col: str = "missing_pct",
    out_col: str = "penalty_nis",
) -> pd.DataFrame:
    """Apply the non-execution penalty table to an execution summary.

    For each row, the ₪-per-missing-ride rate is looked up from the band that
    contains its ``missing_pct``; the penalty is ``missing × rate``.

    Args:
        execution: Output of :func:`stride_analysis.analysis.calc_execution`
            (needs ``missing`` and ``missing_pct`` columns).
        penalty_tables: Parsed tables; loaded from the default workbook if ``None``.
        missing_col / missing_pct_col / out_col: Column name overrides.

    Returns:
        A copy of ``execution`` with ``nis_per_missing`` and ``penalty_nis``
        columns added.
    """
    if execution is None or execution.empty:
        result = (execution.copy() if execution is not None else pd.DataFrame())
        for col in ("nis_per_missing", out_col):
            if col not in result.columns:
                result[col] = pd.Series(dtype="float")
        return result

    tables = penalty_tables or load_penalty_tables()
    if tables.non_execution is None:
        raise ValueError("Non-execution penalty table could not be parsed from the workbook")

    if missing_pct_col not in execution.columns or missing_col not in execution.columns:
        raise ValueError(
            f"execution frame must contain '{missing_col}' and '{missing_pct_col}' columns"
        )

    df = execution.copy()
    df["nis_per_missing"] = df[missing_pct_col].apply(tables.non_execution.amount_for)
    df[out_col] = (df[missing_col].fillna(0) * df["nis_per_missing"]).round(0).astype("int64")
    return df


def penalties_by_operator(penalized: pd.DataFrame) -> pd.DataFrame:
    """Roll a per-slice penalty frame up to ₪ per operator."""
    keys = [c for c in ("operator_ref", "operator_name", "agency_name") if c in penalized.columns]
    if not keys:
        return pd.DataFrame([{"penalty_nis": int(penalized.get("penalty_nis", pd.Series()).sum())}])
    grouped = (
        penalized.groupby(keys, dropna=False)
        .agg(
            planned=("planned", "sum") if "planned" in penalized.columns else ("penalty_nis", "size"),
            missing=("missing", "sum") if "missing" in penalized.columns else ("penalty_nis", "size"),
            penalty_nis=("penalty_nis", "sum"),
        )
        .reset_index()
        .sort_values("penalty_nis", ascending=False)
        .reset_index(drop=True)
    )
    return grouped


# --------------------------------------------------------------------------- #
# Workbook parsing internals
# --------------------------------------------------------------------------- #
def _parse_band_table(raw: pd.DataFrame, marker: str, name: str) -> BandTable | None:
    """Locate a graded table by its header marker and parse its bands."""
    header_row = _find_marker_row(raw, marker)
    if header_row is None:
        return None

    bands: list[dict[str, float]] = []
    for idx in range(header_row + 1, len(raw)):
        row = raw.iloc[idx]
        lo = _parse_pct(row.iloc[0])
        hi = _parse_pct(row.iloc[1]) if raw.shape[1] > 1 else None
        amount = _parse_amount(row.iloc[2]) if raw.shape[1] > 2 else None
        # Stop at the first blank/non-numeric row after the table body.
        if lo is None or hi is None or amount is None:
            break
        bands.append({"from_pct": lo, "to_pct": hi, "amount_nis": amount})

    if not bands:
        return None
    return BandTable(name=name, bands=pd.DataFrame(bands))


def _find_marker_row(raw: pd.DataFrame, marker: str) -> int | None:
    for idx in range(len(raw)):
        first = raw.iloc[idx, 0]
        if isinstance(first, str) and marker in first:
            return idx
    return None


def _parse_pct(value: object) -> float | None:
    """Parse a cell like '1.5%' or 0.015 into a percent number (1.5)."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    if isinstance(value, (int, float)):
        # Heuristic: values ≤ 1 are fractions; otherwise already a percent.
        return float(value) * 100.0 if value <= 1 else float(value)
    text = str(value).strip()
    if not text:
        return None
    match = re.search(r"-?\d+(?:\.\d+)?", text)
    if not match:
        return None
    number = float(match.group())
    return number  # workbook stores explicit percent figures (e.g. "1.5%")


def _parse_amount(value: object) -> float | None:
    """Parse a ₪ amount cell like '5,000' or 63 into a float."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).replace(",", "").strip()
    match = re.search(r"\d+(?:\.\d+)?", text)
    return float(match.group()) if match else None
