"""Invoke the privileged helper script when elevated access is required."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os
import shlex
import shutil
import subprocess
import time

from .config import CACHE_DIR, ensure_cache_dir

HELPER_BIN = os.environ.get("CADDY_TUI_HELPER_BIN", "caddy-tui-helper")
SUDO_BIN = os.environ.get("CADDY_TUI_SUDO_BIN", "sudo")


@dataclass(slots=True)
class HelperCommand:
    args: list[str]

    @property
    def printable(self) -> str:
        return " ".join(shlex.quote(part) for part in self.args)


class HelperInvocationError(RuntimeError):
    def __init__(self, command: HelperCommand, stderr: str) -> None:
        super().__init__(stderr or "helper command failed")
        self.command = command
        self.stderr = stderr


def _resolve_helper_bin() -> str:
    """Return an absolute path for the helper executable."""
    helper_path = Path(HELPER_BIN)
    if helper_path.is_absolute():
        if helper_path.exists():
            return str(helper_path)
        raise FileNotFoundError(f"Helper executable '{HELPER_BIN}' does not exist")
    located = shutil.which(HELPER_BIN)
    if located:
        return located
    raise FileNotFoundError(f"Unable to locate helper executable '{HELPER_BIN}' in PATH")


def _build_base_command(non_interactive: bool = True) -> list[str]:
    if not shutil.which(SUDO_BIN):
        raise FileNotFoundError(f"Unable to locate sudo executable '{SUDO_BIN}'")
    helper_bin = _resolve_helper_bin()
    command = [SUDO_BIN]
    if non_interactive:
        command.append("-n")
    command.append(helper_bin)
    return command


def _run_helper(args: list[str], *, capture_output: bool = False) -> HelperCommand | tuple[HelperCommand, str]:
    command = HelperCommand(args)
    try:
        proc = subprocess.run(args, capture_output=True, text=True, check=True)
    except subprocess.CalledProcessError as exc:  # pragma: no cover - relies on sudo
        raise HelperInvocationError(command, exc.stderr.strip() or exc.stdout.strip()) from exc
    if capture_output:
        return command, (proc.stdout or "").strip()
    return command


def stage_caddyfile_copy(source: Path, *, interactive: bool = False) -> tuple[Path | None, str | None, str | None]:
    """Copy a root-owned Caddyfile into the cache via the helper."""
    ensure_cache_dir()
    timestamp = int(time.time())
    staged = CACHE_DIR / "mirrors" / f"{source.name}.{timestamp}"
    staged.parent.mkdir(parents=True, exist_ok=True)
    args = _build_base_command(non_interactive=not interactive) + [
        "mirror",
        "--source",
        str(source),
        "--dest",
        str(staged),
        "--owner",
        str(os.getuid()),
        "--group",
        str(os.getgid()),
    ]
    try:
        command = _run_helper(args)
    except (HelperInvocationError, FileNotFoundError) as exc:
        printable = exc.command.printable if isinstance(exc, HelperInvocationError) else " ".join(args)
        message = exc.stderr if isinstance(exc, HelperInvocationError) else str(exc)
        return None, printable, message
    return staged, command.printable, None


def install_generated_file(source: Path, dest: Path, mode: int = 0o644) -> tuple[bool, str | None, str | None]:
    args = _build_base_command() + [
        "install",
        "--source",
        str(source),
        "--dest",
        str(dest),
        "--mode",
        oct(mode),
    ]
    try:
        command = _run_helper(args)
    except (HelperInvocationError, FileNotFoundError) as exc:
        printable = exc.command.printable if isinstance(exc, HelperInvocationError) else " ".join(args)
        message = exc.stderr if isinstance(exc, HelperInvocationError) else str(exc)
        return False, printable, message
    return True, command.printable, None


def reload_caddy_service(command_override: str | None = None) -> tuple[bool, str | None, str | None]:
    args = _build_base_command() + ["reload"]
    if command_override:
        args.extend(["--command", command_override])
    try:
        command = _run_helper(args)
    except (HelperInvocationError, FileNotFoundError) as exc:
        printable = exc.command.printable if isinstance(exc, HelperInvocationError) else " ".join(args)
        message = exc.stderr if isinstance(exc, HelperInvocationError) else str(exc)
        return False, printable, message
    return True, command.printable, None


def restart_caddy_service(command_override: str | None = None) -> tuple[bool, str | None, str | None]:
    args = _build_base_command() + ["restart"]
    if command_override:
        args.extend(["--command", command_override])
    try:
        command = _run_helper(args)
    except (HelperInvocationError, FileNotFoundError) as exc:
        printable = exc.command.printable if isinstance(exc, HelperInvocationError) else " ".join(args)
        message = exc.stderr if isinstance(exc, HelperInvocationError) else str(exc)
        return False, printable, message
    return True, command.printable, None


def check_caddy_service(command_override: str | None = None) -> tuple[str | None, str | None, str | None]:
    args = _build_base_command() + ["status"]
    if command_override:
        args.extend(["--command", command_override])
    try:
        command, output = _run_helper(args, capture_output=True)
    except (HelperInvocationError, FileNotFoundError) as exc:
        printable = exc.command.printable if isinstance(exc, HelperInvocationError) else " ".join(args)
        message = exc.stderr if isinstance(exc, HelperInvocationError) else str(exc)
        return None, printable, message
    normalized = output.lower() if output else "unknown"
    return normalized or "unknown", command.printable, None
