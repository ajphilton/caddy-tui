from pathlib import Path

from caddy_tui import db
from caddy_tui.models import (
    Config,
    ConfigSnapshot,
    RawFragment,
    ServerBlock,
    ServerBlockSite,
    SNAPSHOT_KIND_CADDY_TUI,
)
from caddy_tui.exporter import generate_caddyfile, render_caddyfile_text


def test_generate_basic_caddyfile(tmp_path: Path):
    db_path = tmp_path / "config.db"
    db._engine = None  # type: ignore[attr-defined]
    db._SessionLocal = None  # type: ignore[attr-defined]
    db.init_db(db_path)
    sample_block = "example.com {\n    respond \"ok\"\n}\n"
    with db.session_scope(db_path) as session:
        config = Config(name="default", caddyfile_path="/etc/caddy/Caddyfile")
        session.add(config)
        session.flush()
        snapshot = ConfigSnapshot(config=config, source_kind=SNAPSHOT_KIND_CADDY_TUI)
        session.add(snapshot)
        session.flush()
        block = ServerBlock(snapshot=snapshot, block_index=0, raw_prelude=None, raw_postlude=None)
        session.add(block)
        session.flush()
        block.sites.append(
            ServerBlockSite(
                raw_label="example.com",
                host="example.com",
                port=None,
                scheme=None,
                is_ipv6=False,
                is_wildcard=False,
                label_index=0,
            )
        )
        block.fragments.append(RawFragment(fragment_index=0, kind="block", content=sample_block))

    target = tmp_path / "Caddyfile.generated"
    generate_caddyfile(target, db_path=db_path)
    assert sample_block.strip() in target.read_text()
    rendered = render_caddyfile_text(db_path=db_path)
    assert sample_block.strip() in rendered
