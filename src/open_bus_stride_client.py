"""Minimal Open Bus Stride API client.

This module is intentionally small and safe: it centralizes timeouts,
parameter handling, pagination, and DataFrame conversion.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

import pandas as pd
import requests

DEFAULT_BASE_URL = "https://open-bus-stride-api.hasadna.org.il"


@dataclass(frozen=True)
class OpenBusStrideClient:
    """Small HTTP client for Open Bus Stride API."""

    base_url: str = DEFAULT_BASE_URL
    timeout_seconds: int = 60

    def get_json(self, path: str, **params: Any) -> list[dict[str, Any]] | dict[str, Any]:
        """Fetch JSON from a Stride endpoint.

        Args:
            path: Endpoint path, e.g. `/gtfs_routes/list`.
            **params: Query parameters.

        Returns:
            Parsed JSON payload.
        """
        if not path.startswith("/"):
            path = f"/{path}"
        response = requests.get(
            f"{self.base_url}{path}", params=params, timeout=self.timeout_seconds
        )
        response.raise_for_status()
        return response.json()

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


def find_line_refs(
    client: OpenBusStrideClient,
    *,
    service_date: str,
    operator_ref: int | str,
    route_short_name: str,
) -> pd.DataFrame:
    """Find line_ref candidates for a public route number on a service date."""
    return client.get_df(
        "/gtfs_routes/list",
        date_from=service_date,
        date_to=service_date,
        operator_refs=str(operator_ref),
        route_short_name=route_short_name,
        order_by="line_ref asc",
    )
