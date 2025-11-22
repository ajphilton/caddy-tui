"""Integration helpers for invoking the caddy binary."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from shutil import which
from typing import Any

from .config import AppPaths, CADDY_BIN


class CaddyError(RuntimeError):
    pass


def _caddy_bin(paths: AppPaths | None = None) -> str:
    configured = (paths.caddy_bin if paths else None) or CADDY_BIN
    candidate = configured or which("caddy")
    if not candidate:
        raise CaddyError("Unable to locate caddy binary. Set CADDY_TUI_CADDY_BIN.")
    return candidate


def adapt_caddyfile(path: Path, *, paths: AppPaths | None = None) -> dict[str, Any]:
    bin_path = _caddy_bin(paths)
    proc = subprocess.run(
        [bin_path, "adapt", "--config", str(path), "--adapter", "caddyfile", "--pretty"],
        check=False,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise CaddyError(proc.stderr.strip() or "caddy adapt failed")
    return json.loads(proc.stdout)


def validate_config(config_path: Path, fmt: str = "caddyfile", *, paths: AppPaths | None = None) -> None:
    bin_path = _caddy_bin(paths)
    cmd = [bin_path, "validate", "--config", str(config_path)]
    if fmt == "caddyfile":
        cmd.extend(["--adapter", "caddyfile"])
    proc = subprocess.run(cmd, check=False, capture_output=True, text=True)
    if proc.returncode != 0:
        raise CaddyError(proc.stderr.strip() or "caddy validate failed")


def reload_caddy(config_path: Path, fmt: str = "caddyfile", *, paths: AppPaths | None = None) -> None:
    bin_path = _caddy_bin(paths)
    cmd = [bin_path, "reload", "--config", str(config_path)]
    if fmt == "caddyfile":
        cmd.extend(["--adapter", "caddyfile"])
    elif fmt == "json":
        cmd.extend(["--adapter", "json"])
    proc = subprocess.run(cmd, check=False, capture_output=True, text=True)
    if proc.returncode != 0:
        raise CaddyError(proc.stderr.strip() or "caddy reload failed")
