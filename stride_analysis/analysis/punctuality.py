"""Punctuality KPIs: late %, early %, average delay.

Delay is ``actual_start_time − planned_start_time`` in minutes. A ride counts as
*late* when its delay ≥ ``late_threshold_min`` and *early* when its (negative)
delay ≤ ``−early_threshold_min``.

Important data caveat (see AGENTS.md): in ``/rides_execution/list`` the
``actual_start_time`` often mirrors the scheduled time, so departure lateness is
**not** derivable there — only execution/missing. Real punctuality requires the
SIRI feed (:meth:`StrideClient.fetch_siri`). This function therefore reports how
many rows actually had a usable, *differing* actual time, and degrades gracefully
(returning ``None`` metrics) when none do, instead of reporting a misleading 0%.
"""

from __future__ import annotations

import pandas as pd

from stride_analysis import config

_logger = config.get_logger("stride_analysis.punctuality")


def add_delay_minutes(
    rides: pd.DataFrame,
    *,
    planned_col: str = "planned_start_time",
    actual_col: str = "actual_start_time",
    out_col: str = "delay_min",
) -> pd.DataFrame:
    """Return a copy with a ``delay_min`` column (NaN where not computable)."""
    df = rides.copy()
    if planned_col not in df.columns or actual_col not in df.columns:
        df[out_col] = pd.NA
        return df
    planned = pd.to_datetime(df[planned_col], utc=True, errors="coerce")
    actual = pd.to_datetime(df[actual_col], utc=True, errors="coerce")
    df[out_col] = (actual - planned).dt.total_seconds() / 60.0
    return df


def calc_punctuality(
    rides: pd.DataFrame,
    *,
    group_by: list[str] | None = None,
    late_threshold_min: float = config.LATE_THRESHOLD_MIN,
    early_threshold_min: float = config.EARLY_THRESHOLD_MIN,
) -> pd.DataFrame:
    """Compute punctuality KPIs from a rides DataFrame.

    Args:
        rides: Raw ride rows with planned/actual start times (or a precomputed
            ``delay_min`` column).
        group_by: Dimensions to group by. ``None`` → single overall record.
        late_threshold_min: Delay (minutes) at/above which a ride is "late".
        early_threshold_min: Earliness (minutes) at/above which a ride is "early".

    Returns:
        DataFrame with ``measurable`` (rows with a usable delay), ``late``,
        ``early``, ``late_pct``, ``early_pct`` and ``avg_delay_min``. When no row
        has a measurable, differing delay, the *pct/avg* metrics are ``None`` and
        a warning is logged — never a misleading zero.
    """
    if rides is None or rides.empty:
        return _empty_punctuality(group_by)

    df = rides if "delay_min" in rides.columns else add_delay_minutes(rides)
    df = df.copy()
    # Only rows where actual differs from planned are genuinely measurable.
    df["_measurable"] = df["delay_min"].notna() & (df["delay_min"].abs() > 1e-9)

    if group_by:
        group_by = [c for c in group_by if c in df.columns]

    def _summarize(block: pd.DataFrame) -> dict[str, object]:
        measurable = block.loc[block["_measurable"], "delay_min"]
        n_meas = int(measurable.shape[0])
        if n_meas == 0:
            return {
                "rides": int(block.shape[0]),
                "measurable": 0,
                "late": 0,
                "early": 0,
                "late_pct": None,
                "early_pct": None,
                "avg_delay_min": None,
            }
        late = int((measurable >= late_threshold_min).sum())
        early = int((measurable <= -early_threshold_min).sum())
        return {
            "rides": int(block.shape[0]),
            "measurable": n_meas,
            "late": late,
            "early": early,
            "late_pct": round(100.0 * late / n_meas, 2),
            "early_pct": round(100.0 * early / n_meas, 2),
            "avg_delay_min": round(float(measurable.mean()), 2),
        }

    if not group_by:
        result = pd.DataFrame([_summarize(df)])
    else:
        records = []
        for keys, block in df.groupby(group_by, dropna=False):
            keys = keys if isinstance(keys, tuple) else (keys,)
            record = dict(zip(group_by, keys))
            record.update(_summarize(block))
            records.append(record)
        result = pd.DataFrame(records).sort_values(group_by).reset_index(drop=True)

    if (result["measurable"] == 0).all():
        _logger.warning(
            "No rides had a measurable departure delay — punctuality is not "
            "derivable from this feed. Use fetch_siri() for real punctuality."
        )
    return result


def _empty_punctuality(group_by: list[str] | None) -> pd.DataFrame:
    cols = [
        *(group_by or []),
        "rides",
        "measurable",
        "late",
        "early",
        "late_pct",
        "early_pct",
        "avg_delay_min",
    ]
    return pd.DataFrame(columns=cols)
