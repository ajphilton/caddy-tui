"""Compare the SQLite-backed configuration with a target Caddyfile."""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
import difflib

from .exporter import render_caddyfile_text
from .helper_runner import stage_caddyfile_copy

MAX_DIFF_LINES = 200


@dataclass(slots=True)
class DriftReport:
    target_path: Path
    in_sync: bool | None
    generated_hash: str | None
    target_hash: str | None
    diff: str | None
    error: str | None


def compare_caddyfile(target_path: Path, db_path: Path | None = None) -> DriftReport:
    try:
        generated_text = render_caddyfile_text(db_path=db_path)
    except Exception as exc:  # pragma: no cover - defensive, surfaces to caller
        return DriftReport(
            target_path=target_path,
            in_sync=None,
            generated_hash=None,
            target_hash=None,
            diff=None,
            error=f"Failed to render SQLite data: {exc}",
        )

    generated_hash = sha256(generated_text.encode("utf-8")).hexdigest()

    try:
        target_text = target_path.read_text()
    except FileNotFoundError:
        return DriftReport(
            target_path=target_path,
            in_sync=None,
            generated_hash=generated_hash,
            target_hash=None,
            diff=None,
            error=f"No Caddyfile found at {target_path}",
        )
    except PermissionError:
        staged, command, helper_error = stage_caddyfile_copy(target_path)
        if staged:
            target_text = staged.read_text()
        else:
            hint = f"Permission denied reading {target_path}" if helper_error is None else helper_error
            if command:
                hint = f"{hint}. Run: {command}"
            return DriftReport(
                target_path=target_path,
                in_sync=None,
                generated_hash=generated_hash,
                target_hash=None,
                diff=None,
                error=hint,
            )
    except OSError as exc:  # pragma: no cover - unexpected filesystem failures
        return DriftReport(
            target_path=target_path,
            in_sync=None,
            generated_hash=generated_hash,
            target_hash=None,
            diff=None,
            error=f"Unable to read {target_path}: {exc}",
        )

    target_hash = sha256(target_text.encode("utf-8")).hexdigest()

    if generated_hash == target_hash:
        return DriftReport(
            target_path=target_path,
            in_sync=True,
            generated_hash=generated_hash,
            target_hash=target_hash,
            diff=None,
            error=None,
        )

    diff_lines = difflib.unified_diff(
        target_text.splitlines(),
        generated_text.splitlines(),
        fromfile=str(target_path),
        tofile="generated",
        lineterm="",
    )
    limited: list[str] = []
    for idx, line in enumerate(diff_lines):
        if idx >= MAX_DIFF_LINES:
            limited.append("... diff truncated ...")
            break
        limited.append(line)
    diff = "\n".join(limited)

    return DriftReport(
        target_path=target_path,
        in_sync=False,
        generated_hash=generated_hash,
        target_hash=target_hash,
        diff=diff,
        error=None,
    )


def summarise_drift(report: DriftReport) -> str:
    if report.error:
        return f"Drift: {report.error}"
    if report.in_sync is True:
        return f"Drift: {report.target_path} matches the database"
    if report.in_sync is False:
        return f"Drift: differences detected for {report.target_path}"
    return "Drift: status unknown"
