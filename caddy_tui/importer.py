"""Import existing Caddy config into the database."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any, Sequence
import json
import os

from sqlalchemy import select

from . import models
from .caddyfile_parser import ParsedBlock, parse_caddyfile_text
from .caddy_integration import adapt_caddyfile
from .db import session_scope
from .helper_runner import stage_caddyfile_copy
from .json_normalizer import blocks_from_caddy_json

DEFAULT_CADDYFILE_PATHS: tuple[Path, ...] = (
    Path("/etc/caddy/Caddyfile"),
    Path("/usr/local/etc/caddy/Caddyfile"),
    Path("/etc/Caddyfile"),
    Path("./Caddyfile"),
)

MAX_PARENT_SEARCH_DEPTH = 5


def _generate_candidate_paths(explicit: Path) -> list[Path]:
    """Return a list of nearby paths that might contain a Caddyfile."""
    path = explicit.expanduser()
    candidates: list[Path] = []
    seen: set[Path] = set()

    def add(candidate: Path) -> None:
        candidate = candidate.expanduser()
        if candidate not in seen:
            seen.add(candidate)
            candidates.append(candidate)

    add(path)

    if path.name and path.name.lower() != "caddyfile":
        add(path.with_name("Caddyfile"))
    add(path / "Caddyfile")

    current = path.parent
    depth = 0
    while depth < MAX_PARENT_SEARCH_DEPTH and current != current.parent:
        add(current / "Caddyfile")
        current = current.parent
        depth += 1

    return candidates


def _resolve_explicit_path(explicit: Path) -> Path | None:
    for candidate in _generate_candidate_paths(explicit):
        if candidate.exists() and candidate.is_file():
            return candidate
    return None


class CaddyfilePermissionError(PermissionError):
    """Raised when the Caddyfile cannot be read due to permissions."""

    def __init__(self, path: Path, helper_command: str | None = None):
        self.path = path
        self.helper_command = helper_command
        message = f"Permission denied reading {path}. Run the import with elevated permissions or copy the file to a readable location."
        if helper_command:
            message = f"{message} You can run: {helper_command}"
        super().__init__(message)

    @property
    def suggested_command(self) -> str:
        if self.helper_command:
            return self.helper_command
        return f"sudo caddy-tui import --caddyfile {self.path}"


def find_caddyfile(explicit: Path | None = None) -> Path:
    """Locate a Caddyfile to import.

    If an explicit path is provided, search nearby locations for a matching
    Caddyfile. Otherwise fall back to the default search paths.

    Args:
        explicit: Optional path hint to search from.

    Returns:
        Path to the discovered Caddyfile.

    Raises:
        FileNotFoundError: When no Caddyfile can be located.
    """
    if explicit:
        resolved = _resolve_explicit_path(explicit)
        if resolved:
            return resolved
    for candidate in DEFAULT_CADDYFILE_PATHS:
        if candidate.exists():
            return candidate
    raise FileNotFoundError("Unable to locate a Caddyfile to import")


@dataclass(slots=True)
class ImportSummary:
    source_path: Path
    site_labels: list[str]
    site_count: int
    snapshot_kind: models.SnapshotKind
    mirrored_snapshots: tuple[models.SnapshotKind, ...]


DEFAULT_CONFIG_NAME = "default"


def import_caddyfile(
    path: Path | None = None,
    *,
    helper_interactive: bool = False,
    target_snapshot: models.SnapshotKind = models.SNAPSHOT_KIND_CADDY_TUI,
    mirror_to: Sequence[models.SnapshotKind] | None = None,
    db_path: Path | None = None,
) -> ImportSummary:
    """Import a Caddyfile into the database.

    Parses the Caddyfile, validates it with the caddy binary, and stores
    the configuration blocks in the SQLite database.

    Args:
        path: Path to the Caddyfile. If None, searches default locations.
        helper_interactive: Whether to prompt for elevated access when needed.
        target_snapshot: The snapshot type to write to (default: caddy_tui).
        mirror_to: Additional snapshot types to mirror the import to.
        db_path: Optional database path override.

    Returns:
        ImportSummary with import statistics.

    Raises:
        FileNotFoundError: When no Caddyfile is found.
        CaddyfilePermissionError: When the file cannot be read.
        ValueError: When the Caddyfile contains no server blocks.
    """
    source = find_caddyfile(path)
    adapted_source = _ensure_accessible_source(source, helper_interactive=helper_interactive)
    adapt_caddyfile(adapted_source)  # validation only

    text = adapted_source.read_text()
    parsed = parse_caddyfile_text(text)
    if not parsed.blocks:
        raise ValueError("No server blocks detected in Caddyfile")

    digest = sha256(text.encode("utf-8")).hexdigest()
    collected_at = datetime.now(timezone.utc)
    timestamp = collected_at.isoformat(timespec="seconds")

    labels: list[str] = _summarise_block_labels(parsed.blocks)
    snapshots_written = _unique_kinds(target_snapshot, mirror_to)

    with session_scope(db_path=db_path) as session:
        config = _get_or_create_config(session, source)
        if target_snapshot == models.SNAPSHOT_KIND_CADDY_TUI:
            config.last_imported_at = timestamp
            config.last_caddyfile_hash = digest
        for kind in snapshots_written:
            _write_snapshot(
                session,
                config,
                kind,
                parsed.blocks,
                source_path=str(source),
                source_hash=digest,
                collected_at=collected_at,
            )

    return ImportSummary(
        source_path=source,
        site_labels=labels,
        site_count=len(labels),
        snapshot_kind=target_snapshot,
        mirrored_snapshots=tuple(kind for kind in snapshots_written if kind != target_snapshot),
    )


def import_caddyfile_text(
    text: str,
    *,
    source_label: str,
    target_snapshot: models.SnapshotKind,
    mirror_to: Sequence[models.SnapshotKind] | None = None,
    db_path: Path | None = None,
    require_config: bool = False,
) -> ImportSummary | None:
    """Import Caddyfile content from a text string.

    Useful when importing from the Caddy admin API or other sources
    where the content is already available as a string.

    Args:
        text: The Caddyfile content to import.
        source_label: Label to identify the source of this import.
        target_snapshot: The snapshot type to write to.
        mirror_to: Additional snapshot types to mirror the import to.
        db_path: Optional database path override.
        require_config: If True, raise an error if no config exists.

    Returns:
        ImportSummary with import statistics.

    Raises:
        ValueError: When the text contains no server blocks.
        RuntimeError: When require_config is True and no config exists.
    """
    parsed = parse_caddyfile_text(text)
    if not parsed.blocks:
        raise ValueError("No server blocks detected in Caddyfile text")

    digest = sha256(text.encode("utf-8")).hexdigest()
    collected_at = datetime.now(timezone.utc)
    timestamp = collected_at.isoformat(timespec="seconds")
    labels: list[str] = _summarise_block_labels(parsed.blocks)
    snapshots_written = _unique_kinds(target_snapshot, mirror_to)

    with session_scope(db_path=db_path) as session:
        config = _ensure_config_record(session, require_config=require_config, default_path=source_label)
        for kind in snapshots_written:
            _write_snapshot(
                session,
                config,
                kind,
                parsed.blocks,
                source_path=source_label,
                source_hash=digest,
                collected_at=collected_at,
            )

    return ImportSummary(
        source_path=Path(source_label),
        site_labels=labels,
        site_count=len(labels),
        snapshot_kind=target_snapshot,
        mirrored_snapshots=tuple(kind for kind in snapshots_written if kind != target_snapshot),
    )


def import_caddy_json_payload(
    payload: str | dict[str, Any],
    *,
    source_label: str,
    target_snapshot: models.SnapshotKind = models.SNAPSHOT_KIND_CADDY_LIVE,
    mirror_to: Sequence[models.SnapshotKind] | None = None,
    db_path: Path | None = None,
    require_config: bool = False,
) -> ImportSummary | None:
    """Import Caddy configuration from a JSON payload.

    Used primarily for importing from the Caddy admin API which returns
    configuration in JSON format.

    Args:
        payload: The JSON payload as a string or dict.
        source_label: Label to identify the source of this import.
        target_snapshot: The snapshot type to write to (default: caddy_live).
        mirror_to: Additional snapshot types to mirror the import to.
        db_path: Optional database path override.
        require_config: If True, raise an error if no config exists.

    Returns:
        ImportSummary with import statistics.

    Raises:
        RuntimeError: When require_config is True and no config exists.
        json.JSONDecodeError: When payload is a string with invalid JSON.
    """
    data = json.loads(payload) if isinstance(payload, str) else payload
    blocks = blocks_from_caddy_json(data)
    normalised_text = json.dumps(data, sort_keys=True)
    digest = sha256(normalised_text.encode("utf-8")).hexdigest()
    collected_at = datetime.now(timezone.utc)
    labels: list[str] = _summarise_block_labels(blocks)
    snapshots_written = _unique_kinds(target_snapshot, mirror_to)

    with session_scope(db_path=db_path) as session:
        config = _ensure_config_record(session, require_config=require_config, default_path=source_label)
        for kind in snapshots_written:
            _write_snapshot(
                session,
                config,
                kind,
                blocks,
                source_path=source_label,
                source_hash=digest,
                collected_at=collected_at,
            )

    return ImportSummary(
        source_path=Path(source_label),
        site_labels=labels,
        site_count=len(labels),
        snapshot_kind=target_snapshot,
        mirrored_snapshots=tuple(kind for kind in snapshots_written if kind != target_snapshot),
    )


def _ensure_accessible_source(source: Path, *, helper_interactive: bool = False) -> Path:
    if os.access(source, os.R_OK):
        return source
    staged, command, _ = stage_caddyfile_copy(source, interactive=helper_interactive)
    if staged:
        return staged
    raise CaddyfilePermissionError(source, helper_command=command)


def _get_or_create_config(session, source: Path) -> models.Config:
    config = session.scalar(select(models.Config).where(models.Config.name == DEFAULT_CONFIG_NAME))
    if config is None:
        config = models.Config(name=DEFAULT_CONFIG_NAME, caddyfile_path=str(source))
        session.add(config)
        session.flush()
    else:
        config.caddyfile_path = str(source)
    return config


def _ensure_config_record(session, *, require_config: bool, default_path: str | None) -> models.Config:
    config = session.scalar(select(models.Config).where(models.Config.name == DEFAULT_CONFIG_NAME))
    if config is None:
        if require_config:
            raise RuntimeError("Initialise the database with caddy-tui init before importing text")
        config = models.Config(name=DEFAULT_CONFIG_NAME, caddyfile_path=default_path)
        session.add(config)
        session.flush()
    return config


def _write_snapshot(
    session,
    config: models.Config,
    kind: models.SnapshotKind,
    blocks: list[ParsedBlock],
    *,
    source_path: str | None,
    source_hash: str | None,
    collected_at: datetime,
) -> None:
    snapshot = session.scalar(
        select(models.ConfigSnapshot).where(
            models.ConfigSnapshot.config_id == config.id,
            models.ConfigSnapshot.source_kind == kind,
        )
    )
    if snapshot is None:
        snapshot = models.ConfigSnapshot(config=config, source_kind=kind)
        session.add(snapshot)
        session.flush()
    snapshot.source_path = source_path
    snapshot.source_hash = source_hash
    snapshot.collected_at = collected_at
    snapshot.server_blocks.clear()

    for index, block in enumerate(blocks):
        _store_block(snapshot, index, block)


def _store_block(snapshot: models.ConfigSnapshot, index: int, block: ParsedBlock) -> models.ServerBlock:
    block_model = models.ServerBlock(
        snapshot=snapshot,
        block_index=index,
        is_global=block.is_global,
        raw_prelude=block.raw_prelude or None,
        raw_postlude=block.raw_postlude or None,
    )
    snapshot.server_blocks.append(block_model)

    for label_index, raw_label in enumerate(block.labels):
        host, port, scheme, is_ipv6, is_wildcard = _analyse_label(raw_label)
        block_model.sites.append(
            models.ServerBlockSite(
                raw_label=raw_label,
                host=host,
                port=port,
                scheme=scheme,
                is_ipv6=is_ipv6,
                is_wildcard=is_wildcard,
                label_index=label_index,
            )
        )

    for fragment_index, fragment in enumerate(block.fragments):
        block_model.fragments.append(
            models.RawFragment(
                fragment_index=fragment_index,
                kind=fragment.kind,
                content=fragment.content,
            )
        )

    return block_model


def _summarise_block_labels(blocks: list[ParsedBlock]) -> list[str]:
    labels: list[str] = []
    for block in blocks:
        if block.labels:
            labels.append(", ".join(block.labels))
        else:
            labels.append("(global options)")
    return labels


def _unique_kinds(
    target_snapshot: models.SnapshotKind,
    mirror_to: Sequence[models.SnapshotKind] | None,
) -> tuple[models.SnapshotKind, ...]:
    if mirror_to is None and target_snapshot == models.SNAPSHOT_KIND_CADDY_TUI:
        requested = [models.SNAPSHOT_KIND_CADDY_TUI, models.SNAPSHOT_KIND_CADDYFILE]
    elif mirror_to is None:
        requested = [target_snapshot]
    else:
        requested = [target_snapshot, *mirror_to]
    seen: list[models.SnapshotKind] = []
    for kind in requested:
        if kind not in seen:
            seen.append(kind)
    return tuple(seen)


def _analyse_label(raw: str) -> tuple[str | None, int | None, str | None, bool, bool]:
    scheme: str | None = None
    host_port = raw
    if "://" in raw:
        scheme, host_port = raw.split("://", 1)

    host: str | None = None
    port: int | None = None
    is_ipv6 = False
    if host_port.startswith("["):
        end = host_port.find("]")
        if end != -1:
            host = host_port[1:end]
            remainder = host_port[end + 1 :]
            if remainder.startswith(":"):
                try:
                    port = int(remainder[1:])
                except ValueError:
                    port = None
            is_ipv6 = True
        else:
            host = host_port
    else:
        if ":" in host_port:
            maybe_host, maybe_port = host_port.rsplit(":", 1)
            if maybe_port.isdigit():
                host = maybe_host or None
                port = int(maybe_port)
            else:
                host = host_port
        else:
            host = host_port or None

    if host is None and port is None and host_port.startswith(":") and host_port[1:].isdigit():
        port = int(host_port[1:])

    is_wildcard = bool(host and "*" in host)
    return host, port, scheme, is_ipv6, is_wildcard
