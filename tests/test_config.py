from types import SimpleNamespace
from pathlib import Path

from caddy_tui import config


def test_determine_home_prefers_sudo_user(monkeypatch, tmp_path):
    monkeypatch.setenv("SUDO_USER", "dummy")
    monkeypatch.setattr(
        config,
        "pwd",
        SimpleNamespace(getpwnam=lambda user: SimpleNamespace(pw_dir=str(tmp_path))),
        raising=False,
    )
    assert config._determine_home() == tmp_path


def test_determine_home_defaults_to_path_home(monkeypatch):
    monkeypatch.delenv("SUDO_USER", raising=False)
    expected = Path.home()
    assert config._determine_home() == expected
