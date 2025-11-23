"""Helpers that summarise application state for the TUI."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError

from .config import CADDY_ADMIN_ENDPOINT, CADDY_ADMIN_TIMEOUT, DB_PATH, LIVE_CADDYFILE
from .db import init_db, session_scope
from .importer import DEFAULT_CONFIG_NAME, import_caddyfile, import_caddyfile_text, import_caddy_json_payload
from .helper_runner import check_caddy_service
from .models import (
    Config,
    ConfigSnapshot,
    ServerBlock,
    ServerBlockSite,
    SnapshotKind,
    SNAPSHOT_KIND_CADDYFILE,
    SNAPSHOT_KIND_CADDY_LIVE,
    SNAPSHOT_KIND_CADDY_TUI,
)
from .live_api import LiveApiStatus, fetch_live_status
from .snapshots import SNAPSHOT_LABELS, SNAPSHOT_PAIRINGS, SnapshotComparison, compare_snapshots, get_snapshot


@dataclass(slots=True)
class SnapshotInfo:
    kind: SnapshotKind
    label: str
    available: bool
    site_count: int
    block_count: int
    collected_at: str | None
    source_path: str | None
    source_hash: str | None
    error: str | None = None


@dataclass(slots=True)
class AppStatus:
    db_path: Path
    db_exists: bool
    db_ready: bool
    last_import_path: str | None
    last_import_time: str | None
    snapshots: list[SnapshotInfo]
    comparisons: list[SnapshotComparison]
    block_count: int
    service_status: "ServiceStatus" | None


@dataclass(slots=True)
class ServiceStatus:
    state: str
    detail: str | None
    source: str | None
    block_count: int | None
    error: str | None = None


def collect_app_status(
    db_path: Path | None = None,
    *,
    refresh_sources: bool = True,
    refresh_live: bool = False,
    live_caddyfile: Path | None = None,
    purge_live_snapshot: bool = False,
    check_service: bool = True,
    admin_endpoint: str | None = CADDY_ADMIN_ENDPOINT,
) -> AppStatus:
    target = Path(db_path or DB_PATH)
    if not target.exists():
        try:
            init_db(db_path=target)
        except Exception:  # pragma: no cover - initial bootstrap best-effort
            pass
    db_exists = target.exists()
    db_ready = False
    last_import_path: str | None = None
    last_import_time: str | None = None
    snapshots: list[SnapshotInfo] = []
    comparisons: list[SnapshotComparison] = []
    block_count = 0
    refresh_errors: dict[SnapshotKind, str] = {}
    service_status: ServiceStatus | None = None
    live_api_status: LiveApiStatus | None = None
    if check_service:
        live_api_status = fetch_live_status(admin_endpoint, timeout=CADDY_ADMIN_TIMEOUT)

    if db_exists:
        try:
            config_info = _load_config_metadata(target)
            if refresh_sources and config_info is None:
                config_info = _bootstrap_caddyfile_from_disk(target, refresh_errors, live_caddyfile or LIVE_CADDYFILE)
            if refresh_sources and config_info:
                _refresh_snapshot_if_needed(
                    target,
                    config_info.caddyfile_path,
                    SNAPSHOT_KIND_CADDYFILE,
                    refresh_errors,
                )
            if refresh_live:
                _refresh_live_snapshot(
                    target,
                    live_api_status,
                    live_caddyfile or LIVE_CADDYFILE,
                    refresh_errors,
                    admin_endpoint,
                )
            snapshot_data = _collect_snapshot_details(target, purge_live_snapshot=purge_live_snapshot)
            if snapshot_data:
                db_ready = True
                last_import_path = snapshot_data.last_import_path
                last_import_time = snapshot_data.last_import_time
                snapshots = snapshot_data.snapshots
                comparisons = snapshot_data.comparisons
                block_count = snapshot_data.block_count
            elif config_info is None:
                db_ready = True
        except OperationalError:
            db_ready = False

    service_status = _build_service_status(live_api_status, live_caddyfile or LIVE_CADDYFILE, admin_endpoint)

    return AppStatus(
        db_path=target,
        db_exists=db_exists,
        db_ready=db_ready,
        last_import_path=last_import_path,
        last_import_time=last_import_time,
        snapshots=_apply_errors(snapshots, refresh_errors),
        comparisons=comparisons,
        block_count=block_count,
        service_status=service_status,
    )


@dataclass(slots=True)
class _SnapshotData:
    last_import_path: str | None
    last_import_time: str | None
    snapshots: list[SnapshotInfo]
    comparisons: list[SnapshotComparison]
    block_count: int


@dataclass(slots=True)
class _ConfigMetadata:
    caddyfile_path: Path | None


def _load_config_metadata(db_path: Path) -> _ConfigMetadata | None:
    with session_scope(db_path=db_path) as session:
        config = session.scalar(select(Config).where(Config.name == DEFAULT_CONFIG_NAME))
        if not config:
            return None
        last_path = Path(config.caddyfile_path) if config.caddyfile_path else None
        return _ConfigMetadata(caddyfile_path=last_path)


def _bootstrap_caddyfile_from_disk(
    db_path: Path,
    errors: dict[SnapshotKind, str],
    fallback_path: Path | None,
) -> _ConfigMetadata | None:
    try:
        summary = import_caddyfile(fallback_path, target_snapshot=SNAPSHOT_KIND_CADDY_TUI, mirror_to=(SNAPSHOT_KIND_CADDYFILE,), db_path=db_path)
    except FileNotFoundError:
        errors[SNAPSHOT_KIND_CADDY_TUI] = "No Caddyfile found; run an import"
        return None
    except Exception as exc:  # pragma: no cover - unexpected import failure surfaces to UI
        errors[SNAPSHOT_KIND_CADDY_TUI] = str(exc)
        return None
    return _ConfigMetadata(caddyfile_path=Path(summary.source_path))


def _refresh_snapshot_if_needed(
    db_path: Path,
    path: Path | None,
    kind: SnapshotKind,
    errors: dict[SnapshotKind, str],
    *,
    required: bool = False,
) -> None:
    if not path:
        if required:
            errors[kind] = "Live Caddyfile path not configured"
        return
    try:
        import_caddyfile(path, target_snapshot=kind, mirror_to=(), db_path=db_path)
    except Exception as exc:  # pragma: no cover - surfaced to status output
        errors[kind] = str(exc)


def _refresh_live_snapshot(
    db_path: Path,
    api_status: LiveApiStatus | None,
    fallback_path: Path | None,
    errors: dict[SnapshotKind, str],
    admin_endpoint: str | None,
) -> None:
    if api_status and api_status.caddyfile_text:
        try:
            import_caddyfile_text(
                api_status.caddyfile_text,
                source_label=admin_endpoint or "caddy-admin",
                target_snapshot=SNAPSHOT_KIND_CADDY_LIVE,
                db_path=db_path,
            )
            return
        except Exception as exc:  # pragma: no cover - parse/DB errors bubble to UI
            errors[SNAPSHOT_KIND_CADDY_LIVE] = str(exc)
            return
    if api_status and getattr(api_status, "json_payload", None):
        try:
            import_caddy_json_payload(
                api_status.json_payload,
                source_label=admin_endpoint or "caddy-admin",
                target_snapshot=SNAPSHOT_KIND_CADDY_LIVE,
                db_path=db_path,
            )
            return
        except Exception as exc:  # pragma: no cover - parse/DB errors bubble to UI
            errors[SNAPSHOT_KIND_CADDY_LIVE] = str(exc)
            return
    _refresh_snapshot_if_needed(
        db_path,
        fallback_path,
        SNAPSHOT_KIND_CADDY_LIVE,
        errors,
        required=True,
    )


def _collect_snapshot_details(db_path: Path, *, purge_live_snapshot: bool) -> _SnapshotData | None:
    with session_scope(db_path=db_path) as session:
        config = session.scalar(select(Config).where(Config.name == DEFAULT_CONFIG_NAME))
        if not config:
            return None

        snapshots: list[SnapshotInfo] = []
        total_blocks = 0
        live_snapshot_obj: ConfigSnapshot | None = None
        for kind, label in SNAPSHOT_LABELS.items():
            snapshot = get_snapshot(session, config.id, kind)
            site_count = _snapshot_site_count(session, snapshot) if snapshot else 0
            block_count = _snapshot_block_count(session, snapshot) if snapshot else 0
            snapshots.append(
                SnapshotInfo(
                    kind=kind,
                    label=label,
                    available=snapshot is not None,
                    site_count=site_count,
                    block_count=block_count,
                    collected_at=snapshot.collected_at.isoformat(timespec="seconds") if snapshot else None,
                    source_path=snapshot.source_path if snapshot else None,
                    source_hash=snapshot.source_hash if snapshot else None,
                )
            )
            if kind == SNAPSHOT_KIND_CADDY_TUI:
                total_blocks = block_count
            if kind == SNAPSHOT_KIND_CADDY_LIVE:
                live_snapshot_obj = snapshot

        comparisons: list[SnapshotComparison] = []
        for left, right in SNAPSHOT_PAIRINGS:
            left_snapshot = get_snapshot(session, config.id, left)
            right_snapshot = get_snapshot(session, config.id, right)
            comparisons.append(
                compare_snapshots(
                    left_snapshot,
                    right_snapshot,
                    left_kind=left,
                    right_kind=right,
                )
            )

        data = _SnapshotData(
            last_import_path=config.caddyfile_path,
            last_import_time=config.last_imported_at,
            snapshots=snapshots,
            comparisons=comparisons,
            block_count=total_blocks,
        )
        if purge_live_snapshot and live_snapshot_obj is not None:
            session.delete(live_snapshot_obj)
        return data


def _snapshot_site_count(session, snapshot: ConfigSnapshot | None) -> int:
    if snapshot is None:
        return 0
    return (
        session.scalar(
            select(func.count())
            .select_from(ServerBlockSite)
            .join(ServerBlock, ServerBlockSite.block)
            .where(ServerBlock.snapshot_id == snapshot.id)
        )
        or 0
    )


def _snapshot_block_count(session, snapshot: ConfigSnapshot | None) -> int:
    if snapshot is None:
        return 0
    return session.scalar(select(func.count()).select_from(ServerBlock).where(ServerBlock.snapshot_id == snapshot.id)) or 0


def _apply_errors(snapshots: list[SnapshotInfo], errors: dict[SnapshotKind, str]) -> list[SnapshotInfo]:
    if not errors:
        return snapshots
    updated: list[SnapshotInfo] = []
    for snapshot in snapshots:
        error = errors.get(snapshot.kind)
        if error:
            updated.append(
                SnapshotInfo(
                    kind=snapshot.kind,
                    label=snapshot.label,
                    available=snapshot.available,
                    site_count=snapshot.site_count,
                    block_count=snapshot.block_count,
                    collected_at=snapshot.collected_at,
                    source_path=snapshot.source_path,
                    source_hash=snapshot.source_hash,
                    error=error,
                )
            )
        else:
            updated.append(snapshot)
    return updated


def refresh_live_snapshot(
    db_path: Path | None = None,
    *,
    live_caddyfile: Path | None = None,
) -> AppStatus:
    """Refresh the live snapshot without touching other sources."""
    return collect_app_status(
        db_path=db_path,
        refresh_sources=False,
        refresh_live=True,
        live_caddyfile=live_caddyfile,
        purge_live_snapshot=False,
        check_service=True,
        admin_endpoint=CADDY_ADMIN_ENDPOINT,
    )


def _build_service_status(
    api_status: LiveApiStatus | None,
    fallback_path: Path | None,
    admin_endpoint: str | None,
) -> ServiceStatus | None:
    if api_status:
        return ServiceStatus(
            state=api_status.state,
            detail=api_status.format,
            source=admin_endpoint,
            block_count=api_status.block_count,
            error=api_status.error,
        )
    if fallback_path is None:
        return None
    raw_state, command, error = check_caddy_service()
    if raw_state is None:
        return ServiceStatus(state="unknown", detail=None, source=command, block_count=None, error=error)
    normalized = _normalize_service_state(raw_state)
    return ServiceStatus(state=normalized, detail=raw_state, source=command, block_count=None, error=error)


def _normalize_service_state(raw: str) -> str:
    lowered = raw.lower().strip()
    if lowered in {"active", "running", "live", "ok"}:
        return "live"
    if lowered in {"inactive", "failed", "dead", "stopped", "down"}:
        return "down"
    return "unknown"
