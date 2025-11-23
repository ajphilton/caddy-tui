from pathlib import Path
import json

from caddy_tui import config, db, models
from caddy_tui.live_api import LiveApiStatus
from caddy_tui.models import SNAPSHOT_KIND_CADDY_TUI
from caddy_tui.status import collect_app_status, refresh_live_snapshot


def _reset_db(monkeypatch, tmp_path: Path) -> Path:
    db_path = tmp_path / "config.db"
    monkeypatch.setattr(config, "DB_PATH", db_path)
    monkeypatch.setattr(config, "APP_DIR", tmp_path)
    db._engine = None  # type: ignore[attr-defined]
    db._SessionLocal = None  # type: ignore[attr-defined]
    return db_path


def test_status_before_init(monkeypatch, tmp_path):
    db_path = _reset_db(monkeypatch, tmp_path)
    monkeypatch.setattr("caddy_tui.status.fetch_live_status", lambda *args, **kwargs: None)
    status = collect_app_status(db_path, refresh_sources=False, check_service=False)
    assert status.db_path == db_path
    assert status.db_exists
    assert status.db_ready
    assert status.last_import_path is None


def test_status_after_site_import(monkeypatch, tmp_path):
    db_path = _reset_db(monkeypatch, tmp_path)
    db.init_db(db_path)
    with db.session_scope(db_path) as session:
        config_row = models.Config(name="default", caddyfile_path="/etc/caddy/Caddyfile", last_imported_at="2025-01-01T00:00:00")
        session.add(config_row)
        session.flush()
        snapshot = models.ConfigSnapshot(config=config_row, source_kind=SNAPSHOT_KIND_CADDY_TUI)
        session.add(snapshot)
        session.flush()
        block = models.ServerBlock(snapshot=snapshot, block_index=0)
        session.add(block)
        session.flush()
        block.sites.append(
            models.ServerBlockSite(
                raw_label="example.com",
                host="example.com",
                port=None,
                scheme=None,
                is_ipv6=False,
                is_wildcard=False,
                label_index=0,
            )
        )
    monkeypatch.setattr("caddy_tui.status.fetch_live_status", lambda *args, **kwargs: None)
    status = collect_app_status(db_path, refresh_sources=False, check_service=False)
    assert status.db_exists
    assert status.db_ready
    assert status.last_import_path == "/etc/caddy/Caddyfile"
    assert status.last_import_time == "2025-01-01T00:00:00"
    assert status.block_count == 1
    snapshots = {snapshot.kind: snapshot for snapshot in status.snapshots}
    tui_snapshot = snapshots[SNAPSHOT_KIND_CADDY_TUI]
    assert tui_snapshot.block_count == 1
    assert tui_snapshot.site_count == 1


def test_status_includes_service_state(monkeypatch, tmp_path):
    db_path = _reset_db(monkeypatch, tmp_path)
    db.init_db(db_path)
    with db.session_scope(db_path) as session:
        config_row = models.Config(name="default", caddyfile_path="/etc/caddy/Caddyfile")
        session.add(config_row)
        session.flush()
        snapshot = models.ConfigSnapshot(config=config_row, source_kind=SNAPSHOT_KIND_CADDY_TUI)
        session.add(snapshot)
        session.flush()
        session.add(models.ServerBlock(snapshot=snapshot, block_index=0))

    monkeypatch.setattr(
        "caddy_tui.status.fetch_live_status",
        lambda *args, **kwargs: LiveApiStatus(state="live", block_count=2, caddyfile_text=None, format="json", json_payload="{}", error=None),
    )
    monkeypatch.setattr("caddy_tui.status.check_caddy_service", lambda: (_ for _ in ()).throw(AssertionError("helper fallback not expected")))
    status = collect_app_status(
        db_path,
        refresh_sources=False,
        live_caddyfile=Path("/etc/caddy/Caddyfile"),
    )
    assert status.service_status is not None
    assert status.service_status.state == "live"
    assert status.service_status.detail == "json"
    assert status.service_status.block_count == 2


def test_collect_app_status_bootstraps_caddyfile(monkeypatch, tmp_path):
    db_path = _reset_db(monkeypatch, tmp_path)
    db.init_db(db_path)
    caddyfile = tmp_path / "Caddyfile"
    caddyfile.write_text("example.com { respond \"ok\" }\n")
    monkeypatch.setattr("caddy_tui.status.fetch_live_status", lambda *args, **kwargs: None)
    monkeypatch.setattr("caddy_tui.importer.adapt_caddyfile", lambda path: {})
    status = collect_app_status(
        db_path,
        live_caddyfile=caddyfile,
        check_service=False,
    )
    snapshots = {snapshot.kind: snapshot for snapshot in status.snapshots}
    assert snapshots[models.SNAPSHOT_KIND_CADDY_TUI].available
    assert snapshots[models.SNAPSHOT_KIND_CADDYFILE].available
    assert status.last_import_path == str(caddyfile)


def test_refresh_live_snapshot_persists_data(monkeypatch, tmp_path):
    db_path = _reset_db(monkeypatch, tmp_path)
    monkeypatch.setattr(
        "caddy_tui.status.fetch_live_status",
        lambda *args, **kwargs: LiveApiStatus(
            state="live",
            block_count=1,
            caddyfile_text=None,
            format="json",
            json_payload=json.dumps(
                {
                    "apps": {
                        "http": {
                            "servers": {
                                "srv": {
                                    "routes": [
                                        {
                                            "match": [{"host": ["example.com"]}],
                                            "handle": [{"handler": "static_response", "body": "ok"}],
                                        }
                                    ]
                                }
                            }
                        }
                    }
                }
            ),
            error=None,
        ),
    )
    status = refresh_live_snapshot(db_path=db_path)
    snapshots = {snapshot.kind: snapshot for snapshot in status.snapshots}
    live_snapshot = snapshots[models.SNAPSHOT_KIND_CADDY_LIVE]
    assert live_snapshot.available
    assert live_snapshot.block_count == 1
