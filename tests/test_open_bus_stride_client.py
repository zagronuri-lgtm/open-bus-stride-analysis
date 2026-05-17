from src.open_bus_stride_client import OpenBusStrideClient


def test_client_builds_base_url():
    client = OpenBusStrideClient()
    assert client.base_url.startswith("https://open-bus-stride-api")


def test_client_normalizes_path(monkeypatch):
    captured = {}

    class DummyResponse:
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
