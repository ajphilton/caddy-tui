"""Textual application placeholder."""
from __future__ import annotations

from textual.app import App, ComposeResult
from textual.containers import Horizontal
from textual.widgets import Footer, Header, Static, ListView, ListItem

from sqlalchemy import select

from .db import session_scope
from .models import Site


class SiteList(ListView):
    def __init__(self) -> None:
        super().__init__()
        self.refresh_items()

    def refresh_items(self) -> None:
        self.clear()
        with session_scope() as session:
            sites = session.scalars(select(Site)).all()
        if not sites:
            self.append(ListItem(Static("No sites imported")))
            return
        for site in sites:
            label = f"{site.label} ({'enabled' if site.enabled else 'disabled'})"
            self.append(ListItem(Static(label)))


class StatusPane(Static):
    DEFAULT_CSS = "StatusPane {height: 100%; border: round green;}"

    def on_mount(self) -> None:
        self.update("Ready")


class CaddyTuiApp(App):
    CSS = "Screen {background: #101010;}"

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal():
            yield SiteList()
            yield StatusPane()
        yield Footer()


def run_tui() -> None:
    app = CaddyTuiApp()
    app.run()
