from pathlib import Path

from caddy_tui import db
from caddy_tui.models import Site
from caddy_tui.exporter import generate_caddyfile


def test_generate_basic_caddyfile(tmp_path: Path):
    db_path = tmp_path / "config.db"
    db.init_db(db_path)
    with db.session_scope(db_path) as session:
        session.add(Site(label="example", address="example.com", enabled=True))
    target = tmp_path / "Caddyfile.generated"
    generate_caddyfile(target)
    assert "example.com" in target.read_text()
