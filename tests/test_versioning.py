import os

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


def test_detect_install_method_pipx_via_path(mocker):
    mocker.patch("caddy_tui.versioning.sys.executable", "/home/user/.local/pipx/venvs/caddy-tui/bin/python")
    mocker.patch.dict(os.environ, {"PIPX_HOME": ""}, clear=False)
    assert versioning.detect_install_method() == "pipx"


def test_detect_install_method_pipx_via_env(mocker):
    mocker.patch("caddy_tui.versioning.sys.executable", "/opt/pipx/venvs/caddy-tui/bin/python")
    mocker.patch.dict(os.environ, {"PIPX_HOME": "/opt/pipx"}, clear=False)
    assert versioning.detect_install_method() == "pipx"


def test_detect_install_method_venv(mocker):
    mocker.patch("caddy_tui.versioning.sys.executable", "/home/user/project/.venv/bin/python")
    mocker.patch.dict(os.environ, {"PIPX_HOME": ""}, clear=False)
    mocker.patch.object(versioning.sys, "prefix", "/home/user/project/.venv")
    mocker.patch.object(versioning.sys, "base_prefix", "/usr")
    assert versioning.detect_install_method() == "venv"


def test_detect_install_method_system(mocker):
    mocker.patch("caddy_tui.versioning.sys.executable", "/usr/bin/python3")
    mocker.patch.dict(os.environ, {"PIPX_HOME": ""}, clear=False)
    mocker.patch.object(versioning.sys, "prefix", "/usr")
    mocker.patch.object(versioning.sys, "base_prefix", "/usr")
    assert versioning.detect_install_method() == "system"


def test_is_externally_managed_true(mocker, tmp_path):
    stdlib_path = tmp_path / "python3.12"
    stdlib_path.mkdir()
    (stdlib_path / "EXTERNALLY-MANAGED").touch()
    mocker.patch("caddy_tui.versioning.sysconfig.get_path", return_value=str(stdlib_path))
    assert versioning.is_externally_managed() is True


def test_is_externally_managed_false(mocker, tmp_path):
    stdlib_path = tmp_path / "python3.12"
    stdlib_path.mkdir()
    mocker.patch("caddy_tui.versioning.sysconfig.get_path", return_value=str(stdlib_path))
    assert versioning.is_externally_managed() is False


def test_get_upgrade_instructions_pipx(mocker):
    mocker.patch("caddy_tui.versioning.detect_install_method", return_value="pipx")
    instructions = versioning.get_upgrade_instructions()
    assert "pipx upgrade caddy-tui" in instructions
    assert "detected pipx installation" in instructions


def test_get_upgrade_instructions_venv(mocker):
    mocker.patch("caddy_tui.versioning.detect_install_method", return_value="venv")
    instructions = versioning.get_upgrade_instructions()
    assert "pip install --upgrade caddy-tui" in instructions
    assert "detected virtual environment" in instructions


def test_get_upgrade_instructions_system_externally_managed(mocker):
    mocker.patch("caddy_tui.versioning.detect_install_method", return_value="system")
    mocker.patch("caddy_tui.versioning.is_externally_managed", return_value=True)
    instructions = versioning.get_upgrade_instructions()
    assert "pipx upgrade caddy-tui" in instructions
    assert "externally managed" in instructions.lower()


def test_get_upgrade_instructions_system_not_managed(mocker):
    mocker.patch("caddy_tui.versioning.detect_install_method", return_value="system")
    mocker.patch("caddy_tui.versioning.is_externally_managed", return_value=False)
    instructions = versioning.get_upgrade_instructions()
    assert "pip install --upgrade caddy-tui" in instructions
    assert "pipx upgrade caddy-tui" in instructions