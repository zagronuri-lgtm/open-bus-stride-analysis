import requests

from src.open_bus_stride_client import (
    OpenBusStrideClient,
    OpenBusStrideHTTPError,
    OpenBusStrideRequestError,
    find_line_refs,
)


def test_client_builds_base_url():
    client = OpenBusStrideClient()
    assert client.base_url.startswith("https://open-bus-stride-api")


def test_client_normalizes_path(monkeypatch):
    captured = {}

    class DummyResponse:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return []

    def fake_get(url, params, timeout):
        captured["url"] = url
        captured["params"] = params
        captured["timeout"] = timeout
        return DummyResponse()

    monkeypatch.setattr("requests.get", fake_get)
    client = OpenBusStrideClient(timeout_seconds=7)
    client.get_json("gtfs_routes/list", limit=1)
    assert captured["url"].endswith("/gtfs_routes/list")
    assert captured["params"]["limit"] == 1
    assert captured["timeout"] == 7
    assert client.last_request_metadata is not None
    assert client.last_request_metadata.path == "/gtfs_routes/list"
    assert client.last_request_metadata.status_code == 200


def test_client_retries_request_errors(monkeypatch):
    calls = {"count": 0}

    class DummyResponse:
        status_code = 200

        def json(self):
            return [{"ok": True}]

    def fake_get(url, params, timeout):
        calls["count"] += 1
        if calls["count"] == 1:
            raise requests.exceptions.Timeout("slow")
        return DummyResponse()

    monkeypatch.setattr("requests.get", fake_get)
    client = OpenBusStrideClient(max_retries=1, retry_backoff_seconds=0)

    assert client.get_json("/ok") == [{"ok": True}]
    assert calls["count"] == 2
    assert len(client.request_log) == 2
    assert client.request_log[0].error_type == "Timeout"


def test_client_raises_http_error_with_metadata(monkeypatch):
    class DummyResponse:
        status_code = 422

        def json(self):
            return {"detail": "bad param"}

    monkeypatch.setattr("requests.get", lambda url, params, timeout: DummyResponse())
    client = OpenBusStrideClient(max_retries=0, retry_backoff_seconds=0)

    try:
        client.get_json("/bad", limit=1)
    except OpenBusStrideHTTPError as exc:
        assert exc.metadata is not None
        assert exc.metadata.status_code == 422
        assert exc.metadata.params == {"limit": 1}
    else:
        raise AssertionError("Expected HTTP error")


def test_client_raises_request_error_after_retries(monkeypatch):
    def fake_get(url, params, timeout):
        raise requests.exceptions.ConnectionError("offline")

    monkeypatch.setattr("requests.get", fake_get)
    client = OpenBusStrideClient(max_retries=1, retry_backoff_seconds=0)

    try:
        client.get_json("/offline")
    except OpenBusStrideRequestError as exc:
        assert exc.metadata is not None
        assert exc.metadata.attempt == 2
        assert len(client.request_log) == 2
    else:
        raise AssertionError("Expected request error")


def test_find_line_refs_passes_direction_alternative_and_mkt():
    captured = {}

    class DummyClient(OpenBusStrideClient):
        def get_df(self, path, **params):
            captured["path"] = path
            captured["params"] = params
            import pandas as pd

            return pd.DataFrame()

    find_line_refs(
        DummyClient(),
        service_date="2026-05-15",
        operator_ref="3",
        route_short_name="18",
        route_mkt="10018",
        route_direction=1,
        route_alternative="#",
    )

    assert captured["path"] == "/gtfs_routes/list"
    assert captured["params"]["route_mkt"] == "10018"
    assert captured["params"]["route_direction"] == "1"
    assert captured["params"]["route_alternative"] == "#"
