"""CLI entry point for caddy-tui."""
from __future__ import annotations

from pathlib import Path
import json

import click

from .config import GENERATED_CADDYFILE, GENERATED_JSON, ensure_app_dir, DB_PATH
from .db import init_db
from .importer import import_caddyfile
from .exporter import generate_caddyfile
from .caddy_integration import validate_config, reload_caddy
from .tui_app import run_tui


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
    _echo_json({"status": "ok", "db_path": str(target)})


@main.command()
@click.option("--caddyfile", type=click.Path(exists=True, dir_okay=False, path_type=Path))
def import_(caddyfile: Path | None) -> None:
    """Import an existing Caddyfile."""
    summary = import_caddyfile(caddyfile)
    _echo_json(
        {
            "status": "ok",
            "source": str(summary.source_path),
            "sites": summary.site_labels,
            "site_count": summary.site_count,
        }
    )


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
    _echo_json({"status": "ok", "format": fmt, "output": str(target)})


@main.command()
def tui() -> None:
    """Launch the Textual UI."""
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
    _echo_json({"status": "ok", "format": fmt, "output": str(target)})
