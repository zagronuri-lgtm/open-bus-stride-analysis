"""Generic, dynamic client for the Open Bus Stride API.

Design goals:
  * Nothing operator-specific is hard-coded. Operators, routes and rides are all
    discovered at runtime, so the same client serves buses, Israel Railways, or a
    single line in a single town.
  * Every public fetch accepts ``operator_ref=None`` to mean "all operators".
  * Robust by default: retries with exponential backoff, request timeouts, simple
    rate limiting, limit/offset pagination, per-attempt logging, and an automatic
    local cache so a repeated query never hits the network twice.

Example::

    client = StrideClient()
    operators = client.list_operators()                       # discover everyone
    routes = client.list_routes(operator_ref=5)               # Dan's lines
    rides = client.fetch_rides("2026-05-24", "2026-05-30", operator_ref=5)
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable

import pandas as pd
import requests

from stride_analysis import config
from stride_analysis.data.cache import Cache
from stride_analysis.data.models import Operator, Route

_logger = config.get_logger("stride_analysis.client")

RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


@dataclass(frozen=True)
class RequestMetadata:
    """Provenance for a single HTTP attempt — enables reproducible analyses."""

    method: str
    url: str
    path: str
    params: dict[str, Any]
    attempt: int
    status_code: int | None
    elapsed_seconds: float
    timestamp_utc: str
    from_cache: bool = False
    error_type: str | None = None
    error_message: str | None = None


class StrideError(RuntimeError):
    """Base error for Stride client failures."""

    def __init__(self, message: str, *, metadata: RequestMetadata | None = None) -> None:
        super().__init__(message)
        self.metadata = metadata


class StrideRequestError(StrideError):
    """Network, timeout, or transport-level failure."""


class StrideHTTPError(StrideError):
    """Non-success HTTP response."""


class StrideResponseError(StrideError):
    """Unexpected or invalid response payload."""


@dataclass
class StrideClient:
    """HTTP client for the Open Bus Stride API.

    Args:
        base_url: API root. Defaults to :data:`config.BASE_URL`.
        timeout_seconds: Per-request timeout.
        max_retries: Retries on transient failures (exponential backoff).
        retry_backoff_seconds: Base backoff; doubled per attempt.
        rate_limit_seconds: Minimum spacing between requests (0 disables).
        page_size: Default limit/offset page size.
        cache: A :class:`Cache` (created from config if not supplied).
    """

    base_url: str = config.BASE_URL
    timeout_seconds: int = config.DEFAULT_TIMEOUT_SECONDS
    max_retries: int = config.DEFAULT_MAX_RETRIES
    retry_backoff_seconds: float = config.DEFAULT_RETRY_BACKOFF_SECONDS
    rate_limit_seconds: float = config.DEFAULT_RATE_LIMIT_SECONDS
    page_size: int = config.DEFAULT_PAGE_SIZE
    cache: Cache = field(default_factory=Cache)
    request_log: list[RequestMetadata] = field(default_factory=list)
    _session: requests.Session = field(default_factory=requests.Session, repr=False)
    _last_request_at: float = field(default=0.0, repr=False)

    # ------------------------------------------------------------------ #
    # Low-level HTTP
    # ------------------------------------------------------------------ #
    @property
    def last_request_metadata(self) -> RequestMetadata | None:
        """Metadata for the most recent request attempt, if any."""
        return self.request_log[-1] if self.request_log else None

    def get_json(
        self, path: str, *, use_cache: bool = True, **params: Any
    ) -> list[dict[str, Any]]:
        """GET an endpoint and return a list of records.

        Args:
            path: Endpoint path, e.g. ``/gtfs_routes/list``.
            use_cache: Read/write the local cache for this call.
            **params: Query parameters (``None`` values are dropped).

        Returns:
            Parsed JSON as a list of dict records.

        Raises:
            StrideError: On unrecoverable request/HTTP/response failure.
        """
        path = self._normalize_path(path)
        clean_params = {k: v for k, v in params.items() if v is not None}

        if use_cache:
            cached = self.cache.get(path, clean_params)
            if cached is not None:
                self._record(
                    path=path,
                    url=f"{self.base_url.rstrip('/')}{path}",
                    params=clean_params,
                    attempt=0,
                    status_code=200,
                    started=time.monotonic(),
                    from_cache=True,
                )
                return cached

        payload = self._request_with_retries(path, clean_params)
        records = self._coerce_records(payload, path)
        if use_cache:
            self.cache.set(path, clean_params, records)
        return records

    def get_df(self, path: str, *, use_cache: bool = True, **params: Any) -> pd.DataFrame:
        """Like :meth:`get_json` but returns a DataFrame."""
        params.setdefault("limit", 15000)
        return pd.DataFrame(self.get_json(path, use_cache=use_cache, **params))

    def paginated(
        self,
        path: str,
        *,
        page_size: int | None = None,
        max_pages: int | None = None,
        use_cache: bool = True,
        **params: Any,
    ) -> Iterable[list[dict[str, Any]]]:
        """Yield successive pages using limit/offset pagination."""
        size = page_size or self.page_size
        page = 0
        while max_pages is None or page < max_pages:
            payload = self.get_json(
                path, use_cache=use_cache, limit=size, offset=page * size, **params
            )
            if not payload:
                break
            yield payload
            if len(payload) < size:
                break
            page += 1

    def fetch_all(
        self, path: str, *, page_size: int | None = None, use_cache: bool = True, **params: Any
    ) -> list[dict[str, Any]]:
        """Fetch every page of a paginated endpoint into one list."""
        records: list[dict[str, Any]] = []
        for page in self.paginated(
            path, page_size=page_size, use_cache=use_cache, **params
        ):
            records.extend(page)
        return records

    # ------------------------------------------------------------------ #
    # High-level, domain-aware API
    # ------------------------------------------------------------------ #
    def list_operators(
        self, *, date: str | None = None, date_from: str | None = None, date_to: str | None = None
    ) -> list[Operator]:
        """Return every operator that has routes in the given window.

        Operators are derived from ``/gtfs_routes/list`` (unique
        ``operator_ref`` + ``agency_name``), so the list is always live and never
        hard-coded. With no dates given, a single recent service date is used.

        Args:
            date: A single service date (``YYYY-MM-DD``). Convenience for
                ``date_from == date_to``.
            date_from / date_to: Explicit window.
        """
        d_from, d_to = self._resolve_dates(date, date_from, date_to)
        routes = self.get_df(
            config.ENDPOINT_ROUTES, date_from=d_from, date_to=d_to, limit=15000
        )
        if routes.empty or "operator_ref" not in routes.columns:
            _logger.warning("No routes returned for %s..%s", d_from, d_to)
            return []
        name_col = "agency_name" if "agency_name" in routes.columns else None
        operators: list[Operator] = []
        grouped = routes.dropna(subset=["operator_ref"]).groupby("operator_ref")
        for operator_ref, group in grouped:
            name = ""
            if name_col:
                names = group[name_col].dropna().unique().tolist()
                name = str(names[0]) if names else ""
            operators.append(
                Operator(operator_ref=int(operator_ref), name=name, route_count=len(group))
            )
        operators.sort(key=lambda o: (-(o.route_count or 0), o.operator_ref))
        _logger.info("Discovered %d operators for %s..%s", len(operators), d_from, d_to)
        return operators

    def list_routes(
        self,
        *,
        operator_ref: int | str | None = None,
        date: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        route_short_name: str | None = None,
        route_mkt: str | None = None,
        route_direction: int | str | None = None,
        route_alternative: str | None = None,
    ) -> list[Route]:
        """Return routes, optionally filtered. ``operator_ref=None`` → all."""
        d_from, d_to = self._resolve_dates(date, date_from, date_to)
        rows = self.get_df(
            config.ENDPOINT_ROUTES,
            date_from=d_from,
            date_to=d_to,
            operator_refs=str(operator_ref) if operator_ref is not None else None,
            route_short_name=route_short_name,
            route_mkt=route_mkt,
            route_direction=str(route_direction) if route_direction is not None else None,
            route_alternative=route_alternative,
            order_by="line_ref asc",
            limit=15000,
        )
        if rows.empty:
            return []
        return [Route.from_api(r) for r in rows.to_dict(orient="records")]

    def list_line_refs(
        self,
        *,
        operator_ref: int | str,
        date_from: str,
        date_to: str,
        route_short_name: str | None = None,
    ) -> list[int]:
        """Sorted unique ``line_ref`` values for an operator in a window."""
        routes = self.list_routes(
            operator_ref=operator_ref,
            date_from=date_from,
            date_to=date_to,
            route_short_name=route_short_name,
        )
        return sorted({r.line_ref for r in routes if r.line_ref is not None})

    def fetch_rides(
        self,
        date_from: str,
        date_to: str,
        *,
        operator_ref: int | str | None = None,
        line_ref: int | str | None = None,
        route_short_name: str | None = None,
        workers: int = config.DEFAULT_WORKERS,
        progress: bool = False,
    ) -> pd.DataFrame:
        """Fetch planned-vs-actual rides from ``/rides_execution/list``.

        Resolution strategy (all dynamic, nothing hard-coded):
          * ``line_ref`` given → fetch that line directly.
          * else enumerate the relevant ``line_ref`` set via
            :meth:`list_routes` and fetch each concurrently, then concat.
          * ``operator_ref=None`` → every operator discovered for the window.

        Returns:
            A tidy DataFrame of ride rows with at least ``operator_ref``,
            ``line_ref``, ``planned_start_time``, ``actual_start_time``, and a
            derived ``service_date`` (Asia/Jerusalem) and ``executed`` flag.
        """
        if line_ref is not None:
            frame = self._fetch_rides_for_line(operator_ref, line_ref, date_from, date_to)
            return self._decorate_rides(frame)

        # Build the work list: (operator_ref, line_ref) pairs.
        if operator_ref is not None:
            operator_refs: list[int | str] = [operator_ref]
        else:
            operator_refs = [op.operator_ref for op in self.list_operators(
                date_from=date_from, date_to=date_to
            )]

        tasks: list[tuple[int | str, int]] = []
        for ref in operator_refs:
            lines = self.list_line_refs(
                operator_ref=ref,
                date_from=date_from,
                date_to=date_to,
                route_short_name=route_short_name,
            )
            tasks.extend((ref, lr) for lr in lines)
            if progress:
                _logger.info("operator %s: %d lines", ref, len(lines))
        _logger.info("Fetching rides for %d (operator,line) tasks", len(tasks))

        frames: list[pd.DataFrame] = []
        errors = 0
        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            future_to_task = {
                pool.submit(
                    self._fetch_rides_for_line, ref, lr, date_from, date_to
                ): (ref, lr)
                for ref, lr in tasks
            }
            done = 0
            for future in as_completed(future_to_task):
                done += 1
                try:
                    frame = future.result()
                    if not frame.empty:
                        frames.append(frame)
                except StrideError as exc:
                    errors += 1
                    _logger.debug("ride fetch failed for %s: %s", future_to_task[future], exc)
                if progress and done % 250 == 0:
                    _logger.info("progress %d/%d (%d errors)", done, len(tasks), errors)

        if errors:
            _logger.warning("%d/%d line fetches failed", errors, len(tasks))
        if not frames:
            return self._decorate_rides(pd.DataFrame())
        return self._decorate_rides(pd.concat(frames, ignore_index=True))

    def fetch_siri(
        self,
        date_from: str,
        date_to: str,
        *,
        operator_ref: int | str | None = None,
        line_ref: int | str | None = None,
        limit: int = 15000,
    ) -> pd.DataFrame:
        """Fetch real-time execution rows from ``/siri_rides/list``.

        SIRI is the *actual performance* feed (vehicle positions / matched rides),
        as opposed to the GTFS *planning* feed. Use this for punctuality once the
        relevant SIRI fields are available for your window. ``operator_ref=None``
        means all operators.
        """
        return self.get_df(
            config.ENDPOINT_SIRI_RIDES,
            siri_route__operator_ref=str(operator_ref) if operator_ref is not None else None,
            siri_route__line_ref=str(line_ref) if line_ref is not None else None,
            scheduled_start_time_from=f"{date_from}T00:00:00+00:00",
            scheduled_start_time_to=f"{date_to}T23:59:59+00:00",
            limit=limit,
        )

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #
    def _fetch_rides_for_line(
        self,
        operator_ref: int | str | None,
        line_ref: int | str,
        date_from: str,
        date_to: str,
    ) -> pd.DataFrame:
        rows = self.get_df(
            config.ENDPOINT_RIDES_EXECUTION,
            date_from=date_from,
            date_to=date_to,
            operator_ref=str(operator_ref) if operator_ref is not None else None,
            line_ref=str(line_ref),
            order_by="planned_start_time asc",
            limit=15000,
        )
        return rows

    @staticmethod
    def _decorate_rides(frame: pd.DataFrame) -> pd.DataFrame:
        """Add derived ``service_date`` and ``executed`` columns."""
        expected = ["operator_ref", "line_ref", "planned_start_time", "actual_start_time"]
        for col in expected:
            if col not in frame.columns:
                frame[col] = pd.NA
        if frame.empty:
            frame["service_date"] = pd.Series(dtype="object")
            frame["executed"] = pd.Series(dtype="bool")
            return frame
        planned = pd.to_datetime(frame["planned_start_time"], utc=True, errors="coerce")
        frame["service_date"] = (
            planned.dt.tz_convert(config.LOCAL_TZ).dt.strftime("%Y-%m-%d")
        )
        frame["executed"] = frame["actual_start_time"].notna() & (
            frame["actual_start_time"].astype(str).str.len() > 0
        )
        return frame

    def _resolve_dates(
        self, date: str | None, date_from: str | None, date_to: str | None
    ) -> tuple[str, str]:
        if date:
            return date, date
        if date_from and date_to:
            return date_from, date_to
        if date_from:
            return date_from, date_from
        # Default: yesterday in local time, as a single service date.
        ref = pd.Timestamp.now(tz=config.LOCAL_TZ).normalize() - pd.Timedelta(days=1)
        day = ref.strftime("%Y-%m-%d")
        return day, day

    def _request_with_retries(
        self, path: str, params: dict[str, Any]
    ) -> list[dict[str, Any]] | dict[str, Any]:
        url = f"{self.base_url.rstrip('/')}{path}"
        last_error: StrideError | None = None
        for attempt in range(1, self.max_retries + 2):
            self._respect_rate_limit()
            started = time.monotonic()
            try:
                response = self._session.get(url, params=params, timeout=self.timeout_seconds)
            except requests.exceptions.RequestException as exc:
                meta = self._record(
                    path=path, url=url, params=params, attempt=attempt,
                    status_code=None, started=started,
                    error_type=type(exc).__name__, error_message=str(exc),
                )
                last_error = StrideRequestError(
                    f"Request failed for {path}: {exc}", metadata=meta
                )
                if self._should_retry(attempt, None):
                    self._sleep_before_retry(attempt)
                    continue
                raise last_error from exc

            meta = self._record(
                path=path, url=url, params=params, attempt=attempt,
                status_code=response.status_code, started=started,
            )
            if response.status_code >= 400:
                last_error = StrideHTTPError(
                    f"HTTP {response.status_code} for {path}: {response.text[:160]}",
                    metadata=meta,
                )
                if self._should_retry(attempt, response.status_code):
                    self._sleep_before_retry(attempt)
                    continue
                raise last_error

            try:
                return response.json()
            except ValueError as exc:
                raise StrideResponseError(
                    f"Invalid JSON from {path}", metadata=meta
                ) from exc

        if last_error is not None:
            raise last_error
        raise StrideRequestError(f"Request failed for {path}")

    @staticmethod
    def _coerce_records(
        payload: list[dict[str, Any]] | dict[str, Any], path: str
    ) -> list[dict[str, Any]]:
        if isinstance(payload, list):
            return payload
        if isinstance(payload, dict):
            return [payload]
        raise StrideResponseError(f"Unsupported payload type from {path}: {type(payload)}")

    def _respect_rate_limit(self) -> None:
        if self.rate_limit_seconds <= 0:
            return
        wait = self.rate_limit_seconds - (time.monotonic() - self._last_request_at)
        if wait > 0:
            time.sleep(wait)
        self._last_request_at = time.monotonic()

    def _should_retry(self, attempt: int, status_code: int | None) -> bool:
        if attempt > self.max_retries:
            return False
        if status_code is None:
            return True
        return status_code in RETRYABLE_STATUS_CODES

    def _sleep_before_retry(self, attempt: int) -> None:
        if self.retry_backoff_seconds > 0:
            time.sleep(self.retry_backoff_seconds * (2 ** (attempt - 1)))

    @staticmethod
    def _normalize_path(path: str) -> str:
        return path if path.startswith("/") else f"/{path}"

    def _record(
        self,
        *,
        path: str,
        url: str,
        params: dict[str, Any],
        attempt: int,
        status_code: int | None,
        started: float,
        from_cache: bool = False,
        error_type: str | None = None,
        error_message: str | None = None,
    ) -> RequestMetadata:
        meta = RequestMetadata(
            method="GET",
            url=url,
            path=path,
            params=dict(params),
            attempt=attempt,
            status_code=status_code,
            elapsed_seconds=round(time.monotonic() - started, 4),
            timestamp_utc=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            from_cache=from_cache,
            error_type=error_type,
            error_message=error_message,
        )
        self.request_log.append(meta)
        return meta
