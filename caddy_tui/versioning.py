"""Version discovery and tracking utilities."""
from __future__ import annotations

import json
import os
import sys
import sysconfig
import urllib.request
from dataclasses import dataclass
from typing import Literal, Optional

from packaging.version import InvalidVersion, Version

from . import __version__
from pathlib import Path

from .db import session_scope
from . import models

DEFAULT_REPO = "ajphilton/caddy-tui"

InstallMethod = Literal["pipx", "venv", "system"]


@dataclass(slots=True)
class VersionInfo:
    current: str
    latest: str | None
    update_available: bool
    source: str


def _normalize(version: str) -> Version | None:
    value = version.lstrip("v")
    try:
        return Version(value)
    except InvalidVersion:
        return None


def fetch_latest_version(repo: str | None = None, timeout: int = 5) -> str | None:
    """Query GitHub releases for the latest version tag."""
    repository = repo or os.environ.get("CADDY_TUI_REPO", DEFAULT_REPO)
    url = f"https://api.github.com/repos/{repository}/releases/latest"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None
    tag = payload.get("tag_name") or payload.get("name")
    if not tag:
        return None
    normalized = _normalize(tag)
    return normalized.public if normalized else None


def collect_version_info(repo: str | None = None) -> VersionInfo:
    current = __version__
    latest = fetch_latest_version(repo)
    current_v = _normalize(current)
    latest_v = _normalize(latest) if latest else None
    update_available = bool(latest_v and current_v and latest_v > current_v)
    return VersionInfo(current=current, latest=latest, update_available=update_available, source=repo or os.environ.get("CADDY_TUI_REPO", DEFAULT_REPO))


def store_current_version(version: str | None = None, db_path: Path | str | None = None) -> None:
    value = version or __version__
    with session_scope(db_path=db_path) as session:
        try:
            meta = session.get(models.Meta, "app_version")
        except Exception:
            return
        if meta:
            meta.value = value
        else:
            session.add(models.Meta(key="app_version", value=value))


def detect_install_method() -> InstallMethod:
    """Detect how caddy-tui was installed to provide appropriate upgrade instructions.
    
    Returns:
        "pipx" if installed via pipx (executable in pipx venvs directory)
        "venv" if running in a virtual environment
        "system" if installed in system Python (may be externally managed)
    """
    exe = sys.executable
    
    # Check if running in a pipx environment by looking for pipx venvs structure
    # pipx creates venvs in paths like ~/.local/pipx/venvs/<package>/
    # or in $PIPX_HOME/venvs/<package>/
    pipx_home = os.environ.get("PIPX_HOME", "")
    if pipx_home and exe.startswith(pipx_home):
        return "pipx"
    if "/pipx/venvs/" in exe or "\\pipx\\venvs\\" in exe:
        return "pipx"
    
    # Check if in a virtual environment
    in_venv = hasattr(sys, "real_prefix") or (
        hasattr(sys, "base_prefix") and sys.base_prefix != sys.prefix
    )
    if in_venv:
        return "venv"
    
    return "system"


def is_externally_managed() -> bool:
    """Check if the Python environment is externally managed (PEP 668).
    
    Modern Linux distributions mark system Python as externally managed
    to prevent package conflicts. In such environments, pip install
    will fail without --break-system-packages.
    """
    stdlib = sysconfig.get_path("stdlib")
    if stdlib:
        marker_path = Path(stdlib) / "EXTERNALLY-MANAGED"
        return marker_path.exists()
    return False


def get_upgrade_command() -> str:
    """Get the appropriate upgrade command based on installation method."""
    method = detect_install_method()
    if method == "pipx":
        return "pipx upgrade caddy-tui"
    elif method == "venv":
        return "pip install --upgrade caddy-tui"
    else:
        # System Python - check if externally managed
        if is_externally_managed():
            return "pipx upgrade caddy-tui"
        return "pip install --upgrade caddy-tui"


def get_upgrade_instructions() -> str:
    """Get detailed upgrade instructions based on installation method.
    
    Returns a formatted string with upgrade command(s) and helpful notes.
    """
    method = detect_install_method()
    
    if method == "pipx":
        return (
            "pipx upgrade caddy-tui\n\n"
            "(detected pipx installation)"
        )
    
    if method == "venv":
        return (
            "pip install --upgrade caddy-tui\n\n"
            "(detected virtual environment)"
        )
    
    # System Python installation
    if is_externally_managed():
        return (
            "pipx upgrade caddy-tui\n\n"
            "Your system Python is externally managed (PEP 668).\n"
            "If you installed with pipx, the command above will work.\n"
            "If you haven't installed pipx yet:\n"
            "  sudo apt install pipx && pipx install caddy-tui"
        )
    
    return (
        "pip install --upgrade caddy-tui\n"
        "  — or —\n"
        "pipx upgrade caddy-tui\n\n"
        "(use pipx if you installed via pipx)"
    )