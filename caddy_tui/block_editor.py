"""Helpers for editing caddy-tui snapshot blocks."""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Iterable

from sqlalchemy import select

from . import models
from .caddyfile_parser import ParsedBlock, parse_caddyfile_text
from .db import session_scope
from .importer import DEFAULT_CONFIG_NAME, _write_snapshot
from .snapshots import get_snapshot, render_snapshot_text


EDITOR_SOURCE_LABEL = "caddy-tui editor"


def load_caddy_tui_blocks(db_path: Path) -> list[ParsedBlock]:
    """Return the parsed blocks for the caddy-tui snapshot."""
    text = _snapshot_text(db_path)
    if text is None:
        return []
    parsed = parse_caddyfile_text(text)
    return parsed.blocks


def save_caddy_tui_blocks(blocks: list[ParsedBlock], db_path: Path, *, source_label: str = EDITOR_SOURCE_LABEL) -> None:
    """Persist parsed blocks back to the caddy-tui snapshot."""
    text = blocks_to_text(blocks)
    parsed = parse_caddyfile_text(text)
    digest = sha256(text.encode("utf-8")).hexdigest()
    collected_at = datetime.now(timezone.utc)

    with session_scope(db_path=db_path) as session:
        config = session.scalar(select(models.Config).where(models.Config.name == DEFAULT_CONFIG_NAME))
        if config is None:
            raise RuntimeError("Initialise the database before editing blocks.")
        _write_snapshot(
            session,
            config,
            models.SNAPSHOT_KIND_CADDY_TUI,
            parsed.blocks,
            source_path=source_label,
            source_hash=digest,
            collected_at=collected_at,
        )


def blocks_to_text(blocks: Iterable[ParsedBlock]) -> str:
    parts: list[str] = []
    for block in blocks:
        if block.raw_prelude:
            parts.append(block.raw_prelude)
        for fragment in block.fragments:
            parts.append(fragment.content)
        if block.raw_postlude:
            parts.append(block.raw_postlude)
    return "".join(parts)


def parse_single_block(text: str) -> ParsedBlock:
    """Parse ``text`` and ensure it contains exactly one server block."""
    parsed = parse_caddyfile_text(text)
    blocks = [block for block in parsed.blocks if block.fragments]
    if len(blocks) != 1:
        raise ValueError("Expected exactly one server block")
    return blocks[0]


def _snapshot_text(db_path: Path) -> str | None:
    with session_scope(db_path=db_path) as session:
        config = session.scalar(select(models.Config).where(models.Config.name == DEFAULT_CONFIG_NAME))
        if config is None:
            return None
        snapshot = get_snapshot(session, config.id, models.SNAPSHOT_KIND_CADDY_TUI)
        if snapshot is None:
            return None
        return render_snapshot_text(snapshot)
