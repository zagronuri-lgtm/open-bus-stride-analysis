from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from src.ridership_fetch import (
    CkanResource,
    _check_for_update,
    _default_output_path,
    _fetch_all_records,
    _normalize_records,
    _select_latest_resource,
)


class FakeResponse:
    def __init__(self, payload: dict, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self) -> dict:
        return self._payload


class FakeSession:
    def __init__(self, pages: list[dict]) -> None:
        self.pages = pages
        self.calls: list[dict] = []

    def get(self, url: str, *, params: dict, timeout: int) -> FakeResponse:
        self.calls.append({"url": url, "params": params, "timeout": timeout})
        page_index = len(self.calls) - 1
        return FakeResponse(self.pages[page_index])


def test_select_latest_resource_uses_latest_active_csv() -> None:
    resources = [
        {
            "id": "old",
            "name": "קובץ נסועה בקווי אוטובוס שנת 2025",
            "format": "CSV",
            "metadata_modified": "2026-01-01T00:00:00",
            "datastore_active": True,
        },
        {
            "id": "pdf",
            "name": "מידע על קובץ הנסועה",
            "format": "PDF",
            "metadata_modified": "2026-06-01T00:00:00",
            "datastore_active": False,
        },
        {
            "id": "new",
            "name": "קובץ נסועה בקווי אוטובוס שנת 2026",
            "format": "CSV",
            "metadata_modified": "2026-06-16T11:04:47.602523",
            "datastore_active": True,
        },
    ]

    selected = _select_latest_resource(resources)

    assert selected.id == "new"
    assert selected.metadata_modified == "2026-06-16T11:04:47.602523"


def test_fetch_all_records_paginates_until_total() -> None:
    session = FakeSession(
        [
            {
                "success": True,
                "result": {
                    "records": [{"RouteID": 1}, {"RouteID": 2}],
                    "total": 3,
                },
            },
            {
                "success": True,
                "result": {
                    "records": [{"RouteID": 3}],
                    "total": 3,
                },
            },
        ]
    )

    records = _fetch_all_records("resource-1", session=session, page_limit=2)

    assert records == [{"RouteID": 1}, {"RouteID": 2}, {"RouteID": 3}]
    assert [call["params"]["offset"] for call in session.calls] == [0, 2]


def test_normalize_records_renames_observed_schema_to_stable_columns() -> None:
    df = _normalize_records(
        [
            {
                "_id": 1,
                "RouteID": 10003,
                "RouteName": 3,
                "RouteDirection": 1,
                "AgencyName": "אגד",
                "ClusterName": "חיפה עירוני",
                "DailyRides(Tuesday)": 2,
                "ימי חול - 06:00-08:59": 43.1,
                "שישי - 12:00-14:59": 7.2,
                "שבת - 19:00-23:59": None,
                "המלצה": "ללא שינוי",
                "year": 2026,
                "Q": "2",
            }
        ]
    )

    assert list(df.columns) == [
        "_id",
        "route_id",
        "route_short_name",
        "route_direction",
        "agency_name",
        "cluster_name",
        "daily_rides_tuesday",
        "weekday_0600_0859",
        "friday_1200_1459",
        "saturday_1900_2359",
        "recommendation",
        "year",
        "quarter",
    ]
    assert df.loc[0, "route_short_name"] == 3
    assert df.loc[0, "weekday_0600_0859"] == 43.1


def test_check_for_update_compares_cached_resource_identity(tmp_path: Path) -> None:
    cache_path = tmp_path / "ridership_meta.json"
    resource = CkanResource(
        id="resource-1",
        name="קובץ נסועה בקווי אוטובוס שנת 2026",
        metadata_modified="2026-06-16T11:04:47.602523",
    )

    assert _check_for_update(resource, cache_path) is True

    cache_path.write_text(
        json.dumps(
            {
                "resource_id": "resource-1",
                "metadata_modified": "2026-06-16T11:04:47.602523",
            }
        ),
        encoding="utf-8",
    )

    assert _check_for_update(resource, cache_path) is False

    changed = CkanResource(
        id="resource-1",
        name="קובץ נסועה בקווי אוטובוס שנת 2026",
        metadata_modified="2026-07-01T00:00:00",
    )
    assert _check_for_update(changed, cache_path) is True


def test_default_output_path_uses_latest_year_and_quarter(tmp_path: Path) -> None:
    df = pd.DataFrame({"year": [2026, 2026, 2025], "quarter": ["1", "2", "4"]})

    assert _default_output_path(df, tmp_path) == tmp_path / "ridership_2026Q2.csv"
