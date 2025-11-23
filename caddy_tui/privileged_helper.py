"""Privileged helper commands for caddy-tui.

This module is intended to be executed via sudo. It exposes a very small surface
area so operators can grant password-less sudo access to `caddy-tui-helper`
without giving the TUI full root access.
"""
from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path

import click


@click.group()
def main() -> None:
    """Run restricted privileged operations for caddy-tui."""


@main.command()
@click.option("--source", type=click.Path(exists=True, dir_okay=False, path_type=Path), required=True)
@click.option("--dest", type=click.Path(dir_okay=False, path_type=Path), required=True)
@click.option("--owner", type=int, required=True, help="Target file owner UID")
@click.option("--group", type=int, required=True, help="Target file group GID")
def mirror(source: Path, dest: Path, owner: int, group: int) -> None:
    """Copy a root-owned Caddyfile into an unprivileged staging area."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, dest)
    os.chown(dest, owner, group)
    click.echo(f"Mirrored {source} -> {dest}")


@main.command()
@click.option("--source", type=click.Path(exists=True, dir_okay=False, path_type=Path), required=True)
@click.option("--dest", type=click.Path(dir_okay=False, path_type=Path), required=True)
@click.option("--mode", type=str, default="0o644")
def install(source: Path, dest: Path, mode: str) -> None:
    """Install a generated Caddyfile into /etc with controlled permissions."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, dest)
    os.chmod(dest, int(mode, 8))
    click.echo(f"Installed {source} -> {dest}")


@main.command()
@click.option("--command", default="systemctl reload caddy", help="Reload command to execute.")
def reload(command: str) -> None:
    """Reload the running Caddy daemon."""
    parts = command.split()
    subprocess.run(parts, check=True)
    click.echo("Reloaded Caddy")


@main.command()
@click.option("--command", default="systemctl restart caddy", help="Restart command to execute.")
def restart(command: str) -> None:
    """Restart the Caddy daemon when it is not running."""
    parts = command.split()
    subprocess.run(parts, check=True)
    click.echo("Restarted Caddy")


@main.command(name="status")
@click.option("--command", default="systemctl is-active caddy", help="Command that reports Caddy service state.")
def status_cmd(command: str) -> None:
    """Report whether Caddy is live or down."""
    parts = command.split()
    proc = subprocess.run(parts, capture_output=True, text=True, check=False)
    output = (proc.stdout or "").strip()
    if proc.returncode != 0 and not output:
        message = (proc.stderr or "status command failed").strip()
        raise click.ClickException(message)
    click.echo(output or "unknown")


if __name__ == "__main__":
    main()
