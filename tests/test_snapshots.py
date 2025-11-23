import json
from pathlib import Path

from sqlalchemy import select

from caddy_tui import db, models
from caddy_tui.importer import DEFAULT_CONFIG_NAME, import_caddy_json_payload, import_caddyfile_text
from caddy_tui.snapshots import compare_snapshots, load_snapshot_block_texts


def _reset_db(tmp_path: Path) -> Path:
    db._engine = None  # type: ignore[attr-defined]
    db._SessionLocal = None  # type: ignore[attr-defined]
    db_path = tmp_path / "config.db"
    db.init_db(db_path)
    return db_path


def _seed_snapshot(db_path: Path, kind: str) -> None:
    with db.session_scope(db_path=db_path) as session:
        config = models.Config(name=DEFAULT_CONFIG_NAME, caddyfile_path="/etc/caddy/Caddyfile")
        session.add(config)
        session.flush()

        snapshot = models.ConfigSnapshot(config=config, source_kind=kind)
        session.add(snapshot)
        session.flush()

        block = models.ServerBlock(
            snapshot=snapshot,
            block_index=0,
            raw_prelude="",
            raw_postlude="\n",
        )
        session.add(block)
        session.flush()

        block.fragments.append(
            models.RawFragment(
                block=block,
                fragment_index=0,
                kind="header",
                content="example.com {\n",
            )
        )
        block.fragments.append(
            models.RawFragment(
                block=block,
                fragment_index=1,
                kind="body",
                content="    respond \"ok\"\n",
            )
        )
        block.fragments.append(
            models.RawFragment(
                block=block,
                fragment_index=2,
                kind="footer",
                content="}\n",
            )
        )
        block.sites.append(
            models.ServerBlockSite(
                block=block,
                raw_label="example.com",
                host="example.com",
                port=None,
                scheme=None,
                is_ipv6=False,
                is_wildcard=False,
                label_index=0,
            )
        )


def test_load_snapshot_block_texts_returns_content(tmp_path: Path):
    db_path = _reset_db(tmp_path)
    _seed_snapshot(db_path, models.SNAPSHOT_KIND_CADDY_TUI)
    blocks = load_snapshot_block_texts(db_path, models.SNAPSHOT_KIND_CADDY_TUI)
    assert len(blocks) == 1
    assert "example.com" in blocks[0].text
    assert "respond" in blocks[0].text
    assert blocks[0].key == ("example.com",)
    assert blocks[0].handles == ()
    assert blocks[0].handlers == ()
    assert blocks[0].hosts == ()
    assert blocks[0].roots == ()
    assert blocks[0].paths == ()
    assert blocks[0].groups == ()
    assert blocks[0].encodings == ()
    assert blocks[0].locations == ()
    assert blocks[0].dials == ()
    assert blocks[0].status_codes == ()
    assert len(blocks[0].route_payloads) == 1


def test_load_snapshot_block_texts_handles_missing_snapshot(tmp_path: Path):
    db_path = _reset_db(tmp_path)
    blocks = load_snapshot_block_texts(db_path, models.SNAPSHOT_KIND_CADDY_LIVE)
    assert blocks == []


def test_load_snapshot_block_texts_global_block(tmp_path: Path):
    db_path = _reset_db(tmp_path)
    with db.session_scope(db_path=db_path) as session:
        config = models.Config(name=DEFAULT_CONFIG_NAME, caddyfile_path="/etc/caddy/Caddyfile")
        session.add(config)
        session.flush()
        snapshot = models.ConfigSnapshot(config=config, source_kind=models.SNAPSHOT_KIND_CADDY_TUI)
        session.add(snapshot)
        session.flush()
        block = models.ServerBlock(snapshot=snapshot, block_index=0, raw_prelude="", raw_postlude="")
        session.add(block)
        session.flush()
        block.fragments.append(
            models.RawFragment(
                block=block,
                fragment_index=0,
                kind="body",
                content="{\n    respond \"ok\"\n}\n",
            )
        )

    blocks = load_snapshot_block_texts(db_path, models.SNAPSHOT_KIND_CADDY_TUI)
    assert blocks[0].key == ("(global)",)


def test_load_snapshot_block_texts_extracts_handle_metadata(tmp_path: Path):
    db_path = _reset_db(tmp_path)
    with db.session_scope(db_path=db_path) as session:
        config = models.Config(name=DEFAULT_CONFIG_NAME, caddyfile_path="/etc/caddy/Caddyfile")
        session.add(config)
        session.flush()

        snapshot = models.ConfigSnapshot(config=config, source_kind=models.SNAPSHOT_KIND_CADDY_TUI)
        session.add(snapshot)
        session.flush()

        block = models.ServerBlock(snapshot=snapshot, block_index=0, raw_prelude="", raw_postlude="")
        session.add(block)
        session.flush()

        route_payload = {
            "match": [
                {
                    "hosts": ["alpha.test", "beta.test"],
                    "paths": ["/api/*"],
                    "path": "/console",
                    "group": "medusa",
                }
            ],
            "handle": [
                {
                    "handler": "reverse_proxy",
                    "upstreams": [{"dial": "medusa:9000"}],
                },
                {
                    "handler": "encode",
                    "encodings": {"zstd": {}, "gzip": {}},
                },
                {
                    "handler": "subroute",
                    "routes": [
                        {
                            "handle": [
                                {"handler": "static_response", "status_code": 201},
                                {"handler": "rewrite", "location": "/tmp"},
                                    {"handler": "headers", "response": {"set": {"X-Test": ["1"]}}},
                                {},
                            ]
                        }
                    ],
                },
                {
                    "handler": "file_server",
                    "root": "/srv/site",
                    "location": "/shared/static",
                    "path": "/assets/*",
                },
            ],
        }

        block.fragments.append(
            models.RawFragment(
                block=block,
                fragment_index=0,
                kind="json_route",
                content=json.dumps(route_payload),
            )
        )

    blocks = load_snapshot_block_texts(db_path, models.SNAPSHOT_KIND_CADDY_TUI)
    assert blocks[0].handles == (
        "handle[0]: reverse_proxy",
        "handle[1]: encode",
        "handle[2].routes[0].handle[0]: static_response",
        "handle[2].routes[0].handle[2]: header",
        "handle[3]: file_server",
    )
    assert blocks[0].handlers == (
        "reverse_proxy",
        "encode",
        "static_response",
        "header",
        "file_server",
    )
    assert blocks[0].hosts == ("alpha.test", "beta.test")
    assert blocks[0].roots == ("/srv/site",)
    assert blocks[0].paths == ("/api/*", "/console", "/assets/*")
    assert blocks[0].groups == ("medusa",)
    assert blocks[0].encodings == ("zstd", "gzip")
    assert blocks[0].locations == ("/shared/static",)
    assert blocks[0].dials == ("medusa:9000",)
    assert blocks[0].status_codes == ("201",)
    assert len(blocks[0].route_payloads) == 1


def test_live_snapshot_blocks_render_json_like_caddyfile(tmp_path: Path):
    db_path = _reset_db(tmp_path)
    with db.session_scope(db_path=db_path) as session:
        config = models.Config(name=DEFAULT_CONFIG_NAME, caddyfile_path="/etc/caddy/Caddyfile")
        session.add(config)
        session.flush()

        snapshot = models.ConfigSnapshot(config=config, source_kind=models.SNAPSHOT_KIND_CADDY_LIVE)
        session.add(snapshot)
        session.flush()

        block = models.ServerBlock(
            snapshot=snapshot,
            block_index=0,
            raw_prelude="# server: srv0 route: 0\n",
            raw_postlude="",
        )
        session.add(block)
        session.flush()

        block.sites.append(
            models.ServerBlockSite(
                block=block,
                raw_label="www.redirect.test",
                host="www.redirect.test",
                port=None,
                scheme=None,
                is_ipv6=False,
                is_wildcard=False,
                label_index=0,
            )
        )

        route_payload = {
            "match": [{"host": ["www.redirect.test"]}],
            "handle": [
                {
                    "handler": "subroute",
                    "routes": [
                        {
                            "handle": [
                                {
                                    "handler": "static_response",
                                    "headers": {"Location": ["https://redirect.test{http.request.uri}"]},
                                    "status_code": 308,
                                }
                            ]
                        }
                    ],
                }
            ],
            "terminal": True,
        }

        block.fragments.append(
            models.RawFragment(
                block=block,
                fragment_index=0,
                kind="json_route",
                content=json.dumps(route_payload, sort_keys=True),
            )
        )

    blocks = load_snapshot_block_texts(db_path, models.SNAPSHOT_KIND_CADDY_LIVE)
    assert len(blocks) == 1
    text = blocks[0].text
    assert "www.redirect.test {" in text
    assert "redir https://redirect.test{http.request.uri} 308" in text
    assert text.strip().endswith("}")
    assert len(blocks[0].route_payloads) == 1


def test_compare_snapshots_uses_adapted_json(monkeypatch, tmp_path: Path):
    db_path = _reset_db(tmp_path)

    route_payload = {
        "match": [{"host": ["example.test"]}],
        "handle": [
            {
                "handler": "static_response",
                "body": "ok",
            }
        ],
        "terminal": True,
    }
    json_payload = {
        "apps": {
            "http": {
                "servers": {
                    "srv0": {
                        "routes": [route_payload],
                    }
                }
            }
        }
    }

    def fake_adapt(path: Path):  # type: ignore[unused-argument]
        return json_payload

    monkeypatch.setattr("caddy_tui.importer.adapt_caddyfile", fake_adapt)
    monkeypatch.setattr("caddy_tui.snapshots.adapt_caddyfile", fake_adapt)

    import_caddyfile_text(
        "example.test {\n    respond \"ok\"\n}\n",
        source_label="fs",
        target_snapshot=models.SNAPSHOT_KIND_CADDYFILE,
        db_path=db_path,
    )
    import_caddy_json_payload(
        json_payload,
        source_label="live",
        target_snapshot=models.SNAPSHOT_KIND_CADDY_LIVE,
        db_path=db_path,
    )

    with db.session_scope(db_path=db_path) as session:
        config = session.scalar(select(models.Config))
        assert config is not None
        file_snapshot = session.scalar(
            select(models.ConfigSnapshot)
            .where(
                models.ConfigSnapshot.config_id == config.id,
                models.ConfigSnapshot.source_kind == models.SNAPSHOT_KIND_CADDYFILE,
            )
            .limit(1)
        )
        live_snapshot = session.scalar(
            select(models.ConfigSnapshot)
            .where(
                models.ConfigSnapshot.config_id == config.id,
                models.ConfigSnapshot.source_kind == models.SNAPSHOT_KIND_CADDY_LIVE,
            )
            .limit(1)
        )
        assert file_snapshot is not None
        assert live_snapshot is not None
        comparison = compare_snapshots(
            file_snapshot,
            live_snapshot,
            left_kind=models.SNAPSHOT_KIND_CADDYFILE,
            right_kind=models.SNAPSHOT_KIND_CADDY_LIVE,
        )
        assert comparison.status == "match"
        assert comparison.mismatch_count == 0


def test_block_texts_include_route_payloads(monkeypatch, tmp_path: Path):
    db_path = _reset_db(tmp_path)

    route_payload = {
        "match": [{"host": ["payload.test"]}],
        "handle": [
            {
                "handler": "static_response",
                "body": "ok",
            }
        ],
        "terminal": True,
    }
    json_payload = {
        "apps": {
            "http": {
                "servers": {
                    "srv0": {
                        "routes": [route_payload],
                    }
                }
            }
        }
    }

    def fake_adapt(path: Path):  # type: ignore[unused-argument]
        return json_payload

    monkeypatch.setattr("caddy_tui.importer.adapt_caddyfile", fake_adapt)
    monkeypatch.setattr("caddy_tui.snapshots.adapt_caddyfile", fake_adapt)

    import_caddyfile_text(
        "payload.test {\n    respond \"ok\"\n}\n",
        source_label="fs",
        target_snapshot=models.SNAPSHOT_KIND_CADDYFILE,
        db_path=db_path,
    )
    import_caddy_json_payload(
        json_payload,
        source_label="live",
        target_snapshot=models.SNAPSHOT_KIND_CADDY_LIVE,
        db_path=db_path,
    )

    caddyfile_blocks = load_snapshot_block_texts(db_path, models.SNAPSHOT_KIND_CADDYFILE)
    live_blocks = load_snapshot_block_texts(db_path, models.SNAPSHOT_KIND_CADDY_LIVE)

    assert len(caddyfile_blocks) == 1
    assert len(live_blocks) == 1
    assert caddyfile_blocks[0].route_payloads
    assert live_blocks[0].route_payloads
    assert caddyfile_blocks[0].route_payloads == live_blocks[0].route_payloads
