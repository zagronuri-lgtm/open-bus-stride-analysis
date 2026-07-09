"""Compare execution KPIs between two periods (e.g. this week vs. last week)."""

from __future__ import annotations

import pandas as pd

from stride_analysis import config

_logger = config.get_logger("stride_analysis.compare")

# Metrics compared by default and how to label their deltas.
_DEFAULT_METRICS = ["planned", "executed", "missing", "missing_pct", "penalty_nis"]


def compare_periods(
    period_a: pd.DataFrame,
    period_b: pd.DataFrame,
    *,
    on: list[str] | None = None,
    metrics: list[str] | None = None,
    labels: tuple[str, str] = ("a", "b"),
) -> pd.DataFrame:
    """Join two period summaries on shared keys and compute deltas.

    Args:
        period_a: Earlier period summary (the baseline).
        period_b: Later period summary (the comparison).
        on: Join keys. Defaults to the entity keys present in both frames
            (``operator_ref`` / ``operator_name`` / ``service_date`` excluded —
            dates differ between periods, so they are dropped automatically).
        metrics: Metric columns to diff. Defaults to the standard execution set.
        labels: Suffixes used to disambiguate the two periods.

    Returns:
        A DataFrame with, per key, ``<metric>_<labelA>``, ``<metric>_<labelB>``,
        ``<metric>_delta`` (B − A) and ``<metric>_pct_change`` columns.
    """
    la, lb = labels
    metrics = metrics or _DEFAULT_METRICS

    if on is None:
        # Default to entity identity, never the time axis.
        candidate_keys = ["operator_ref", "operator_name", "agency_name", "line_ref", "route_short_name"]
        on = [c for c in candidate_keys if c in period_a.columns and c in period_b.columns]

    a = _collapse(period_a, on, metrics)
    b = _collapse(period_b, on, metrics)

    if not on:
        # No shared keys: compare grand totals as a single row.
        a["_k"] = 0
        b["_k"] = 0
        on = ["_k"]

    merged = a.merge(b, on=on, how="outer", suffixes=(f"_{la}", f"_{lb}"))

    for metric in metrics:
        col_a, col_b = f"{metric}_{la}", f"{metric}_{lb}"
        if col_a not in merged.columns or col_b not in merged.columns:
            continue
        merged[col_a] = merged[col_a].fillna(0)
        merged[col_b] = merged[col_b].fillna(0)
        merged[f"{metric}_delta"] = (merged[col_b] - merged[col_a]).round(2)
        merged[f"{metric}_pct_change"] = _pct_change(merged[col_a], merged[col_b])

    merged = merged.drop(columns=["_k"], errors="ignore")
    return merged


def _collapse(df: pd.DataFrame, on: list[str], metrics: list[str]) -> pd.DataFrame:
    """Sum metrics up to the join-key grain so periods align cleanly."""
    present_metrics = [m for m in metrics if m in df.columns]
    if not on:
        return df[present_metrics].sum().to_frame().T
    grouped = df.groupby(on, dropna=False)[present_metrics].sum().reset_index()
    # Recompute missing_pct after summation so it stays meaningful.
    if "missing" in grouped.columns and "planned" in grouped.columns:
        grouped["missing_pct"] = (
            100.0 * grouped["missing"] / grouped["planned"].replace(0, pd.NA)
        ).fillna(0.0).round(2)
    return grouped


def _pct_change(before: pd.Series, after: pd.Series) -> pd.Series:
    before = pd.to_numeric(before, errors="coerce")
    after = pd.to_numeric(after, errors="coerce")
    denom = before.where(before != 0)  # 0 → NaN (float dtype, round-safe)
    return (100.0 * (after - before) / denom).round(2)
