"""Generate Caddy configuration from the database."""
from __future__ import annotations

from pathlib import Path

from sqlalchemy import select

from . import models
from .db import session_scope
from .helper_runner import install_generated_file
from .config import ensure_cache_dir
from .importer import DEFAULT_CONFIG_NAME


class ExportError(RuntimeError):
    pass


def render_caddyfile_text(
    db_path: Path | None = None,
    *,
    snapshot_kind: models.SnapshotKind = models.SNAPSHOT_KIND_CADDY_TUI,
) -> str:
    """Render a Caddyfile from the database snapshot as text.

    Reconstructs the Caddyfile content from stored server blocks,
    preserving original formatting where possible.

    Args:
        db_path: Optional database path override.
        snapshot_kind: Which snapshot type to render from.

    Returns:
        The rendered Caddyfile content as a string.
        Returns empty string if no config or snapshot exists.
    """
    with session_scope(db_path=db_path) as session:
        config = session.scalar(select(models.Config).where(models.Config.name == DEFAULT_CONFIG_NAME))
        if config is None:
            return ""
        snapshot = session.scalar(
            select(models.ConfigSnapshot)
                .where(
                    models.ConfigSnapshot.config_id == config.id,
                    models.ConfigSnapshot.source_kind == snapshot_kind,
                )
                .limit(1)
        )
        if snapshot is None:
            return ""
        chunks: list[str] = []
        for block in sorted(snapshot.server_blocks, key=lambda b: b.block_index):
            if block.raw_prelude:
                chunks.append(block.raw_prelude)
            fragments = sorted(block.fragments, key=lambda f: f.fragment_index)
            if fragments:
                chunks.extend(fragment.content for fragment in fragments)
            else:
                chunks.append(_synthesise_block(block))
            if block.raw_postlude:
                chunks.append(block.raw_postlude)
        return "".join(chunks)


def _synthesise_block(block: models.ServerBlock) -> str:
    labels = ", ".join(site.raw_label for site in sorted(block.sites, key=lambda s: s.label_index))
    header = f"{labels} {{\n" if labels else "{\n"
    body = "    respond \"caddy-tui placeholder\"\n"
    return header + body + "}\n"


def generate_caddyfile(
    target: Path,
    db_path: Path | None = None,
    *,
    snapshot_kind: models.SnapshotKind = models.SNAPSHOT_KIND_CADDY_TUI,
) -> Path:
    """Generate a Caddyfile from the database and write it to disk.

    Renders the snapshot to text and writes it to the target path.
    Falls back to using the privileged helper when direct write fails.

    Args:
        target: Path to write the generated Caddyfile to.
        db_path: Optional database path override.
        snapshot_kind: Which snapshot type to generate from.

    Returns:
        The path where the file was written.

    Raises:
        PermissionError: When the file cannot be written and helper fails.
    """
    data = render_caddyfile_text(db_path=db_path, snapshot_kind=snapshot_kind)
    try:
        target.write_text(data)
    except PermissionError:
        cache = ensure_cache_dir() / "generated"
        cache.mkdir(parents=True, exist_ok=True)
        staged = cache / target.name
        staged.write_text(data)
        success, command, error = install_generated_file(staged, target)
        if not success:
            detail = error or "Helper install failed"
            hint = f"Run: {command}" if command else "Run helper install manually"
            raise PermissionError(f"Unable to write {target}: {detail}. {hint}")
    return target
