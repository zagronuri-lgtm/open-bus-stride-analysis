"""Chart helpers for transit reports (matplotlib, RTL-friendly).

All functions accept a tidy DataFrame and write a PNG to ``out_path`` (or a
default under :data:`config.OUTPUT_DIR`). Matplotlib is imported lazily and with
the non-interactive ``Agg`` backend so charts render headless (CI, bots).

Hebrew labels are reshaped for correct right-to-left display when the optional
``arabic_reshaper`` / ``python-bidi`` packages are installed; otherwise the raw
string is used (still legible, just visually LTR).
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from stride_analysis import config

_logger = config.get_logger("stride_analysis.charts")


def _mpl():
    """Import matplotlib with a headless backend on first use."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    return plt


def _rtl(text: object) -> str:
    """Best-effort reshape a (possibly Hebrew) label for RTL rendering."""
    s = str(text)
    try:  # optional deps; degrade gracefully
        import arabic_reshaper
        from bidi.algorithm import get_display

        return get_display(arabic_reshaper.reshape(s))
    except Exception:  # noqa: BLE001
        return s


def _resolve(out_path: Path | str | None, default_name: str) -> Path:
    if out_path is not None:
        path = Path(out_path)
    else:
        config.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        path = config.OUTPUT_DIR / default_name
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def create_bar(
    data: pd.DataFrame,
    *,
    label_col: str,
    value_col: str,
    title: str = "",
    out_path: Path | str | None = None,
    color: str = "#2c7fb8",
) -> Path:
    """Horizontal bar chart (good for ranking operators by a metric)."""
    plt = _mpl()
    df = data.sort_values(value_col)
    fig, ax = plt.subplots(figsize=(9, max(3, 0.5 * len(df) + 1)))
    ax.barh([_rtl(v) for v in df[label_col]], df[value_col], color=color)
    ax.set_title(_rtl(title))
    ax.set_xlabel(_rtl(value_col))
    for i, v in enumerate(df[value_col]):
        ax.text(v, i, f" {v:,.0f}", va="center", fontsize=8)
    fig.tight_layout()
    path = _resolve(out_path, "bar.png")
    fig.savefig(path, dpi=130)
    plt.close(fig)
    _logger.info("wrote %s", path)
    return path


def create_timeline(
    data: pd.DataFrame,
    *,
    date_col: str = "service_date",
    value_col: str = "missing_pct",
    group_col: str | None = "operator_name",
    title: str = "",
    out_path: Path | str | None = None,
) -> Path:
    """Line chart of a metric over time, one line per group."""
    plt = _mpl()
    fig, ax = plt.subplots(figsize=(11, 6))
    df = data.copy()
    df[date_col] = pd.to_datetime(df[date_col], errors="coerce")
    if group_col and group_col in df.columns:
        for name, block in df.groupby(group_col):
            block = block.sort_values(date_col)
            ax.plot(block[date_col], block[value_col], marker="o", label=_rtl(name))
        ax.legend(loc="best", fontsize=8)
    else:
        df = df.sort_values(date_col)
        ax.plot(df[date_col], df[value_col], marker="o")
    ax.set_title(_rtl(title))
    ax.set_ylabel(_rtl(value_col))
    ax.grid(True, alpha=0.3)
    fig.autofmt_xdate()
    fig.tight_layout()
    path = _resolve(out_path, "timeline.png")
    fig.savefig(path, dpi=130)
    plt.close(fig)
    _logger.info("wrote %s", path)
    return path


def create_heatmap(
    data: pd.DataFrame,
    *,
    index_col: str = "operator_name",
    column_col: str = "service_date",
    value_col: str = "missing_pct",
    title: str = "",
    out_path: Path | str | None = None,
    cmap: str = "YlOrRd",
) -> Path:
    """Heatmap of a metric across two dimensions (e.g. operator × day)."""
    plt = _mpl()
    pivot = data.pivot_table(
        index=index_col, columns=column_col, values=value_col, aggfunc="mean"
    )
    fig, ax = plt.subplots(figsize=(1.2 * len(pivot.columns) + 4, 0.5 * len(pivot) + 2))
    im = ax.imshow(pivot.values, aspect="auto", cmap=cmap)
    ax.set_xticks(range(len(pivot.columns)))
    ax.set_xticklabels([_rtl(c) for c in pivot.columns], rotation=45, ha="right", fontsize=8)
    ax.set_yticks(range(len(pivot.index)))
    ax.set_yticklabels([_rtl(i) for i in pivot.index], fontsize=9)
    ax.set_title(_rtl(title))
    for i in range(pivot.shape[0]):
        for j in range(pivot.shape[1]):
            val = pivot.values[i, j]
            if pd.notna(val):
                ax.text(j, i, f"{val:.1f}", ha="center", va="center", fontsize=7)
    fig.colorbar(im, ax=ax, label=_rtl(value_col))
    fig.tight_layout()
    path = _resolve(out_path, "heatmap.png")
    fig.savefig(path, dpi=130)
    plt.close(fig)
    _logger.info("wrote %s", path)
    return path
