from caddy_tui import versioning


class _DummyResponse:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return b'{"tag_name": "v0.2.0"}'


def test_fetch_latest_version_parses_tag(mocker):
    mocker.patch("urllib.request.urlopen", return_value=_DummyResponse())
    latest = versioning.fetch_latest_version(repo="ajphilton/caddy-tui")
    assert latest == "0.2.0"


def test_collect_version_info_handles_missing_remote(mocker):
    mocker.patch("caddy_tui.versioning.fetch_latest_version", return_value=None)
    info = versioning.collect_version_info()
    assert info.current == versioning.__version__
    assert info.latest is None
    assert info.update_available is False