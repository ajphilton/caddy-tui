from pathlib import Path

import pytest
from sqlalchemy import select

from caddy_tui import db
from caddy_tui.importer import (
    import_caddyfile,
    import_caddyfile_text,
    import_caddy_json_payload,
    find_caddyfile,
    DEFAULT_CADDYFILE_PATHS,
    CaddyfilePermissionError,
)
from caddy_tui.models import (
    Config,
    ConfigSnapshot,
    ServerBlock,
    ServerBlockSite,
    SNAPSHOT_KIND_CADDYFILE,
    SNAPSHOT_KIND_CADDY_LIVE,
    SNAPSHOT_KIND_CADDY_TUI,
)


def _reset_db(tmp_path: Path) -> Path:
    db._engine = None  # type: ignore[attr-defined]
    db._SessionLocal = None  # type: ignore[attr-defined]
    db_path = tmp_path / "config.db"
    db.init_db(db_path)
    return db_path


def test_find_caddyfile(tmp_path: Path):
    caddyfile = tmp_path / "Caddyfile"
    caddyfile.write_text("localhost")
    assert find_caddyfile(caddyfile) == caddyfile


def test_find_caddyfile_from_directory(tmp_path: Path):
    config_dir = tmp_path / "configs"
    config_dir.mkdir()
    caddyfile = config_dir / "Caddyfile"
    caddyfile.write_text("localhost")
    assert find_caddyfile(config_dir) == caddyfile


def test_find_caddyfile_walks_parents(tmp_path: Path):
    etc = tmp_path / "etc"
    etc.mkdir()
    (etc / "caddy").mkdir()
    caddyfile = etc / "Caddyfile"
    caddyfile.write_text("localhost")
    unexpected_path = etc / "caddy" / "missing.conf"
    assert find_caddyfile(unexpected_path) == caddyfile


def test_find_caddyfile_uses_defaults(monkeypatch, tmp_path: Path):
    caddyfile = tmp_path / "Caddyfile"
    caddyfile.write_text("localhost")
    monkeypatch.setattr("caddy_tui.importer.DEFAULT_CADDYFILE_PATHS", (caddyfile,))
    assert find_caddyfile(None) == caddyfile


def test_import_caddyfile_permission_error(tmp_path: Path, mocker):
    _reset_db(tmp_path)
    caddyfile = tmp_path / "Caddyfile"
    caddyfile.write_text("localhost")
    mocker.patch("caddy_tui.importer.find_caddyfile", return_value=caddyfile)
    mocker.patch("caddy_tui.importer.os.access", return_value=False)
    mocker.patch("caddy_tui.importer.stage_caddyfile_copy", return_value=(None, "sudo helper", "denied"))

    with pytest.raises(CaddyfilePermissionError) as excinfo:
        import_caddyfile(caddyfile)
    assert excinfo.value.path == caddyfile
    assert excinfo.value.suggested_command == "sudo helper"


def test_import_caddyfile_creates_site_per_host(tmp_path: Path, monkeypatch):
    _reset_db(tmp_path)
    caddyfile = tmp_path / "Caddyfile"
    caddyfile.write_text(
        """
cheapranker.com {
    respond "ok"
}

www.cheapranker.com {
    respond "ok"
}

bottega-natura.com, www.bottega-natura.com {
    respond "ok"
}
        """.strip()
    )

    monkeypatch.setattr("caddy_tui.importer.find_caddyfile", lambda explicit=None: caddyfile)
    monkeypatch.setattr("caddy_tui.importer.adapt_caddyfile", lambda path: {})

    summary = import_caddyfile(caddyfile)
    assert summary.site_count == 3
    assert summary.snapshot_kind == SNAPSHOT_KIND_CADDY_TUI
    assert summary.mirrored_snapshots == (SNAPSHOT_KIND_CADDYFILE,)
    expected = {
        "cheapranker.com",
        "www.cheapranker.com",
        "bottega-natura.com, www.bottega-natura.com",
    }
    assert set(summary.site_labels) == expected

    with db.session_scope() as session:
        config = session.scalar(select(Config))
        assert config is not None
        tui_snapshot = session.scalar(
            select(ConfigSnapshot)
            .where(
                ConfigSnapshot.config_id == config.id,
                ConfigSnapshot.source_kind == SNAPSHOT_KIND_CADDY_TUI,
            )
            .limit(1)
        )
        file_snapshot = session.scalar(
            select(ConfigSnapshot)
            .where(
                ConfigSnapshot.config_id == config.id,
                ConfigSnapshot.source_kind == SNAPSHOT_KIND_CADDYFILE,
            )
            .limit(1)
        )
        assert tui_snapshot is not None
        assert file_snapshot is not None
        labels = {
            site.raw_label
            for site in session.scalars(
                select(ServerBlockSite)
                .join(ServerBlock, ServerBlockSite.block)
                .where(ServerBlock.snapshot_id == tui_snapshot.id)
            )
        }
        assert "cheapranker.com" in labels
        assert "www.cheapranker.com" in labels
        assert "bottega-natura.com" in labels


def test_import_caddyfile_text_updates_snapshot(tmp_path: Path, monkeypatch):
    _reset_db(tmp_path)
    caddyfile = tmp_path / "Caddyfile"
    caddyfile.write_text("example.com { respond \"ok\" }")
    monkeypatch.setattr("caddy_tui.importer.find_caddyfile", lambda explicit=None: caddyfile)
    monkeypatch.setattr("caddy_tui.importer.adapt_caddyfile", lambda path: {})
    import_caddyfile(caddyfile)

    summary = import_caddyfile_text(
        "live.example.com {\n    respond \"ok\"\n}\n",
        source_label="admin-api",
        target_snapshot=SNAPSHOT_KIND_CADDYFILE,
    )
    assert summary is not None
    assert summary.site_count == 1

    with db.session_scope() as session:
        config = session.scalar(select(Config))
        assert config is not None
        snapshot = session.scalar(
            select(ConfigSnapshot)
            .where(
                ConfigSnapshot.config_id == config.id,
                ConfigSnapshot.source_kind == SNAPSHOT_KIND_CADDYFILE,
            )
            .limit(1)
        )
        assert snapshot is not None
        assert snapshot.source_path == "admin-api"
        assert len(snapshot.server_blocks) == 1


def test_import_caddyfile_text_creates_config(tmp_path: Path):
    db_path = _reset_db(tmp_path)
    summary = import_caddyfile_text(
        "example.com {\n    respond \"ok\"\n}\n",
        source_label="admin-api",
        target_snapshot=SNAPSHOT_KIND_CADDY_TUI,
        db_path=db_path,
    )
    assert summary is not None
    with db.session_scope(db_path=db_path) as session:
        config = session.scalar(select(Config))
        assert config is not None
        snapshot = session.scalar(
            select(ConfigSnapshot)
            .where(
                ConfigSnapshot.config_id == config.id,
                ConfigSnapshot.source_kind == SNAPSHOT_KIND_CADDY_TUI,
            )
            .limit(1)
        )
        assert snapshot is not None


def test_import_caddy_json_payload(tmp_path: Path):
    db_path = _reset_db(tmp_path)
    payload = {
        "apps": {
            "http": {
                "servers": {
                    "srv0": {
                        "listen": [":443"],
                        "routes": [
                            {
                                "match": [{"host": ["example.com", "www.example.com"]}],
                                "handle": [{"handler": "static_response", "body": "ok"}],
                            }
                        ],
                    }
                }
            }
        }
    }

    summary = import_caddy_json_payload(payload, source_label="admin-api", db_path=db_path)
    assert summary is not None
    assert summary.site_count == 1

    with db.session_scope(db_path=db_path) as session:
        config = session.scalar(select(Config))
        assert config is not None
        snapshot = session.scalar(
            select(ConfigSnapshot)
            .where(
                ConfigSnapshot.config_id == config.id,
                ConfigSnapshot.source_kind == SNAPSHOT_KIND_CADDY_LIVE,
            )
            .limit(1)
        )
        assert snapshot is not None
        assert len(snapshot.server_blocks) == 1
