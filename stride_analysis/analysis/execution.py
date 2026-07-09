"""Execution KPIs: planned vs. actual, missing rides, missing %.

Generic over any rides DataFrame. Two input shapes are supported automatically:

  1. **Raw ride rows** (one row per ride) with an ``executed`` boolean (or an
     ``actual_start_time`` column from which it is derived). This is what
     :meth:`StrideClient.fetch_rides` returns.
  2. **Pre-aggregated rows** already carrying ``planned`` and ``executed``
     integer counts (e.g. a cached weekly summary CSV).

``calc_execution`` returns a tidy frame grouped by ``group_by`` dimensions, plus
``planned``, ``executed``, ``missing`` and ``missing_pct`` columns.
"""

from __future__ import annotations

import pandas as pd

from stride_analysis import config

_logger = config.get_logger("stride_analysis.execution")

# Default grouping dimensions for an execution summary.
DEFAULT_GROUP_BY = ["operator_ref", "service_date"]


def _is_aggregated(rides: pd.DataFrame) -> bool:
    """True when the frame already carries planned/executed counts."""
    return {"planned", "executed"}.issubset(rides.columns) and "actual_start_time" not in rides.columns


def calc_execution(
    rides: pd.DataFrame,
    *,
    group_by: list[str] | None = None,
    keep_names: bool = True,
) -> pd.DataFrame:
    """Compute execution KPIs from a rides DataFrame.

    Args:
        rides: Raw ride rows or pre-aggregated counts (see module docstring).
        group_by: Dimensions to group by. Defaults to
            ``["operator_ref", "service_date"]``. Pass ``[]`` for a single total.
        keep_names: Carry through descriptive columns like ``operator_name`` /
            ``agency_name`` / ``route_short_name`` when present.

    Returns:
        DataFrame with the group_by columns plus ``planned``, ``executed``,
        ``missing`` and ``missing_pct`` (rounded to 2 decimals). One extra row is
        not added; callers can append totals via :func:`execution_totals`.
    """
    group_by = DEFAULT_GROUP_BY if group_by is None else list(group_by)

    if rides is None or rides.empty:
        return pd.DataFrame(columns=[*group_by, "planned", "executed", "missing", "missing_pct"])

    df = rides.copy()
    group_by = [c for c in group_by if c in df.columns]

    if _is_aggregated(df):
        agg = _aggregate_counts(df, group_by)
    else:
        df = _ensure_executed(df)
        agg = _aggregate_raw(df, group_by)

    agg["missing"] = agg["planned"] - agg["executed"]
    agg["missing_pct"] = _safe_pct(agg["missing"], agg["planned"])

    if keep_names:
        agg = _attach_names(df, agg, group_by)

    sort_cols = [c for c in group_by if c in agg.columns]
    if sort_cols:
        agg = agg.sort_values(sort_cols).reset_index(drop=True)
    return agg


def execution_totals(summary: pd.DataFrame) -> dict[str, float]:
    """Roll a per-slice execution summary up to a single overall record."""
    if summary is None or summary.empty:
        return {"planned": 0, "executed": 0, "missing": 0, "missing_pct": 0.0}
    planned = int(summary["planned"].sum())
    executed = int(summary["executed"].sum())
    missing = planned - executed
    return {
        "planned": planned,
        "executed": executed,
        "missing": missing,
        "missing_pct": round(100.0 * missing / planned, 2) if planned else 0.0,
    }


# --------------------------------------------------------------------------- #
# Internals
# --------------------------------------------------------------------------- #
def _ensure_executed(df: pd.DataFrame) -> pd.DataFrame:
    if "executed" not in df.columns:
        if "actual_start_time" in df.columns:
            df["executed"] = df["actual_start_time"].notna() & (
                df["actual_start_time"].astype(str).str.len() > 0
            )
        else:
            raise ValueError(
                "rides frame needs an 'executed' or 'actual_start_time' column"
            )
    df["executed"] = df["executed"].astype(bool)
    return df


def _aggregate_raw(df: pd.DataFrame, group_by: list[str]) -> pd.DataFrame:
    if not group_by:
        return pd.DataFrame(
            [{"planned": len(df), "executed": int(df["executed"].sum())}]
        )
    grouped = df.groupby(group_by, dropna=False)["executed"].agg(
        planned="size", executed="sum"
    )
    return grouped.reset_index()


def _aggregate_counts(df: pd.DataFrame, group_by: list[str]) -> pd.DataFrame:
    if not group_by:
        return pd.DataFrame(
            [{"planned": int(df["planned"].sum()), "executed": int(df["executed"].sum())}]
        )
    grouped = df.groupby(group_by, dropna=False)[["planned", "executed"]].sum()
    return grouped.reset_index()


def _safe_pct(numerator: pd.Series, denominator: pd.Series) -> pd.Series:
    pct = 100.0 * numerator / denominator.replace(0, pd.NA)
    return pct.fillna(0.0).round(2)


def _attach_names(source: pd.DataFrame, agg: pd.DataFrame, group_by: list[str]) -> pd.DataFrame:
    """Merge first-seen descriptive labels onto the aggregated frame."""
    name_cols = [
        c
        for c in ("operator_name", "agency_name", "route_short_name", "route_long_name")
        if c in source.columns and c not in agg.columns
    ]
    if not name_cols or not group_by:
        return agg
    key = group_by[0]
    if key not in source.columns:
        return agg
    lookup = (
        source.dropna(subset=[key])
        .groupby(key)[name_cols]
        .first()
        .reset_index()
    )
    merged = agg.merge(lookup, on=key, how="left")
    # Reorder: keep group keys, then names, then metrics.
    metric_cols = ["planned", "executed", "missing", "missing_pct"]
    ordered = [*group_by, *[c for c in name_cols if c in merged.columns], *metric_cols]
    ordered = [c for c in ordered if c in merged.columns]
    return merged[ordered]
