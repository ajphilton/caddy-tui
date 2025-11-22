"""Version discovery and tracking utilities."""
from __future__ import annotations

import json
import os
import urllib.request
from dataclasses import dataclass
from typing import Optional

from packaging.version import InvalidVersion, Version

from . import __version__
from pathlib import Path

from .db import session_scope
from . import models

DEFAULT_REPO = "ajphilton/caddy-tui"


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