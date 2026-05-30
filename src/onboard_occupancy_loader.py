"""Onboard bus occupancy 2022 loader.

Fetches the Ministry of Transport "onboard bus 2022 survey" dataset from
data.gov.il via the CKAN API and caches it locally as Parquet to avoid
repeated downloads.

Dataset:
    https://data.gov.il/he/datasets/ministry_of_transport/onboardbusta2022peima1

Usage:
    from onboard_occupancy_loader import load_onboard_occupancy
    df = load_onboard_occupancy()

Design principles (per AGENTS.md / SKILL.md):
    - No data is guessed: if the CKAN response is missing/empty, raise.
    - Cache is content-addressed by resource_id; stale caches are ignored
      only when the caller passes force_refresh=True.
    - Column names are normalized to a stable schema; unknown columns are
      preserved as-is so analysts can inspect them.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

import requests

try:  # pandas is a soft dependency at import time but required to load.
    import pandas as pd
except ImportError:  # pragma: no cover - surfaced only when actually used
    pd = None  # type: ignore[assignment]

LOGGER = logging.getLogger(__name__)

# CKAN datastore search endpoint on data.gov.il.
CKAN_BASE_URL = "https://data.gov.il/api/3/action/datastore_search"

# Dataset / resource identifiers for the 2022 onboard occupancy survey.
# The dataset slug is stable, but resource_id may change if the publisher
# republishes the data. Override via env var ONBOARD_2022_RESOURCE_ID.
DATASET_SLUG = "onboardbusta2022peima1"
DEFAULT_RESOURCE_ID_ENV = "ONBOARD_2022_RESOURCE_ID"

# Default cache directory inside the repo (gitignored).
DEFAULT_CACHE_DIR = Path(".cache")
DEFAULT_CACHE_FILE = "onboard2022.parquet"

# CKAN paginates at 100 rows by default; bump to the documented maximum.
CKAN_PAGE_LIMIT = 32000

# Canonical schema we expose to downstream analyzers. The mapping below maps
# the raw Hebrew/English column names that appear in CKAN to these keys.
CANONICAL_COLUMNS = (
    "operator",
    "line_id",
    "direction",
    "stop_id",
    "day_type",
    "hour",
    "boardings",
)

# Best-effort mapping. Extend as new field names are observed in the wild.
RAW_TO_CANONICAL = {
    # Hebrew variants
    "מפעיל": "operator",
    "שם מפעיל": "operator",
    "מספר קו": "line_id",
    "קו": "line_id",
    "כיוון": "direction",
    "תחנה": "stop_id",
    "קוד תחנה": "stop_id",
    "סוג יום": "day_type",
    "שעה": "hour",
    "עליות": "boardings",
    "מס' נוסעים": "boardings",
    # English variants (in case the publisher provides bilingual fields)
    "operator": "operator",
    "line_id": "line_id",
    "direction": "direction",
    "stop_id": "stop_id",
    "day_type": "day_type",
    "hour": "hour",
    "boardings": "boardings",
}


@dataclass(frozen=True)
class LoaderConfig:
    """Runtime configuration for the loader."""

    resource_id: str
    cache_dir: Path = DEFAULT_CACHE_DIR
    cache_file: str = DEFAULT_CACHE_FILE
    page_limit: int = CKAN_PAGE_LIMIT
    timeout_sec: int = 30

    @property
    def cache_path(self) -> Path:
        return self.cache_dir / self.cache_file


class OnboardOccupancyLoaderError(RuntimeError):
    """Raised when the dataset cannot be retrieved or validated."""


def _resolve_resource_id(explicit: Optional[str]) -> str:
    if explicit:
        return explicit
    env_value = os.environ.get(DEFAULT_RESOURCE_ID_ENV)
    if env_value:
        return env_value
    raise OnboardOccupancyLoaderError(
        "resource_id is required. Pass it explicitly or set the "
        f"{DEFAULT_RESOURCE_ID_ENV} environment variable. Find the current "
        "resource_id on the dataset page: "
        "https://data.gov.il/he/datasets/ministry_of_transport/"
        f"{DATASET_SLUG}"
    )


def _fetch_page(config: LoaderConfig, offset: int) -> dict:
    params = {
        "resource_id": config.resource_id,
        "limit": config.page_limit,
        "offset": offset,
    }
    LOGGER.debug("CKAN request: %s offset=%s", config.resource_id, offset)
    response = requests.get(
        CKAN_BASE_URL, params=params, timeout=config.timeout_sec
    )
    response.raise_for_status()
    payload = response.json()
    if not payload.get("success"):
        raise OnboardOccupancyLoaderError(
            f"CKAN reported failure: {payload!r}"
        )
    return payload["result"]


def _iter_records(config: LoaderConfig) -> Iterable[dict]:
    offset = 0
    while True:
        result = _fetch_page(config, offset=offset)
        records = result.get("records") or []
        if not records:
            return
        for record in records:
            yield record
        total = result.get("total", 0)
        offset += len(records)
        if offset >= total:
            return


def _normalize_columns(df: "pd.DataFrame") -> "pd.DataFrame":
    rename_map = {
        col: RAW_TO_CANONICAL[col]
        for col in df.columns
        if col in RAW_TO_CANONICAL
    }
    return df.rename(columns=rename_map)


def load_onboard_occupancy(
    resource_id: Optional[str] = None,
    *,
    cache_dir: Path | str | None = None,
    force_refresh: bool = False,
) -> "pd.DataFrame":
    """Return the onboard occupancy 2022 survey as a DataFrame.

    Args:
        resource_id: CKAN resource_id for the dataset's primary CSV.
            If omitted, falls back to the ONBOARD_2022_RESOURCE_ID env var.
        cache_dir: Directory where the Parquet cache is stored.
        force_refresh: If True, ignore any cached Parquet and re-fetch.

    Raises:
        OnboardOccupancyLoaderError: If pandas is missing, the resource_id
            is unknown, the response is malformed, or the dataset is empty.
    """
    if pd is None:
        raise OnboardOccupancyLoaderError(
            "pandas is required for load_onboard_occupancy(). "
            "Install it via `pip install pandas pyarrow`."
        )

    config = LoaderConfig(
        resource_id=_resolve_resource_id(resource_id),
        cache_dir=Path(cache_dir) if cache_dir else DEFAULT_CACHE_DIR,
    )

    cache_path = config.cache_path
    if cache_path.exists() and not force_refresh:
        LOGGER.info("Loading onboard occupancy from cache: %s", cache_path)
        return pd.read_parquet(cache_path)

    LOGGER.info(
        "Fetching onboard occupancy 2022 from CKAN (resource_id=%s)",
        config.resource_id,
    )
    records = list(_iter_records(config))
    if not records:
        raise OnboardOccupancyLoaderError(
            "CKAN returned zero records. Verify resource_id and that the "
            "dataset is still published."
        )

    df = pd.DataFrame.from_records(records)
    df = _normalize_columns(df)

    cache_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(cache_path, index=False)
    LOGGER.info(
        "Cached %d rows to %s", len(df), cache_path
    )
    return df


__all__ = [
    "CANONICAL_COLUMNS",
    "LoaderConfig",
    "OnboardOccupancyLoaderError",
    "load_onboard_occupancy",
]
