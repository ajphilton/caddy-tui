from pathlib import Path

from sqlalchemy import inspect

from caddy_tui import db


def test_init_db(tmp_path: Path):
    db_path = tmp_path / "config.db"
    db.init_db(db_path)
    engine = db.get_engine(db_path)
    insp = inspect(engine)
    tables = insp.get_table_names()
    assert "configs" in tables
    assert "server_blocks" in tables
