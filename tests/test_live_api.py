from caddy_tui.live_api import fetch_live_status


class DummyResponse:
    def __init__(self, payload: str, content_type: str):
        self._payload = payload
        self.headers = {"Content-Type": content_type}

    def read(self) -> bytes:
        return self._payload.encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def test_fetch_live_status_returns_caddyfile(monkeypatch):
    def fake_urlopen(request, timeout=0):  # noqa: ARG001 - signature mimics urlopen
        return DummyResponse("example.com {\n    respond \"ok\"\n}\n", "text/caddyfile")

    monkeypatch.setattr("caddy_tui.live_api.urlopen", fake_urlopen)
    status = fetch_live_status("http://admin/config")
    assert status is not None
    assert status.state == "live"
    assert status.block_count == 1
    assert status.caddyfile_text is not None
    assert status.json_payload is None


def test_fetch_live_status_counts_json_routes(monkeypatch):
    payload = """
    {
        "apps": {
            "http": {
                "servers": {
                    "srv0": {"routes": [{}, {}]}
                }
            }
        }
    }
    """

    def fake_urlopen(request, timeout=0):  # noqa: ARG001 - signature mimics urlopen
        return DummyResponse(payload, "application/json")

    monkeypatch.setattr("caddy_tui.live_api.urlopen", fake_urlopen)
    status = fetch_live_status("http://admin/config")
    assert status is not None
    assert status.block_count == 2
    assert status.caddyfile_text is None
    assert status.json_payload is not None
