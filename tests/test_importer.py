from pathlib import Path
import json

import pytest

from caddy_tui import db
from caddy_tui.importer import import_caddyfile, find_caddyfile
from caddy_tui.models import Site


class DummyCaddyfile(Path):
    _flavour = type(Path())._flavour


def test_find_caddyfile(tmp_path: Path):
    caddyfile = tmp_path / "Caddyfile"
    caddyfile.write_text("localhost")
    assert find_caddyfile(caddyfile) == caddyfile
