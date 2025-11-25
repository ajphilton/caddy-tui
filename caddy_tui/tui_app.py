"""Simple interactive terminal menu built with colorama and rich."""

from __future__ import annotations

import importlib
import os
import shlex
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

from colorama import Fore, Style, init as colorama_init
from rich.console import Console, RenderableType
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from .block_editor import blocks_to_text, load_caddy_tui_blocks, parse_single_block, save_caddy_tui_blocks
from .caddyfile_parser import ParsedBlock
from .config import LIVE_CADDYFILE
from .drift import compare_caddyfile, summarise_drift
from .exporter import generate_caddyfile
from .helper_runner import reload_caddy_service, restart_caddy_service
from .importer import CaddyfilePermissionError, import_caddyfile
from .models import SNAPSHOT_KIND_CADDYFILE, SNAPSHOT_KIND_CADDY_LIVE, SNAPSHOT_KIND_CADDY_TUI
from .snapshots import SNAPSHOT_LABELS, SnapshotBlockText, SnapshotComparison, load_snapshot_block_texts
from .status import AppStatus, ServiceStatus, SnapshotInfo, collect_app_status, refresh_live_snapshot
from .versioning import VersionInfo, collect_version_info


@dataclass(slots=True)
class MenuOption:
    key: str
    description: str
    handler: Callable[[], None]


class TerminalMenuApp:
    """Lightweight scrolling menu that prints status, results, and prompts."""

    def __init__(self) -> None:
        colorama_init(autoreset=True)
        self.console = Console()
        self.last_output: RenderableType | None = None
        self._last_output_once = False
        self.running = True
        self._refresh_live_next = True
        self._latest_status: AppStatus | None = None
        self._version_info: VersionInfo | None = None
        self._version_last_attempt: float | None = None

    def run(self) -> None:
        """Enter the interactive loop."""
        try:
            while self.running:
                status = collect_app_status(
                    live_caddyfile=LIVE_CADDYFILE,
                    refresh_live=self._refresh_live_next,
                )
                self._refresh_live_next = False
                self._latest_status = status
                self._get_version_info()
                options = self._build_menu(status)
                self._render_cycle(status, options)
                choice = input(f"{Fore.CYAN}Select option{Style.RESET_ALL}: ").strip().lower()
                if not choice:
                    continue
                option = next((opt for opt in options if opt.key == choice), None)
                if option is None:
                    self._set_message(f"Unknown option '{choice}'.", style="red")
                    continue
                try:
                    option.handler()
                except KeyboardInterrupt:
                    self._set_message("Action cancelled.", style="yellow")
        except KeyboardInterrupt:
            self.console.print("\nExiting...")

    # Rendering helpers

    def _render_cycle(self, status: AppStatus, options: list[MenuOption]) -> None:
        self.console.rule("caddy-tui")
        if self.last_output:
            self.console.print(self.last_output)
            self.console.print()
            if self._last_output_once:
                self.last_output = None
                self._last_output_once = False
        self.console.print(self._status_table(status))
        self.console.print()
        self.console.print("Menu:")
        for option in options:
            self.console.print(f"  [cyan]{option.key}[/cyan] — {option.description}")

    def _status_table(self, status: AppStatus) -> Table:
        table = Table(title="Status", show_lines=True)
        table.add_column("Field", style="bold")
        table.add_column("Value")
        table.add_row("DB path", str(status.db_path))
        table.add_row("DB ready", "yes" if status.db_ready else "no")
        table.add_row("Version", self._version_status_text())
        table.add_row("Stored blocks", str(status.block_count))
        if status.last_import_path:
            when = status.last_import_time or "time unknown"
            table.add_row("Last import", f"{status.last_import_path} ({when})")
        else:
            table.add_row("Last import", "Never — run an import")
        table.add_row("Snapshots", self._snapshot_matrix(status))
        for comparison in status.comparisons:
            label = f"{SNAPSHOT_LABELS[comparison.left_kind]} ↔ {SNAPSHOT_LABELS[comparison.right_kind]}"
            table.add_row(
                f"Diff ({label})",
                self._format_comparison(comparison),
            )
        return table

    def _snapshot_matrix(self, status: AppStatus) -> Table:
        matrix = Table(show_header=False, box=None, pad_edge=False)
        for _ in range(4):
            matrix.add_column(justify="center", ratio=1)

        snapshot_map = {snapshot.kind: snapshot for snapshot in status.snapshots}
        colors = self._snapshot_colors(status)
        status_cells: list[Text] = []
        detail_cells: list[Text] = []

        ordered = [
            (SNAPSHOT_KIND_CADDY_TUI, "caddy-tui"),
            (SNAPSHOT_KIND_CADDYFILE, "caddyfile"),
            (SNAPSHOT_KIND_CADDY_LIVE, "live caddy"),
        ]
        for kind, label in ordered:
            snapshot = snapshot_map.get(kind)
            status_cells.append(self._snapshot_status_cell(label, snapshot, colors.get(kind)))
            detail_cells.append(self._snapshot_detail_cell(snapshot))

        status_cells.append(self._service_status_cell(status))
        detail_cells.append(self._service_detail_cell(status))

        matrix.add_row(*status_cells)
        matrix.add_row(*detail_cells)
        return matrix

    @staticmethod
    def _snapshot_status_cell(label: str, snapshot: SnapshotInfo | None, color: str | None) -> Text:
        if snapshot and snapshot.available:
            style = color or "green"
            return Text(label, style=style)
        return Text(f"{label} (missing)", style=color or "grey50")

    @staticmethod
    def _snapshot_detail_cell(snapshot: SnapshotInfo | None) -> Text:
        if not snapshot or not snapshot.available:
            return Text("—", style="grey50")
        detail = f"{snapshot.block_count} block(s)"
        if snapshot.error:
            detail = f"{detail} • error: {snapshot.error}"
        return Text(detail)

    def _build_menu(self, status: AppStatus) -> list[MenuOption]:
        options: list[MenuOption] = [
            MenuOption("f", "Write Caddyfile ➜ caddy-tui", self._write_caddyfile_over_tui),
            MenuOption("t", "Write caddy-tui ➜ Caddyfile", self._write_tui_over_caddyfile),
            MenuOption("r", "Refresh live snapshot", self._refresh_live_snapshot),
            MenuOption("b", "Show snapshot blocks", self._show_snapshot_blocks),
            MenuOption("p", "Print live Caddyfile", self._print_live_caddyfile),
            MenuOption("h", "List CLI commands", self._show_cli_commands),
        ]
        version_info = self._version_info
        if version_info and version_info.update_available and version_info.latest:
            options.append(MenuOption("u", f"Update available → {version_info.latest}", self._show_update_instructions))
        options.append(MenuOption("n", "Add caddy-tui block", self._add_caddy_tui_block))
        options.append(MenuOption("e", "Edit caddy-tui block", self._edit_caddy_tui_block))
        options.append(MenuOption("x", "Delete caddy-tui block", self._delete_caddy_tui_block))
        service_state = status.service_status.state if status.service_status else None
        if service_state == "down":
            options.append(MenuOption("s", "Restart Caddy (sudo)", self._restart_caddy))
        else:
            options.append(MenuOption("c", "Reload Caddy (sudo)", self._reload_caddy))
        if status.last_import_path:
            options.append(
                MenuOption(
                    "d",
                    "Show drift diff",
                    lambda path=status.last_import_path, db_path=status.db_path: self._show_drift(Path(path), db_path),
                )
            )
        options.append(MenuOption("q", "Quit", self._quit))
        return options

    # Actions

    def _write_caddyfile_over_tui(self) -> None:
        explicit_path: Path | None = None
        while True:
            try:
                summary = import_caddyfile(explicit_path, helper_interactive=True)
            except FileNotFoundError:
                explicit_path = self._prompt_for_path("Path to Caddyfile")
                if explicit_path is None:
                    self._set_message("Write cancelled.", style="yellow")
                    return
                continue
            except CaddyfilePermissionError as exc:
                self._set_message(str(exc), style="yellow")
                return
            except Exception as exc:  # pragma: no cover - unexpected failures bubble to user
                self._set_message(f"Failed to load Caddyfile: {exc}", style="red")
                return
            else:
                self._set_message(
                    f"Wrote Caddyfile ➜ caddy-tui ({summary.site_count} block(s))",
                    style="green",
                )
                self._schedule_live_refresh()
                return

    def _write_tui_over_caddyfile(self) -> None:
        status = self._latest_status
        default_path = Path(status.last_import_path) if status and status.last_import_path else None
        target = self._prompt_for_path("Overwrite which Caddyfile?", default=default_path)
        if target is None:
            self._set_message("Write cancelled.", style="yellow")
            return
        try:
            generate_caddyfile(target)
        except PermissionError as exc:
            self._set_message(str(exc), style="red")
            return
        except Exception as exc:  # pragma: no cover - unexpected failures bubble to user
            self._set_message(f"Failed to write Caddyfile: {exc}", style="red")
            return
        self._set_message(f"Wrote caddy-tui ➜ {target}", style="green")
        self._schedule_live_refresh()

    def _prompt_for_path(self, prompt: str, default: Path | None = None) -> Path | None:
        label = prompt
        if default:
            label += f" [{default}]"
        label += ": "
        response = input(label).strip()
        if not response:
            return default
        return Path(response).expanduser()

    def _show_drift(self, target: Path, db_path: Path) -> None:
        report = compare_caddyfile(target, db_path=db_path)
        if report.error:
            self._set_message(f"Unable to compare drift: {report.error}", style="red")
            return
        summary = summarise_drift(report)
        diff_text = report.diff or "No differences detected."
        border = "red" if report.diff else "green"
        panel = Panel(diff_text, title=summary, border_style=border, expand=False)
        self._set_renderable(panel)

    def _quit(self) -> None:
        self.running = False
        self._set_message("Goodbye!", style="green")

    def _show_update_instructions(self) -> None:
        info = self._get_version_info(force=True)
        if info is None:
            self._set_message("Unable to contact GitHub for version info. Try again later.", style="red")
            return
        if not info.update_available or not info.latest:
            self._set_message(f"Already running the latest version ({info.current}).", style="green")
            return
        pip_cmd = "pip install --upgrade caddy-tui"
        pipx_cmd = "pipx upgrade caddy-tui"
        instructions = (
            f"Current version: {info.current}\n"
            f"Latest release: {info.latest}\n\n"
            "Upgrade with either command:\n"
            f"  {pip_cmd}\n"
            f"  {pipx_cmd}\n"
            "(use pipx if you installed via pipx)"
        )
        panel = Panel(
            instructions,
            title="Update available",
            border_style="yellow",
        )
        self._set_renderable(panel)

    # Output helpers

    def _set_message(self, message: str, *, style: str | None = None, persist: bool = True) -> None:
        text = Text(message, style=style)
        self.last_output = text
        self._last_output_once = not persist
        if persist:
            self.console.print()
            self.console.print(text)

    def _set_renderable(self, renderable: RenderableType, *, persist: bool = True) -> None:
        self.last_output = renderable
        self._last_output_once = not persist
        if persist:
            self.console.print()
            self.console.print(renderable)

    @staticmethod
    def _format_comparison(comparison: SnapshotComparison) -> str:
        if comparison.status == "match":
            return "[green]match[/green]"
        if comparison.status == "different":
            mismatch = comparison.mismatch_count or 0
            suffix = f"{mismatch} block{'s' if mismatch != 1 else ''} differ"
            return f"[red]different[/red] ({suffix})"
        return "[yellow]missing[/yellow]"

    def _version_status_text(self) -> str:
        info = self._version_info
        if info is None:
            return "checking…"
        if info.update_available and info.latest:
            return f"[red]{info.current}[/red] → [yellow]{info.latest}[/yellow] (press 'u')"
        return f"[green]{info.current}[/green]"

    def _service_status_cell(self, status: AppStatus) -> Text:
        service = status.service_status
        if service is None:
            return Text("caddy service (n/a)", style="grey50")
        label = f"caddy service ({service.state})"
        return Text(label, style=self._service_color(status, service))

    def _service_detail_cell(self, status: AppStatus) -> Text:
        service = status.service_status
        if service is None:
            return Text("configure CADDY_TUI_ADMIN_ENDPOINT", style="grey50")
        parts: list[str] = []
        if service.block_count is not None:
            parts.append(f"{service.block_count} block(s)")
        if service.detail and service.detail.lower() not in {"live", "down", "unknown"}:
            parts.append(service.detail)
        if service.error:
            parts.append(f"error: {service.error}")
        if not parts:
            parts.append("no data")
        return Text(" • ".join(parts), style=self._service_color(status, service))

    def _snapshot_colors(self, status: AppStatus) -> dict[str, str | None]:
        snapshot_map = {snapshot.kind: snapshot for snapshot in status.snapshots}
        colors: dict[str, str | None] = {}
        for kind in (
            SNAPSHOT_KIND_CADDY_TUI,
            SNAPSHOT_KIND_CADDYFILE,
            SNAPSHOT_KIND_CADDY_LIVE,
        ):
            snap = snapshot_map.get(kind)
            if not snap or not snap.available:
                colors[kind] = "grey50"
            else:
                colors[kind] = None

        def _comparison_status(left: str, right: str) -> str | None:
            key = frozenset((left, right))
            for comparison in status.comparisons:
                if frozenset((comparison.left_kind, comparison.right_kind)) == key:
                    return comparison.status
            return None

        ct_cf = _comparison_status(SNAPSHOT_KIND_CADDY_TUI, SNAPSHOT_KIND_CADDYFILE)
        ct_live = _comparison_status(SNAPSHOT_KIND_CADDY_TUI, SNAPSHOT_KIND_CADDY_LIVE)

        tui_snap = snapshot_map.get(SNAPSHOT_KIND_CADDY_TUI)
        caddyfile_snap = snapshot_map.get(SNAPSHOT_KIND_CADDYFILE)
        live_snap = snapshot_map.get(SNAPSHOT_KIND_CADDY_LIVE)

        if tui_snap and tui_snap.available and caddyfile_snap and caddyfile_snap.available:
            if ct_cf == "match":
                colors[SNAPSHOT_KIND_CADDY_TUI] = "green"
                colors[SNAPSHOT_KIND_CADDYFILE] = "green"
            elif ct_cf == "different":
                colors[SNAPSHOT_KIND_CADDY_TUI] = "red"
                colors[SNAPSHOT_KIND_CADDYFILE] = "red"
            else:
                colors[SNAPSHOT_KIND_CADDY_TUI] = colors[SNAPSHOT_KIND_CADDYFILE] = "yellow"

        if not live_snap or not live_snap.available:
            colors[SNAPSHOT_KIND_CADDY_LIVE] = "grey50"
            return colors

        if ct_cf == "different":
            colors[SNAPSHOT_KIND_CADDY_LIVE] = "orange1"
        else:
            if ct_live == "match":
                colors[SNAPSHOT_KIND_CADDY_LIVE] = "green"
            elif ct_live == "different":
                colors[SNAPSHOT_KIND_CADDY_LIVE] = "red"
            else:
                colors[SNAPSHOT_KIND_CADDY_LIVE] = "yellow"

        return colors

    @staticmethod
    def _has_disagreement(status: AppStatus) -> bool:
        return any(comparison.status == "different" for comparison in status.comparisons)

    def _service_color(self, status: AppStatus, service: "ServiceStatus") -> str:
        if service.state == "down":
            return "red"
        if service.state != "live":
            return "yellow"
        return "green" if not self._has_disagreement(status) else "orange1"

    def _get_version_info(self, *, force: bool = False) -> VersionInfo | None:
        if self._version_info is not None and not force:
            return self._version_info
        now = time.monotonic()
        if not force and self._version_last_attempt and (now - self._version_last_attempt) < 300:
            return self._version_info
        return self._refresh_version_info()

    def _refresh_version_info(self) -> VersionInfo | None:
        self._version_last_attempt = time.monotonic()
        try:
            info = collect_version_info()
        except Exception:
            return None
        self._version_info = info
        return info

    # Live refresh helpers

    def _schedule_live_refresh(self) -> None:
        self._refresh_live_next = True

    def _refresh_live_snapshot(self) -> None:
        info = refresh_live_snapshot(live_caddyfile=LIVE_CADDYFILE)
        live_snapshot = next((snap for snap in info.snapshots if snap.kind == SNAPSHOT_KIND_CADDY_LIVE), None)
        if live_snapshot and live_snapshot.available:
            details = f"{live_snapshot.block_count} block(s)"
            if live_snapshot.collected_at:
                details = f"{details} @ {live_snapshot.collected_at}"
            self._set_message(f"Live snapshot refreshed ({details}).", style="green")
        elif live_snapshot and live_snapshot.error:
            self._set_message(f"Live snapshot refresh failed: {live_snapshot.error}", style="red")
        else:
            self._set_message("Live snapshot refresh failed: no data returned.", style="red")

    def _print_live_caddyfile(self) -> None:
        status = refresh_live_snapshot(live_caddyfile=LIVE_CADDYFILE)
        self._latest_status = status
        live_snapshot = next((snap for snap in status.snapshots if snap.kind == SNAPSHOT_KIND_CADDY_LIVE), None)
        snapshot_text = self._live_snapshot_caddyfile_text(status)
        source_hint = live_snapshot.source_path if live_snapshot and live_snapshot.source_path else None
        if snapshot_text:
            title = "Live Caddyfile"
            if source_hint:
                title = f"{title} ({source_hint})"
            panel = Panel(snapshot_text, title=title, border_style="cyan")
            self._set_renderable(panel, persist=False)
            return

        fallback_text, error = self._read_live_caddyfile_from_disk()
        if fallback_text:
            title = f"Live Caddyfile ({LIVE_CADDYFILE})" if LIVE_CADDYFILE else "Live Caddyfile"
            panel = Panel(fallback_text, title=title, border_style="cyan")
            self._set_renderable(panel, persist=False)
            return

        hint = error or (live_snapshot.error if live_snapshot else "snapshot unavailable")
        self._set_message(f"Unable to load live Caddyfile: {hint}", style="red")

    def _reload_caddy(self) -> None:
        success, command, error = reload_caddy_service(None)
        if not success:
            hint = error or command or "reload failed"
            self._set_message(f"Reload failed: {hint}", style="red")
            return
        printable = command or "helper reload"
        self._set_message(f"Reload requested via {printable}.", style="green")
        self._schedule_live_refresh()

    def _restart_caddy(self) -> None:
        success, command, error = restart_caddy_service(None)
        if not success:
            hint = error or command or "restart failed"
            self._set_message(f"Restart failed: {hint}", style="red")
            return
        printable = command or "helper restart"
        self._set_message(f"Restart requested via {printable}.", style="green")
        self._schedule_live_refresh()

    def _show_snapshot_blocks(self) -> None:
        status = self._latest_status
        if status is None or not status.db_ready:
            self._set_message("No snapshot data available. Run an import first.", style="yellow")
            return

        kinds = [
            (SNAPSHOT_KIND_CADDY_TUI, "caddy-tui"),
            (SNAPSHOT_KIND_CADDYFILE, "caddyfile"),
            (SNAPSHOT_KIND_CADDY_LIVE, "live caddy"),
        ]

        blocks_by_kind: dict[str, list[SnapshotBlockText]] = {}
        block_maps: dict[str, dict[int, SnapshotBlockText]] = {}
        for kind, _label in kinds:
            blocks = sorted(load_snapshot_block_texts(status.db_path, kind), key=lambda b: b.block_index)
            blocks_by_kind[kind] = blocks
            block_maps[kind] = {block.block_index: block for block in blocks}

        if not any(blocks_by_kind.values()):
            self._set_message("No snapshot blocks to display.", style="yellow")
            return

        table = Table(title="Snapshot Blocks", show_lines=True, expand=True)
        table.add_column("Key", style="bold cyan", justify="center", width=20)
        for _kind, label in kinds:
            table.add_column(label, overflow="fold", no_wrap=False, justify="left")

        live_blocks = blocks_by_kind.get(SNAPSHOT_KIND_CADDY_LIVE, [])

        if live_blocks:
            other_kinds = [kind for kind, _label in kinds if kind != SNAPSHOT_KIND_CADDY_LIVE]
            match_results: dict[str, dict[int, SnapshotBlockText]] = {}
            leftovers: dict[str, list[SnapshotBlockText]] = {}
            for kind in other_kinds:
                matches, remaining = self._match_blocks_by_tokens(live_blocks, blocks_by_kind.get(kind, []))
                match_results[kind] = matches
                leftovers[kind] = remaining

            for live_block in live_blocks:
                key_label = self._block_label(live_block)
                cells = [Text(key_label, style="bold cyan")]
                for kind, _label in kinds:
                    if kind == SNAPSHOT_KIND_CADDY_LIVE:
                        cells.append(self._format_block_cell(live_block))
                        continue
                    match = match_results.get(kind, {}).get(live_block.block_index)
                    if match:
                        cells.append(self._format_block_cell(match))
                    else:
                        cells.append(Text("—", style="grey50"))
                table.add_row(*cells)

            for kind in other_kinds:
                for block in leftovers.get(kind, []):
                    label = f"{SNAPSHOT_LABELS[kind]}(?)"
                    cells = [Text(label, style="bold cyan")]
                    for loop_kind, _label in kinds:
                        if loop_kind == kind:
                            cells.append(self._format_block_cell(block))
                        else:
                            cells.append(Text("—", style="grey50"))
                    table.add_row(*cells)
        else:
            block_indexes: set[int] = set()
            for block_map in block_maps.values():
                block_indexes.update(block_map.keys())

            for block_index in sorted(block_indexes):
                representative = next((bm.get(block_index) for bm in block_maps.values() if bm.get(block_index)), None)
                key_label = self._block_label(representative)
                cells = [Text(key_label, style="bold cyan")]
                for kind, _label in kinds:
                    entry = block_maps.get(kind, {}).get(block_index)
                    cells.append(self._format_block_cell(entry))
                table.add_row(*cells)

        self._set_renderable(table, persist=False)

    def _show_cli_commands(self) -> None:
        try:
            import click  # type: ignore
        except ImportError as exc:  # pragma: no cover - click is required at runtime
            self._set_message(f"CLI metadata unavailable: {exc}", style="red")
            return

        try:
            cli_module = importlib.import_module("caddy_tui.cli")
        except Exception as exc:  # pragma: no cover - import errors surface to UI
            self._set_message(f"Failed to load CLI definitions: {exc}", style="red")
            return

        main = getattr(cli_module, "main", None)
        if not isinstance(getattr(main, "commands", None), dict):
            self._set_message("CLI definitions missing.", style="red")
            return

        root_ctx = click.Context(main)
        table = Table(title="CLI Commands", show_lines=True, expand=True)
        table.add_column("Command", style="bold cyan", width=18)
        table.add_column("Usage", overflow="fold")
        table.add_column("Description", overflow="fold")

        for name in sorted(main.commands.keys()):
            command = main.commands[name]
            cmd_ctx = click.Context(command, parent=root_ctx)
            usage_line = command.get_usage(cmd_ctx).strip()
            usage_formatted = self._format_cli_usage(usage_line, name)
            description = command.help or command.short_help or command.get_short_help_str() or "—"
            table.add_row(f"caddy-tui {name}", usage_formatted, description)

        self._set_renderable(table, persist=False)

    @staticmethod
    def _format_cli_usage(raw_usage: str, command_name: str) -> str:
        if not raw_usage:
            return f"caddy-tui {command_name}"
        first_line = raw_usage.splitlines()[0]
        if first_line.lower().startswith("usage:"):
            first_line = first_line[6:].strip()
        if first_line.startswith("main"):
            first_line = "caddy-tui" + first_line[len("main") :]
        return first_line.strip()

    def _add_caddy_tui_block(self) -> None:
        result = self._load_blocks_for_editor(require_existing=False)
        if result is None:
            return
        db_path, blocks = result
        template = "example.com {\n    respond \"hello\"\n}\n"
        content = self._launch_editor(template)
        if content is None:
            return
        try:
            new_block = parse_single_block(content)
        except Exception as exc:
            self._set_message(f"Invalid block: {exc}", style="red")
            return
        blocks.append(new_block)
        try:
            save_caddy_tui_blocks(blocks, db_path)
        except Exception as exc:
            blocks.pop()
            self._set_message(f"Failed to save block: {exc}", style="red")
            return
        self._set_message(f"Added block #{len(blocks)} to caddy-tui snapshot.", style="green")

    def _edit_caddy_tui_block(self) -> None:
        result = self._load_blocks_for_editor(require_existing=True)
        if result is None:
            return
        db_path, blocks = result
        self._display_caddy_tui_blocks(blocks, title="Edit block")
        selection = self._prompt_block_selection(len(blocks), "edit")
        if selection is None:
            return
        current = blocks[selection]
        content = blocks_to_text([current]) or ""
        edited = self._launch_editor(content)
        if edited is None:
            return
        if edited == content:
            self._set_message("No changes made.", style="yellow")
            return
        try:
            replacement = parse_single_block(edited)
        except Exception as exc:
            self._set_message(f"Invalid block: {exc}", style="red")
            return
        original = blocks[selection]
        blocks[selection] = replacement
        try:
            save_caddy_tui_blocks(blocks, db_path)
        except Exception as exc:
            blocks[selection] = original
            self._set_message(f"Failed to save edits: {exc}", style="red")
            return
        self._set_message(f"Updated block #{selection + 1}.", style="green")

    def _delete_caddy_tui_block(self) -> None:
        result = self._load_blocks_for_editor(require_existing=True)
        if result is None:
            return
        db_path, blocks = result
        self._display_caddy_tui_blocks(blocks, title="Delete block")
        selection = self._prompt_block_selection(len(blocks), "delete")
        if selection is None:
            return
        confirm = input(f"Delete block #{selection + 1}? (y/N): ").strip().lower()
        if confirm not in {"y", "yes"}:
            self._set_message("Delete cancelled.", style="yellow")
            return
        removed = blocks.pop(selection)
        try:
            save_caddy_tui_blocks(blocks, db_path)
        except Exception as exc:
            blocks.insert(selection, removed)
            self._set_message(f"Failed to delete block: {exc}", style="red")
            return
        self._set_message(f"Deleted block #{selection + 1}.", style="green")

    def _load_blocks_for_editor(self, *, require_existing: bool) -> tuple[Path, list[ParsedBlock]] | None:
        status = self._latest_status
        if status is None or not status.db_ready:
            self._set_message("Database is not initialised. Run an import first.", style="yellow")
            return None
        db_path = status.db_path
        blocks = load_caddy_tui_blocks(db_path)
        if require_existing and not blocks:
            self._set_message("No caddy-tui snapshot available. Run an import first.", style="yellow")
            return None
        return db_path, list(blocks)

    def _display_caddy_tui_blocks(self, blocks: list[ParsedBlock], *, title: str) -> None:
        if not blocks:
            table = Table(title=title)
            table.add_column("#")
            table.add_column("Labels")
            table.add_column("Preview")
            table.add_row("—", "—", "No blocks available")
            self._set_renderable(table)
            return
        table = Table(title=title, show_lines=False)
        table.add_column("#", style="bold cyan", width=4, justify="right")
        table.add_column("Labels", style="bold", no_wrap=True)
        table.add_column("Preview", overflow="fold")
        for idx, block in enumerate(blocks, start=1):
            label = ", ".join(block.labels) if block.labels else "(global)"
            preview = self._parsed_block_preview(block)
            table.add_row(str(idx), label, preview)
        self._set_renderable(table)

    def _parsed_block_preview(self, block: ParsedBlock, *, max_length: int = 80) -> str:
        text = blocks_to_text([block]).strip()
        for line in text.splitlines():
            candidate = line.strip()
            if not candidate or candidate.startswith("#"):
                continue
            snippet = candidate
            break
        else:
            snippet = "(empty)"
        if len(snippet) > max_length:
            return snippet[: max_length - 1] + "…"
        return snippet

    def _prompt_block_selection(self, total: int, action: str) -> int | None:
        if total <= 0:
            self._set_message("No blocks available.", style="yellow")
            return None
        prompt = f"Select block number to {action} [1-{total}] (blank to cancel): "
        while True:
            choice = input(prompt).strip()
            if not choice:
                self._set_message(f"{action.capitalize()} cancelled.", style="yellow")
                return None
            if not choice.isdigit():
                self._set_message("Enter a numeric block index.", style="yellow")
                continue
            index = int(choice)
            if not 1 <= index <= total:
                self._set_message("Block number out of range.", style="yellow")
                continue
            return index - 1

    def _launch_editor(self, initial_text: str) -> str | None:
        command = self._resolve_editor_command()
        if command is None:
            return None
        initial = initial_text if initial_text.endswith("\n") else f"{initial_text}\n"
        with tempfile.NamedTemporaryFile("w+", delete=False, encoding="utf-8") as handle:
            handle.write(initial)
            handle.flush()
            temp_path = handle.name
        try:
            try:
                result = subprocess.run([*command, temp_path])
            except FileNotFoundError:
                self._set_message(f"Editor command not found: {command[0]}", style="red")
                return None
            except Exception as exc:
                self._set_message(f"Failed to launch editor: {exc}", style="red")
                return None
            if result.returncode != 0:
                self._set_message(f"Editor exited with status {result.returncode}.", style="red")
                return None
            with open(temp_path, "r", encoding="utf-8") as handle:
                return handle.read()
        finally:
            try:
                os.unlink(temp_path)
            except OSError:
                pass

    def _resolve_editor_command(self) -> list[str] | None:
        candidates: list[str] = []
        env_editor = os.environ.get("EDITOR")
        if env_editor:
            candidates.append(env_editor)
        candidates.extend(["nano", "vi", "vim"])
        for candidate in candidates:
            if not candidate:
                continue
            try:
                parts = shlex.split(candidate)
            except ValueError:
                continue
            executable = parts[0]
            if os.path.isabs(executable) or "/" in executable or shutil.which(executable):
                return parts
        self._set_message("Set $EDITOR or install nano/vi for editing.", style="red")
        return None

    @staticmethod
    def _block_label(block: SnapshotBlockText | None) -> str:
        if block is None:
            return "—"
        if block.key:
            return ", ".join(block.key)
        return str(block.block_index + 1)

    def _format_block_cell(
        self,
        block: SnapshotBlockText | None,
    ) -> Text:
        if block is None:
            return Text("—", style="grey50")
        display = block.text or "(empty)"
        return Text(display)

    @classmethod
    def _match_blocks_by_tokens(
        cls,
        source_blocks: list[SnapshotBlockText],
        target_blocks: list[SnapshotBlockText],
    ) -> tuple[dict[int, SnapshotBlockText], list[SnapshotBlockText]]:
        if not source_blocks or not target_blocks:
            return {}, target_blocks

        target_entries = [
            (block, cls._block_search_blob(block))
            for block in target_blocks
        ]
        available = list(target_entries)
        matches: dict[int, SnapshotBlockText] = {}

        for source in source_blocks:
            source_tokens = cls._block_tokens(source)
            if not source_tokens:
                continue
            best_idx = None
            best_score = 0.0
            for idx, (candidate, candidate_blob) in enumerate(available):
                score = cls._token_overlap_score(source_tokens, candidate_blob)
                if score > best_score:
                    best_score = score
                    best_idx = idx
            if best_idx is not None and best_score > 0:
                match_block, _blob = available.pop(best_idx)
                matches[source.block_index] = match_block

        leftovers = [entry[0] for entry in available]
        return matches, leftovers

    def _live_snapshot_caddyfile_text(self, status: AppStatus | None) -> str | None:
        if status is None or not status.db_ready:
            return None
        blocks = load_snapshot_block_texts(status.db_path, SNAPSHOT_KIND_CADDY_LIVE)
        if not blocks:
            return None
        ordered = sorted(blocks, key=lambda block: block.block_index)
        joined = "\n\n".join(block.text.strip() or block.text for block in ordered if block.text)
        return joined.strip() or None

    def _read_live_caddyfile_from_disk(self) -> tuple[str | None, str | None]:
        if not LIVE_CADDYFILE:
            return None, "LIVE_CADDYFILE path not configured"
        path = LIVE_CADDYFILE
        try:
            text = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None, f"{path} not found"
        except PermissionError as exc:
            return None, f"Permission denied reading {path}: {exc}"
        except OSError as exc:
            return None, f"Unable to read {path}: {exc}"
        return text, None

    @staticmethod
    def _block_tokens(block: SnapshotBlockText | None) -> set[str]:
        tokens: set[str] = set()
        if block is None:
            return tokens

        def _extend(values: Iterable[str]) -> None:
            for value in values:
                trimmed = value.strip().lower()
                if trimmed:
                    tokens.add(trimmed)

        _extend(block.hosts)
        _extend(block.paths)
        _extend(block.groups)
        _extend(block.roots)
        _extend(block.dials)
        _extend(block.locations)
        _extend(block.encodings)
        _extend(block.status_codes)
        _extend(block.handlers)
        _extend(block.key)
        return tokens

    @staticmethod
    def _block_search_blob(block: SnapshotBlockText | None) -> str:
        if block is None:
            return ""

        segments: list[str] = []

        def _extend(values: Iterable[str]) -> None:
            for value in values:
                trimmed = value.strip().lower()
                if trimmed:
                    segments.append(trimmed)

        _extend(block.hosts)
        _extend(block.paths)
        _extend(block.groups)
        _extend(block.roots)
        _extend(block.dials)
        _extend(block.locations)
        _extend(block.encodings)
        _extend(block.status_codes)
        _extend(block.handlers)
        _extend(block.key)
        if block.text:
            segments.append(block.text.lower())
        return "\n".join(segments)

    @staticmethod
    def _token_overlap_score(source_tokens: list[str], candidate_blob: str) -> float:
        if not source_tokens:
            return 0.0
        if not candidate_blob:
            return 0.0
        matches = 0
        for token in source_tokens:
            if token and token in candidate_blob:
                matches += 1
        return matches / len(source_tokens)


def run_tui() -> None:
    TerminalMenuApp().run()
