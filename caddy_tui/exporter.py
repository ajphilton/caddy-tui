"""Generate Caddy configuration from the database."""
from __future__ import annotations

from pathlib import Path
from typing import Iterable

from sqlalchemy import select

from . import models
from .db import session_scope


class ExportError(RuntimeError):
    pass


def _build_simple_caddyfile(sites: Iterable[models.Site]) -> str:
    blocks: list[str] = []
    for site in sites:
        status = "# disabled\n" if not site.enabled else ""
        body = ["    respond \"caddy-tui placeholder\""]
        blocks.append(f"{status}{site.address} {{\n" + "\n".join(body) + "\n}\n")
    return "\n".join(blocks)


def generate_caddyfile(target: Path) -> Path:
    with session_scope() as session:
        sites = session.scalars(select(models.Site)).all()
        data = _build_simple_caddyfile(sites)
        target.write_text(data)
        return target
