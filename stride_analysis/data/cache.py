"""Local file cache for API responses.

Keyed by a hash of (endpoint path, sorted params). JSON payloads are stored as
Parquet when they look tabular (fast, typed) and otherwise as ``.json``. This
means a repeated query — same endpoint, same params — never hits the network
twice, which matters when fanning out across hundreds of lines.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any

import pandas as pd

from stride_analysis import config

_logger = config.get_logger("stride_analysis.cache")


class Cache:
    """Disk cache for endpoint responses under :data:`config.CACHE_DIR`."""

    def __init__(
        self,
        cache_dir: Path | str | None = None,
        *,
        enabled: bool = config.CACHE_ENABLED,
        ttl_seconds: int = config.CACHE_TTL_SECONDS,
    ) -> None:
        self.cache_dir = Path(cache_dir) if cache_dir is not None else config.CACHE_DIR
        self.enabled = enabled
        self.ttl_seconds = ttl_seconds
        if self.enabled:
            self.cache_dir.mkdir(parents=True, exist_ok=True)

    # -- key handling ------------------------------------------------------- #
    @staticmethod
    def make_key(path: str, params: dict[str, Any]) -> str:
        """Deterministic cache key for an endpoint + params combination."""
        clean = {k: v for k, v in params.items() if v is not None}
        blob = json.dumps([path, clean], sort_keys=True, ensure_ascii=False, default=str)
        digest = hashlib.sha256(blob.encode("utf-8")).hexdigest()[:20]
        # Keep a readable prefix for humans browsing the cache dir.
        prefix = path.strip("/").replace("/", "_")[:40]
        return f"{prefix}__{digest}"

    def _paths(self, key: str) -> tuple[Path, Path]:
        return self.cache_dir / f"{key}.parquet", self.cache_dir / f"{key}.json"

    def _is_fresh(self, file: Path) -> bool:
        if self.ttl_seconds <= 0:
            return True
        try:
            age = time.time() - file.stat().st_mtime
        except OSError:
            return False
        return age <= self.ttl_seconds

    # -- public API --------------------------------------------------------- #
    def get(self, path: str, params: dict[str, Any]) -> list[dict[str, Any]] | None:
        """Return a cached list-of-records payload, or ``None`` on miss."""
        if not self.enabled:
            return None
        key = self.make_key(path, params)
        parquet_file, json_file = self._paths(key)
        try:
            if parquet_file.exists() and self._is_fresh(parquet_file):
                _logger.debug("cache hit (parquet): %s", key)
                return pd.read_parquet(parquet_file).to_dict(orient="records")
            if json_file.exists() and self._is_fresh(json_file):
                _logger.debug("cache hit (json): %s", key)
                return json.loads(json_file.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001 - cache must never be fatal
            _logger.warning("cache read failed for %s: %s", key, exc)
        return None

    def set(self, path: str, params: dict[str, Any], payload: list[dict[str, Any]]) -> None:
        """Persist a list-of-records payload for later reuse."""
        if not self.enabled or not isinstance(payload, list):
            return
        key = self.make_key(path, params)
        parquet_file, json_file = self._paths(key)
        try:
            if payload:
                # Parquet needs uniform-ish records; fall back to JSON on failure.
                try:
                    pd.DataFrame(payload).to_parquet(parquet_file, index=False)
                    return
                except Exception:  # noqa: BLE001
                    pass
            json_file.write_text(
                json.dumps(payload, ensure_ascii=False, default=str), encoding="utf-8"
            )
        except Exception as exc:  # noqa: BLE001
            _logger.warning("cache write failed for %s: %s", key, exc)

    def clear(self) -> int:
        """Delete all cached files. Returns the number of files removed."""
        if not self.cache_dir.exists():
            return 0
        removed = 0
        for file in self.cache_dir.glob("*"):
            if file.suffix in {".parquet", ".json"}:
                file.unlink(missing_ok=True)
                removed += 1
        return removed
