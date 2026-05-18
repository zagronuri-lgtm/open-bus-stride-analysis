"""Open Bus Stride API client with retries and request metadata."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import time
from typing import Any, Iterable

import pandas as pd
import requests

DEFAULT_BASE_URL = "https://open-bus-stride-api.hasadna.org.il"

RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


@dataclass(frozen=True)
class RequestMetadata:
    """Metadata for one HTTP request attempt."""

    method: str
    url: str
    path: str
    params: dict[str, Any]
    attempt: int
    status_code: int | None
    elapsed_seconds: float
    timestamp_utc: str
    error_type: str | None = None
    error_message: str | None = None


class OpenBusStrideError(RuntimeError):
    """Base error for Open Bus Stride client failures."""

    def __init__(self, message: str, *, metadata: RequestMetadata | None = None) -> None:
        super().__init__(message)
        self.metadata = metadata


class OpenBusStrideRequestError(OpenBusStrideError):
    """Network, timeout, or transport-level request failure."""


class OpenBusStrideHTTPError(OpenBusStrideError):
    """Non-success HTTP response from the API."""


class OpenBusStrideResponseError(OpenBusStrideError):
    """Unexpected or invalid API response payload."""


@dataclass
class OpenBusStrideClient:
    """Small HTTP client for Open Bus Stride API.

    The client stores per-attempt request metadata in ``request_log`` so
    analytical outputs can include URL, params, status, timing, and errors.
    """

    base_url: str = DEFAULT_BASE_URL
    timeout_seconds: int = 60
    max_retries: int = 2
    retry_backoff_seconds: float = 0.5
    request_log: list[RequestMetadata] = field(default_factory=list)

    @property
    def last_request_metadata(self) -> RequestMetadata | None:
        """Return metadata for the latest request attempt, if any."""
        return self.request_log[-1] if self.request_log else None

    def get_json(self, path: str, **params: Any) -> list[dict[str, Any]] | dict[str, Any]:
        """Fetch JSON from a Stride endpoint.

        Args:
            path: Endpoint path, e.g. `/gtfs_routes/list`.
            **params: Query parameters.

        Returns:
            Parsed JSON payload.
        """
        normalized_path = self._normalize_path(path)
        url = f"{self.base_url.rstrip('/')}{normalized_path}"
        clean_params = {key: value for key, value in params.items() if value is not None}

        last_error: OpenBusStrideError | None = None
        for attempt in range(1, self.max_retries + 2):
            started = time.monotonic()
            try:
                response = requests.get(url, params=clean_params, timeout=self.timeout_seconds)
            except requests.exceptions.RequestException as exc:
                metadata = self._record_metadata(
                    path=normalized_path,
                    url=url,
                    params=clean_params,
                    attempt=attempt,
                    status_code=None,
                    started=started,
                    error_type=exc.__class__.__name__,
                    error_message=str(exc),
                )
                last_error = OpenBusStrideRequestError(
                    f"Open Bus Stride request failed for {normalized_path}: {exc}",
                    metadata=metadata,
                )
                if self._should_retry(attempt, status_code=None):
                    self._sleep_before_retry(attempt)
                    continue
                raise last_error from exc

            metadata = self._record_metadata(
                path=normalized_path,
                url=url,
                params=clean_params,
                attempt=attempt,
                status_code=response.status_code,
                started=started,
            )

            if response.status_code >= 400:
                last_error = OpenBusStrideHTTPError(
                    f"Open Bus Stride returned HTTP {response.status_code} for {normalized_path}",
                    metadata=metadata,
                )
                if self._should_retry(attempt, status_code=response.status_code):
                    self._sleep_before_retry(attempt)
                    continue
                raise last_error

            try:
                payload = response.json()
            except ValueError as exc:
                raise OpenBusStrideResponseError(
                    f"Open Bus Stride returned invalid JSON for {normalized_path}",
                    metadata=metadata,
                ) from exc
            if not isinstance(payload, (list, dict)):
                raise OpenBusStrideResponseError(
                    f"Open Bus Stride returned unsupported JSON payload for {normalized_path}",
                    metadata=metadata,
                )
            return payload

        if last_error is not None:
            raise last_error
        raise OpenBusStrideRequestError(f"Open Bus Stride request failed for {normalized_path}")

    def get_df(self, path: str, **params: Any) -> pd.DataFrame:
        """Fetch an endpoint and return a DataFrame."""
        params.setdefault("limit", 15000)
        payload = self.get_json(path, **params)
        if isinstance(payload, dict):
            return pd.DataFrame([payload])
        return pd.DataFrame(payload)

    def paginated(
        self, path: str, page_size: int = 5000, max_pages: int | None = None, **params: Any
    ) -> Iterable[list[dict[str, Any]]]:
        """Yield pages using limit/offset pagination."""
        page = 0
        while max_pages is None or page < max_pages:
            payload = self.get_json(
                path, **params, limit=page_size, offset=page * page_size
            )
            if not isinstance(payload, list):
                raise TypeError("Expected list payload for paginated endpoint")
            if not payload:
                break
            yield payload
            if len(payload) < page_size:
                break
            page += 1

    def _normalize_path(self, path: str) -> str:
        if not path.startswith("/"):
            return f"/{path}"
        return path

    def _record_metadata(
        self,
        *,
        path: str,
        url: str,
        params: dict[str, Any],
        attempt: int,
        status_code: int | None,
        started: float,
        error_type: str | None = None,
        error_message: str | None = None,
    ) -> RequestMetadata:
        metadata = RequestMetadata(
            method="GET",
            url=url,
            path=path,
            params=dict(params),
            attempt=attempt,
            status_code=status_code,
            elapsed_seconds=round(time.monotonic() - started, 4),
            timestamp_utc=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            error_type=error_type,
            error_message=error_message,
        )
        self.request_log.append(metadata)
        return metadata

    def _should_retry(self, attempt: int, *, status_code: int | None) -> bool:
        if attempt > self.max_retries:
            return False
        if status_code is None:
            return True
        return status_code in RETRYABLE_STATUS_CODES

    def _sleep_before_retry(self, attempt: int) -> None:
        if self.retry_backoff_seconds <= 0:
            return
        time.sleep(self.retry_backoff_seconds * (2 ** (attempt - 1)))


def find_line_refs(
    client: OpenBusStrideClient,
    *,
    service_date: str,
    operator_ref: int | str,
    route_short_name: str,
    route_direction: int | str | None = None,
    route_alternative: str | None = None,
    route_mkt: str | None = None,
) -> pd.DataFrame:
    """Find line_ref candidates for a public route number on a service date."""
    return client.get_df(
        "/gtfs_routes/list",
        date_from=service_date,
        date_to=service_date,
        operator_refs=str(operator_ref),
        route_short_name=route_short_name,
        route_direction=str(route_direction) if route_direction is not None else None,
        route_alternative=route_alternative,
        route_mkt=route_mkt,
        order_by="line_ref asc",
    )


def _request_metadata_to_dict(metadata: RequestMetadata | None) -> dict[str, Any] | None:
    """Convert request metadata to a serializable dictionary."""
    if metadata is None:
        return None
    return {
        "method": metadata.method,
        "url": metadata.url,
        "path": metadata.path,
        "params": metadata.params,
        "attempt": metadata.attempt,
        "status_code": metadata.status_code,
        "elapsed_seconds": metadata.elapsed_seconds,
        "timestamp_utc": metadata.timestamp_utc,
        "error_type": metadata.error_type,
        "error_message": metadata.error_message,
    }
