"""Import existing Caddy config into the database."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from sqlalchemy import select

from . import models
from .caddy_integration import adapt_caddyfile
from .db import session_scope

DEFAULT_CADDYFILE_PATHS: tuple[Path, ...] = (
    Path("/etc/caddy/Caddyfile"),
    Path("/usr/local/etc/caddy/Caddyfile"),
    Path("/etc/Caddyfile"),
    Path("./Caddyfile"),
)


def find_caddyfile(explicit: Path | None = None) -> Path:
    if explicit and explicit.exists():
        return explicit
    for candidate in DEFAULT_CADDYFILE_PATHS:
        if candidate.exists():
            return candidate
    raise FileNotFoundError("Unable to locate a Caddyfile to import")


@dataclass(slots=True)
class ImportSummary:
    source_path: Path
    site_labels: list[str]
    site_count: int


def import_caddyfile(path: Path | None = None) -> ImportSummary:
    source = find_caddyfile(path)
    adapted = adapt_caddyfile(source)

    with session_scope() as session:
        apps = adapted.get("apps", {})
        http = apps.get("http", {})
        servers: dict = http.get("servers", {})
        labels: list[str] = []
        for label, server in servers.items():
            listener = ",".join(server.get("listen", [])) or label
            labels.append(label)
            existing = session.scalars(select(models.Site).where(models.Site.label == label)).first()
            if existing:
                existing.address = listener
            else:
                session.add(models.Site(label=label, address=listener, enabled=True))

        meta_payload = json.dumps({"path": str(source), "listen": labels})
        meta = session.get(models.Meta, "last_import")
        if meta:
            meta.value = meta_payload
        else:
            session.add(models.Meta(key="last_import", value=meta_payload))

    return ImportSummary(source_path=source, site_labels=labels, site_count=len(labels))
