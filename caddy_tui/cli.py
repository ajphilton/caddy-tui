"""CLI entry point for caddy-tui."""
from __future__ import annotations

from pathlib import Path
import json

import click

from . import models
from .config import GENERATED_CADDYFILE, GENERATED_JSON, LIVE_CADDYFILE, ensure_app_dir, DB_PATH
from .db import init_db
from .importer import import_caddyfile, CaddyfilePermissionError
from .exporter import generate_caddyfile
from .caddy_integration import validate_config, reload_caddy
from .tui_app import run_tui
from .versioning import collect_version_info, store_current_version
from .status import collect_app_status, refresh_live_snapshot
from .drift import compare_caddyfile


def _echo_json(payload: dict) -> None:
    click.echo(json.dumps(payload))


@click.group()
@click.version_option()
def main() -> None:
    """Manage Caddy configuration via SQLite."""


@main.command()
@click.option("--db", "db_path", type=click.Path(dir_okay=False, path_type=Path))
def init(db_path: Path | None) -> None:
    """Initialise the SQLite database."""
    ensure_app_dir()
    target = db_path or DB_PATH
    init_db(target)
    store_current_version(db_path=target)
    _echo_json({"status": "ok", "db_path": str(target)})


@main.command(name="import")
@click.option("--caddyfile", type=click.Path(exists=True, dir_okay=False, path_type=Path))
def import_cmd(caddyfile: Path | None) -> None:
    """Import an existing Caddyfile."""
    init_db()
    try:
        summary = import_caddyfile(caddyfile)
    except CaddyfilePermissionError as exc:
        raise click.ClickException(str(exc))
    store_current_version()
    _echo_json(
        {
            "status": "ok",
            "source": str(summary.source_path),
            "sites": summary.site_labels,
            "site_count": summary.site_count,
        }
    )
    refresh_live_snapshot(live_caddyfile=LIVE_CADDYFILE)


@main.command()
@click.option("--output", type=click.Path(dir_okay=False, path_type=Path))
@click.option("--format", "fmt", type=click.Choice(["caddyfile", "json"]), default="caddyfile")
def apply(output: Path | None, fmt: str) -> None:
    """Generate, validate and reload Caddy."""
    if fmt == "caddyfile":
        target = output or GENERATED_CADDYFILE
        generate_caddyfile(target)
    else:
        target = output or GENERATED_JSON
        # TODO: implement JSON exporter
        target.write_text("{}\n")
    validate_config(target, fmt)
    reload_caddy(target, fmt)
    store_current_version()
    _echo_json({"status": "ok", "format": fmt, "output": str(target)})
    refresh_live_snapshot(live_caddyfile=LIVE_CADDYFILE)


@main.command()
def tui() -> None:
    """Launch the interactive menu UI (import, drift diff, live refresh, reload)."""
    run_tui()


@main.command()
@click.option("--format", "fmt", type=click.Choice(["caddyfile", "json"]), default="caddyfile")
def validate(fmt: str) -> None:
    """Generate and validate the config without reloading."""
    if fmt == "caddyfile":
        target = GENERATED_CADDYFILE
        generate_caddyfile(target)
    else:
        target = GENERATED_JSON
        target.write_text("{}\n")
    validate_config(target, fmt)
    store_current_version()
    _echo_json({"status": "ok", "format": fmt, "output": str(target)})


@main.command("version")
def version_cmd() -> None:
    """Report current and latest known versions."""
    info = collect_version_info()
    _echo_json(
        {
            "status": "ok",
            "current_version": info.current,
            "latest_version": info.latest,
            "update_available": info.update_available,
            "source": info.source,
        }
    )


@main.command("refresh-live")
def refresh_live_cmd() -> None:
    """Refresh the live snapshot via the configured helper (same as TUI option 'r')."""
    info = refresh_live_snapshot(live_caddyfile=LIVE_CADDYFILE)
    payload = {
        "status": "ok",
        "live_available": next((snap.available for snap in info.snapshots if snap.kind == models.SNAPSHOT_KIND_CADDY_LIVE), False),
        "live_error": next((snap.error for snap in info.snapshots if snap.kind == models.SNAPSHOT_KIND_CADDY_LIVE), None),
    }
    if info.service_status:
        payload.update(
            {
                "service_state": info.service_status.state,
                "service_detail": info.service_status.detail,
                "service_block_count": info.service_status.block_count,
                "service_source": info.service_status.source,
                "service_error": info.service_status.error,
            }
        )
    _echo_json(payload)


@main.command()
@click.option("--caddyfile", "caddyfile_path", type=click.Path(dir_okay=False, path_type=Path))
@click.option("--diff/--no-diff", default=False, help="Include a unified diff when drift is detected.")
@click.option("--refresh-live", is_flag=True, help="Refresh the live snapshot before reporting status.")
def status(caddyfile_path: Path | None, diff: bool, refresh_live: bool) -> None:
    """Report snapshot drift, block counts, and optionally refresh the live helper snapshot."""
    if caddyfile_path:
        import_caddyfile(caddyfile_path, target_snapshot=models.SNAPSHOT_KIND_CADDYFILE, mirror_to=())

    info = collect_app_status(live_caddyfile=LIVE_CADDYFILE, refresh_live=refresh_live)
    target = caddyfile_path or (Path(info.last_import_path) if info.last_import_path else None)
    if target is None:
        raise click.ClickException("Specify --caddyfile or run an import so the tool knows which file to compare.")

    payload = {
        "status": "ok",
        "db_ready": info.db_ready,
        "block_count": info.block_count,
        "service": (
            {
                "state": info.service_status.state,
                "detail": info.service_status.detail,
                "source": info.service_status.source,
                "block_count": info.service_status.block_count,
                "error": info.service_status.error,
            }
            if info.service_status
            else None
        ),
        "snapshots": [
            {
                "kind": snapshot.kind,
                "label": snapshot.label,
                "available": snapshot.available,
                "site_count": snapshot.site_count,
                "block_count": snapshot.block_count,
                "collected_at": snapshot.collected_at,
                "source_path": snapshot.source_path,
                "source_hash": snapshot.source_hash,
                "error": snapshot.error,
            }
            for snapshot in info.snapshots
        ],
        "comparisons": [
            {
                "left": comparison.left_kind,
                "right": comparison.right_kind,
                "status": comparison.status,
                "mismatch_count": comparison.mismatch_count,
                "left_hash": comparison.left_hash,
                "right_hash": comparison.right_hash,
            }
            for comparison in info.comparisons
        ],
        "target": str(target),
    }

    diff_report = None
    if diff:
        report = compare_caddyfile(target, db_path=info.db_path)
        if report.error:
            raise click.ClickException(report.error)
        payload.update(
            {
                "in_sync": report.in_sync,
                "generated_hash": report.generated_hash,
                "target_hash": report.target_hash,
                "diff_included": diff and report.diff is not None,
            }
        )
        if report.diff:
            payload["diff"] = report.diff
        diff_report = report

    drift_detected = any(
        comparison.status == "different"
        and (comparison.left_kind == models.SNAPSHOT_KIND_CADDY_TUI or comparison.right_kind == models.SNAPSHOT_KIND_CADDY_TUI)
        for comparison in info.comparisons
    )
    if "in_sync" not in payload:
        payload["in_sync"] = not drift_detected

    _echo_json(payload)
    if (diff_report and diff_report.in_sync is False) or (diff_report is None and drift_detected):
        raise SystemExit(1)
