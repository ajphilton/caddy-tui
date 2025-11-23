from pathlib import Path

from sqlalchemy import select

from caddy_tui import db
from caddy_tui.importer import import_caddyfile
from caddy_tui.models import (
    Config,
    ConfigSnapshot,
    RawFragment,
    ServerBlock,
    ServerBlockSite,
    SNAPSHOT_KIND_CADDYFILE,
    SNAPSHOT_KIND_CADDY_TUI,
)
from caddy_tui.exporter import render_caddyfile_text
from caddy_tui.drift import compare_caddyfile, summarise_drift
from caddy_tui import status as status_mod


def _reset_db(tmp_path: Path, sample: str | None = None) -> Path:
    db._engine = None  # type: ignore[attr-defined]
    db._SessionLocal = None  # type: ignore[attr-defined]
    db_path = tmp_path / "config.db"
    db.init_db(db_path)
    block_text = sample or "example.com {\n    respond \"ok\"\n}\n"
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
        block.fragments.append(RawFragment(fragment_index=0, kind="block", content=block_text))
    return db_path


def test_compare_caddyfile_in_sync(tmp_path: Path):
    db_path = _reset_db(tmp_path)
    target = tmp_path / "Caddyfile"
    target.write_text(render_caddyfile_text(db_path=db_path))

    report = compare_caddyfile(target, db_path=db_path)
    assert report.in_sync is True
    assert report.error is None
    assert report.diff is None


def test_compare_caddyfile_detects_drift(tmp_path: Path):
    db_path = _reset_db(tmp_path)
    target = tmp_path / "Caddyfile"
    target.write_text("broken")

    report = compare_caddyfile(target, db_path=db_path)
    assert report.in_sync is False
    assert report.diff and "broken" in report.diff
    assert report.error is None


def test_summarise_drift_respects_error(tmp_path: Path, monkeypatch):
    db_path = _reset_db(tmp_path)
    target = tmp_path / "Caddyfile"

    def _denied(*_args, **_kwargs):
        raise PermissionError

    monkeypatch.setattr(Path, "read_text", _denied)
    monkeypatch.setattr(
        "caddy_tui.drift.stage_caddyfile_copy",
        lambda path: (None, "sudo helper", "Permission denied"),
    )
    report = compare_caddyfile(target, db_path=db_path)
    assert "Permission denied" in summarise_drift(report)


def test_compare_caddyfile_uses_helper_on_permission(monkeypatch, tmp_path: Path):
    db_path = _reset_db(tmp_path)
    target = tmp_path / "Caddyfile"
    target.write_text("ignored")

    mirror = tmp_path / "mirror"
    mirror.write_text(render_caddyfile_text(db_path=db_path))

    original_read = Path.read_text

    def fake_read(self, *args, **kwargs):
        if self == target:
            raise PermissionError
        return original_read(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", fake_read)
    monkeypatch.setattr(
        "caddy_tui.drift.stage_caddyfile_copy",
        lambda path: (mirror, "sudo helper", None),
    )

    report = compare_caddyfile(target, db_path=db_path)
    assert report.in_sync is True
    assert report.error is None


def test_collect_app_status_includes_drift(tmp_path: Path, monkeypatch):
    db_path = _reset_db(tmp_path)
    target = tmp_path / "Caddyfile"
    target.write_text(render_caddyfile_text(db_path=db_path))
    with db.session_scope(db_path) as session:
        config = session.scalar(select(Config))
        assert config is not None
        config.caddyfile_path = str(target)
        config.last_imported_at = "2025-11-22T00:00:00Z"

    monkeypatch.setattr("caddy_tui.importer.adapt_caddyfile", lambda path: {})
    import_caddyfile(target, target_snapshot=SNAPSHOT_KIND_CADDY_TUI)
    info = status_mod.collect_app_status(db_path=db_path, refresh_sources=False)
    assert info.block_count == 1
    assert any(snapshot.block_count == 1 for snapshot in info.snapshots)
    assert any(
        comparison.status == "match"
        and {comparison.left_kind, comparison.right_kind} == {SNAPSHOT_KIND_CADDY_TUI, SNAPSHOT_KIND_CADDYFILE}
        for comparison in info.comparisons
    )
