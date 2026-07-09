"""Central configuration: API endpoint, filesystem paths, defaults, logging.

Everything here is environment-overridable and free of operator-specific values.
Nothing in this module hard-codes an ``operator_ref`` — operator identity is
always discovered at runtime via the API (see ``data.stride_client``).
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

# --------------------------------------------------------------------------- #
# API
# --------------------------------------------------------------------------- #
BASE_URL: str = os.environ.get(
    "OPEN_BUS_STRIDE_BASE_URL", "https://open-bus-stride-api.hasadna.org.il"
)

# Default network behaviour for the HTTP client.
DEFAULT_TIMEOUT_SECONDS: int = int(os.environ.get("STRIDE_TIMEOUT_SECONDS", "60"))
DEFAULT_MAX_RETRIES: int = int(os.environ.get("STRIDE_MAX_RETRIES", "3"))
DEFAULT_RETRY_BACKOFF_SECONDS: float = float(
    os.environ.get("STRIDE_RETRY_BACKOFF_SECONDS", "0.5")
)
# Minimum seconds between successive requests (simple rate limiting; 0 disables).
DEFAULT_RATE_LIMIT_SECONDS: float = float(
    os.environ.get("STRIDE_RATE_LIMIT_SECONDS", "0.0")
)
# Page size for limit/offset pagination.
DEFAULT_PAGE_SIZE: int = int(os.environ.get("STRIDE_PAGE_SIZE", "5000"))
# Concurrent worker threads when fanning out per-line fetches.
DEFAULT_WORKERS: int = int(os.environ.get("STRIDE_WORKERS", "6"))

# Known Stride endpoints (do not invent fields/paths beyond these — see AGENTS.md).
ENDPOINT_ROUTES = "/gtfs_routes/list"
ENDPOINT_RIDES_EXECUTION = "/rides_execution/list"
ENDPOINT_GTFS_RIDES = "/gtfs_rides/list"
ENDPOINT_GTFS_RIDE_STOPS = "/gtfs_ride_stops/list"
ENDPOINT_SIRI_RIDES = "/siri_rides/list"
ENDPOINT_SIRI_RIDE_STOPS = "/siri_ride_stops/list"
ENDPOINT_SIRI_SNAPSHOTS = "/siri_snapshots/list"

# --------------------------------------------------------------------------- #
# Filesystem layout
# --------------------------------------------------------------------------- #
PACKAGE_DIR: Path = Path(__file__).resolve().parent
PROJECT_ROOT: Path = PACKAGE_DIR.parent

DATA_DIR: Path = Path(os.environ.get("STRIDE_DATA_DIR", str(PROJECT_ROOT / "data")))
RAW_DIR: Path = DATA_DIR / "raw"
CACHE_DIR: Path = Path(os.environ.get("STRIDE_CACHE_DIR", str(DATA_DIR / "cache")))
REFERENCE_DIR: Path = DATA_DIR / "reference"
OUTPUT_DIR: Path = Path(os.environ.get("STRIDE_OUTPUT_DIR", str(DATA_DIR / "output")))

# Default reference workbooks.
PENALTIES_WORKBOOK: Path = REFERENCE_DIR / "penalties_workbook.xlsx"

# Local timezone for bucketing rides into service days (Israel).
LOCAL_TZ: str = "Asia/Jerusalem"

# --------------------------------------------------------------------------- #
# KPI thresholds (defaults; analysis functions accept overrides)
# --------------------------------------------------------------------------- #
# Minutes of delay at which a ride is counted "late" for the user-facing metric.
LATE_THRESHOLD_MIN: int = 5
# Penalty workbook table 3 ("חריגה מעל 10 דקות").
PENALTY_LATE_MIN: int = 10
# Penalty workbook table 4 ("הקדמה מ-2 דקות ומעלה").
EARLY_THRESHOLD_MIN: int = 2

# Toggle caching globally (env STRIDE_CACHE_ENABLED=0 to disable).
CACHE_ENABLED: bool = os.environ.get("STRIDE_CACHE_ENABLED", "1") not in {"0", "false", "False"}
# Cache time-to-live in seconds (0 = never expires).
CACHE_TTL_SECONDS: int = int(os.environ.get("STRIDE_CACHE_TTL_SECONDS", "0"))


def ensure_dirs() -> None:
    """Create the data directories if they do not yet exist."""
    for path in (RAW_DIR, CACHE_DIR, REFERENCE_DIR, OUTPUT_DIR):
        path.mkdir(parents=True, exist_ok=True)


def get_logger(name: str = "stride_analysis") -> logging.Logger:
    """Return a configured logger.

    Honors the ``STRIDE_LOG_LEVEL`` environment variable (default ``INFO``).
    A single stream handler is attached lazily so importing the package is quiet.
    """
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s")
        )
        logger.addHandler(handler)
        logger.propagate = False
    logger.setLevel(os.environ.get("STRIDE_LOG_LEVEL", "INFO").upper())
    return logger
