"""Application configuration helpers."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os

try:  # pragma: no cover - pwd isn't available on Windows
    import pwd
except ImportError:  # pragma: no cover
    pwd = None


def _determine_home() -> Path:
    sudo_user = os.environ.get("SUDO_USER")
    if sudo_user and pwd:
        try:
            return Path(pwd.getpwnam(sudo_user).pw_dir)
        except KeyError:  # pragma: no cover - only when user missing from passwd
            pass
    return Path.home()

APP_DIR = Path(os.environ.get("CADDY_TUI_HOME", _determine_home() / ".caddy-tui"))
DB_PATH = Path(os.environ.get("CADDY_TUI_DB", APP_DIR / "config.db"))
CACHE_DIR = Path(os.environ.get("CADDY_TUI_CACHE", APP_DIR / "cache"))
HELPER_SOCKET = Path(os.environ.get("CADDY_TUI_HELPER_SOCKET", APP_DIR / "helper.sock"))
GENERATED_CADDYFILE = Path(
    os.environ.get("CADDY_TUI_GENERATED_CADDYFILE", "/etc/caddy/Caddyfile.generated")
)
GENERATED_JSON = Path(
    os.environ.get("CADDY_TUI_GENERATED_JSON", "/etc/caddy/caddy-tui.json")
)
CADDY_BIN = os.environ.get("CADDY_TUI_CADDY_BIN")
RELOAD_MODE = os.environ.get("CADDY_TUI_RELOAD_MODE", "caddy")
LIVE_CADDYFILE = (
    Path(os.environ["CADDY_TUI_LIVE_CADDYFILE"]).expanduser()
    if os.environ.get("CADDY_TUI_LIVE_CADDYFILE")
    else None
)
CADDY_ADMIN_ENDPOINT = os.environ.get("CADDY_TUI_ADMIN_ENDPOINT", "http://127.0.0.1:2019/config")
CADDY_ADMIN_TIMEOUT = float(os.environ.get("CADDY_TUI_ADMIN_TIMEOUT", "2.5"))


@dataclass(slots=True)
class AppPaths:
    db_path: Path = DB_PATH
    generated_caddyfile: Path = GENERATED_CADDYFILE
    generated_json: Path = GENERATED_JSON
    caddy_bin: str | None = CADDY_BIN
    reload_mode: str = RELOAD_MODE
    live_caddyfile: Path | None = LIVE_CADDYFILE


def ensure_app_dir(path: Path | None = None) -> Path:
    """Ensure the data directory exists and return it."""
    target = path or APP_DIR
    target.mkdir(parents=True, exist_ok=True)
    return target


def ensure_cache_dir() -> Path:
    root = ensure_app_dir()
    cache = CACHE_DIR
    cache.mkdir(parents=True, exist_ok=True)
    return cache
