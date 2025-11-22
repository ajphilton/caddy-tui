"""Application configuration helpers."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os

APP_DIR = Path(os.environ.get("CADDY_TUI_HOME", Path.home() / ".caddy-tui"))
DB_PATH = Path(os.environ.get("CADDY_TUI_DB", APP_DIR / "config.db"))
GENERATED_CADDYFILE = Path(
    os.environ.get("CADDY_TUI_GENERATED_CADDYFILE", "/etc/caddy/Caddyfile.generated")
)
GENERATED_JSON = Path(
    os.environ.get("CADDY_TUI_GENERATED_JSON", "/etc/caddy/caddy-tui.json")
)
CADDY_BIN = os.environ.get("CADDY_TUI_CADDY_BIN")
RELOAD_MODE = os.environ.get("CADDY_TUI_RELOAD_MODE", "caddy")


@dataclass(slots=True)
class AppPaths:
    db_path: Path = DB_PATH
    generated_caddyfile: Path = GENERATED_CADDYFILE
    generated_json: Path = GENERATED_JSON
    caddy_bin: str | None = CADDY_BIN
    reload_mode: str = RELOAD_MODE


def ensure_app_dir(path: Path | None = None) -> Path:
    """Ensure the data directory exists and return it."""
    target = path or APP_DIR
    target.mkdir(parents=True, exist_ok=True)
    return target
