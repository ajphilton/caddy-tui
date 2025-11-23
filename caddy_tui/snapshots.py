"""Helpers for working with configuration snapshots."""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable
import json
import tempfile
from hashlib import sha256

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import models
from .caddy_integration import CaddyError, adapt_caddyfile
from .json_normalizer import blocks_from_caddy_json
from .db import session_scope
from .importer import DEFAULT_CONFIG_NAME
from .live_renderer import render_live_block_like_caddyfile

SNAPSHOT_PAIRINGS: tuple[tuple[models.SnapshotKind, models.SnapshotKind], ...] = (
    (models.SNAPSHOT_KIND_CADDY_TUI, models.SNAPSHOT_KIND_CADDYFILE),
    (models.SNAPSHOT_KIND_CADDY_TUI, models.SNAPSHOT_KIND_CADDY_LIVE),
    (models.SNAPSHOT_KIND_CADDYFILE, models.SNAPSHOT_KIND_CADDY_LIVE),
)

SNAPSHOT_LABELS = {
    models.SNAPSHOT_KIND_CADDY_TUI: "caddy-tui",
    models.SNAPSHOT_KIND_CADDYFILE: "caddyfile",
    models.SNAPSHOT_KIND_CADDY_LIVE: "caddy live",
}
CADDYFILE_HIDE_SENTINEL = "__caddyfile__"


@dataclass(slots=True)
class SnapshotComparison:
    left_kind: models.SnapshotKind
    right_kind: models.SnapshotKind
    status: str
    mismatch_count: int | None
    left_hash: str | None
    right_hash: str | None


@dataclass(slots=True)
class SnapshotBlockText:
    block_index: int
    text: str
    key: tuple[str, ...]
    handles: tuple[str, ...]
    handlers: tuple[str, ...]
    hosts: tuple[str, ...]
    roots: tuple[str, ...]
    paths: tuple[str, ...]
    groups: tuple[str, ...]
    encodings: tuple[str, ...]
    locations: tuple[str, ...]
    dials: tuple[str, ...]
    status_codes: tuple[str, ...]
    route_payloads: tuple[str, ...]


def get_snapshot(session: Session, config_id: int, kind: models.SnapshotKind) -> models.ConfigSnapshot | None:
    return session.scalar(
        select(models.ConfigSnapshot)
        .where(
            models.ConfigSnapshot.config_id == config_id,
            models.ConfigSnapshot.source_kind == kind,
        )
        .limit(1)
    )


def structural_hash(snapshot: models.ConfigSnapshot) -> str:
    route_blobs = _snapshot_route_blobs(snapshot)
    if route_blobs is not None:
        blob = json.dumps(route_blobs, sort_keys=True, ensure_ascii=False)
        return sha256(blob.encode("utf-8")).hexdigest()
    payload = [_block_payload(block) for block in sorted(snapshot.server_blocks, key=lambda b: b.block_index)]
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return sha256(blob.encode("utf-8")).hexdigest()


def compare_snapshots(
    left: models.ConfigSnapshot | None,
    right: models.ConfigSnapshot | None,
    *,
    left_kind: models.SnapshotKind,
    right_kind: models.SnapshotKind,
) -> SnapshotComparison:
    if left is None or right is None:
        return SnapshotComparison(
            left_kind=left_kind,
            right_kind=right_kind,
            status="missing",
            mismatch_count=None,
            left_hash=structural_hash(left) if left else None,
            right_hash=structural_hash(right) if right else None,
        )

    left_blocks = _block_hashes(left)
    right_blocks = _block_hashes(right)
    mismatch = 0
    for block_index in sorted(set(left_blocks) | set(right_blocks)):
        if left_blocks.get(block_index) != right_blocks.get(block_index):
            mismatch += 1

    left_hash = structural_hash(left)
    right_hash = structural_hash(right)
    status = "match" if left_hash == right_hash else "different"
    return SnapshotComparison(
        left_kind=left_kind,
        right_kind=right_kind,
        status=status,
        mismatch_count=mismatch,
        left_hash=left_hash,
        right_hash=right_hash,
    )


def _block_hashes(snapshot: models.ConfigSnapshot) -> dict[int, str]:
    route_blobs = _snapshot_route_blobs(snapshot)
    if route_blobs is not None:
        return {
            idx: sha256(blob.encode("utf-8")).hexdigest()
            for idx, blob in enumerate(route_blobs)
        }
    return {
        block.block_index: sha256(
            json.dumps(_block_payload(block), sort_keys=True, ensure_ascii=False).encode("utf-8")
        ).hexdigest()
        for block in sorted(snapshot.server_blocks, key=lambda b: b.block_index)
    }


def _block_payload(block: models.ServerBlock) -> dict:
    return {
        "index": block.block_index,
        "is_global": block.is_global,
        "raw_prelude": block.raw_prelude,
        "raw_postlude": block.raw_postlude,
        "sites": [
            {
                "label": site.raw_label,
                "host": site.host,
                "port": site.port,
                "scheme": site.scheme,
                "is_ipv6": site.is_ipv6,
                "is_wildcard": site.is_wildcard,
                "order": site.label_index,
            }
            for site in sorted(block.sites, key=lambda site: site.label_index)
        ],
        "fragments": [
            {
                "kind": fragment.kind,
                "content": fragment.content,
                "index": fragment.fragment_index,
            }
            for fragment in sorted(block.fragments, key=lambda frag: frag.fragment_index)
        ],
        "directives": [
            {
                "name": directive.name,
                "matcher": directive.matcher,
                "line": directive.line_index,
                "raw_leading": directive.raw_leading,
                "raw_trailing": directive.raw_trailing,
                "has_block": directive.has_block,
                "raw_block_body": directive.raw_block_body,
                "args": [
                    {
                        "value": arg.value,
                        "index": arg.arg_index,
                    }
                    for arg in sorted(directive.args, key=lambda arg: arg.arg_index)
                ],
                "kv": [
                    {
                        "section": kv.section,
                        "key": kv.key,
                        "value": kv.value,
                        "index": kv.kv_index,
                    }
                    for kv in sorted(directive.kv_pairs, key=lambda kv: kv.kv_index)
                ],
            }
            for directive in sorted(block.directives, key=lambda d: d.line_index)
        ],
    }


def load_snapshot_block_texts(db_path: Path, kind: models.SnapshotKind) -> list[SnapshotBlockText]:
    with session_scope(db_path=db_path) as session:
        config = session.scalar(select(models.Config).where(models.Config.name == DEFAULT_CONFIG_NAME))
        if not config:
            return []
        snapshot = get_snapshot(session, config.id, kind)
        if snapshot is None:
            return []
        scrub_paths = _snapshot_scrub_paths(snapshot)
        route_lookup: dict[tuple[str, ...], deque[str]] = {}
        if kind in {models.SNAPSHOT_KIND_CADDY_TUI, models.SNAPSHOT_KIND_CADDYFILE}:
            route_lookup = _caddyfile_route_lookup(snapshot)
        block_texts: list[SnapshotBlockText] = []
        for block in sorted(snapshot.server_blocks, key=lambda b: b.block_index):
            metadata = _block_json_metadata(block)
            block_key = _canonical_block_key(block)
            route_payloads: tuple[str, ...] = ()
            if kind == models.SNAPSHOT_KIND_CADDY_LIVE:
                route_payloads = tuple(_block_route_fragments(block, scrub_paths=scrub_paths))
            else:
                queue = route_lookup.get(block_key)
                if queue:
                    payload = queue.popleft()
                    route_payloads = (payload,)
                else:
                    route_payloads = tuple(_block_route_fragments(block, scrub_paths=scrub_paths))
            block_texts.append(
                SnapshotBlockText(
                    block_index=block.block_index,
                    text=_render_block_text(block, source_kind=kind),
                    key=block_key,
                    handles=metadata.handles,
                    handlers=metadata.handlers,
                    hosts=metadata.hosts,
                    roots=metadata.roots,
                    paths=metadata.paths,
                    groups=metadata.groups,
                    encodings=metadata.encodings,
                    locations=metadata.locations,
                    dials=metadata.dials,
                    status_codes=metadata.status_codes,
                    route_payloads=route_payloads,
                )
            )
        return block_texts


def _render_block_text(block: models.ServerBlock, *, source_kind: models.SnapshotKind) -> str:
    if source_kind == models.SNAPSHOT_KIND_CADDY_LIVE:
        rendered = render_live_block_like_caddyfile(block)
        if rendered:
            trimmed = rendered.strip("\n")
            return trimmed if trimmed else rendered
    fragments = ''.join(fragment.content for fragment in sorted(block.fragments, key=lambda f: f.fragment_index))
    combined = f"{block.raw_prelude or ''}{fragments}{block.raw_postlude or ''}"
    stripped = combined.strip("\n")
    return stripped if stripped else combined


def _canonical_block_key(block: models.ServerBlock) -> tuple[str, ...]:
    labels = [site.raw_label.strip() for site in sorted(block.sites, key=lambda s: s.label_index) if site.raw_label]
    return _canonical_label_tuple(labels)


def _canonical_label_tuple(labels: Iterable[str], *, default: str = "(global)") -> tuple[str, ...]:
    cleaned = [label.strip() for label in labels if label and label.strip()]
    values = cleaned or [default]
    return tuple(sorted({value for value in values}))

def _snapshot_scrub_paths(snapshot: models.ConfigSnapshot) -> tuple[str, ...]:
    config = snapshot.config
    if config and getattr(config, "caddyfile_path", None):
        path = config.caddyfile_path.strip()
        return (path,) if path else ()
    return ()

def _snapshot_route_blobs(snapshot: models.ConfigSnapshot) -> list[str] | None:
    route_map = _snapshot_route_map(snapshot)
    if route_map is None:
        return None
    ordered: list[str] = []
    for key in sorted(route_map.keys(), key=_key_sort_value):
        ordered.extend(route_map[key])
    return ordered


def _routes_from_caddyfile_snapshot(snapshot: models.ConfigSnapshot) -> list[str]:
    return [payload for _key, payload in _caddyfile_route_entries(snapshot)]


def _routes_from_json_payload(payload: dict[str, Any], *, scrub_paths: tuple[str, ...]) -> list[str]:
    blobs: list[str] = []
    blocks = blocks_from_caddy_json(payload)
    for block in blocks:
        fragment = next((frag for frag in block.fragments if frag.kind == "json_route"), None)
        if fragment is None:
            continue
        normalised = _normalise_json_fragment(fragment.content, scrub_paths=scrub_paths)
        if normalised is None:
            continue
        blobs.append(normalised)
    return blobs


def render_snapshot_text(snapshot: models.ConfigSnapshot) -> str:
    chunks: list[str] = []
    for block in sorted(snapshot.server_blocks, key=lambda b: b.block_index):
        if block.raw_prelude:
            chunks.append(block.raw_prelude)
        fragments = sorted(block.fragments, key=lambda f: f.fragment_index)
        chunks.extend(fragment.content for fragment in fragments)
        if block.raw_postlude:
            chunks.append(block.raw_postlude)
    return "".join(chunks)


def _block_route_fragments(block: models.ServerBlock, *, scrub_paths: tuple[str, ...]) -> list[str]:
    payloads: list[str] = []
    for fragment in sorted(block.fragments, key=lambda f: f.fragment_index):
        if fragment.kind != "json_route":
            continue
        normalised = _normalise_json_fragment(fragment.content, scrub_paths=scrub_paths)
        if normalised:
            payloads.append(normalised)
    return payloads


def _snapshot_route_map(snapshot: models.ConfigSnapshot) -> dict[tuple[str, ...], list[str]] | None:
    kind = snapshot.source_kind
    if kind == models.SNAPSHOT_KIND_CADDY_LIVE:
        return _live_route_map(snapshot)
    if kind in {models.SNAPSHOT_KIND_CADDY_TUI, models.SNAPSHOT_KIND_CADDYFILE}:
        try:
            return _caddyfile_route_map(snapshot)
        except CaddyError:
            return None
    return None


def _live_route_map(snapshot: models.ConfigSnapshot) -> dict[tuple[str, ...], list[str]] | None:
    mapping: dict[tuple[str, ...], list[str]] = {}
    scrub_paths = _snapshot_scrub_paths(snapshot)
    for block in sorted(snapshot.server_blocks, key=lambda b: b.block_index):
        payloads = _block_route_fragments(block, scrub_paths=scrub_paths)
        if not payloads:
            return None
        key = _canonical_block_key(block)
        mapping.setdefault(key, []).extend(payloads)
    return mapping


def _caddyfile_route_map(snapshot: models.ConfigSnapshot) -> dict[tuple[str, ...], list[str]]:
    mapping: dict[tuple[str, ...], list[str]] = {}
    for key, payload in _caddyfile_route_entries(snapshot):
        mapping.setdefault(key, []).append(payload)
    return mapping


def _key_sort_value(key: tuple[str, ...]) -> tuple[int, str]:
    label = ", ".join(key)
    return (len(key), label.lower())


def _caddyfile_route_lookup(snapshot: models.ConfigSnapshot) -> dict[tuple[str, ...], deque[str]]:
    lookup: dict[tuple[str, ...], deque[str]] = {}
    try:
        entries = _caddyfile_route_entries(snapshot)
    except CaddyError:
        return lookup
    for key, payload in entries:
        lookup.setdefault(key, deque()).append(payload)
    return lookup


def _caddyfile_route_entries(snapshot: models.ConfigSnapshot) -> list[tuple[tuple[str, ...], str]]:
    text = render_snapshot_text(snapshot)
    if not text.strip():
        return []
    with tempfile.NamedTemporaryFile("w", delete=False) as temp:
        temp.write(text)
        temp_path = Path(temp.name)
    try:
        adapted = adapt_caddyfile(temp_path)
    finally:
        temp_path.unlink(missing_ok=True)
    entries: list[tuple[tuple[str, ...], str]] = []
    base_paths = _snapshot_scrub_paths(snapshot)
    scrub_paths = (*base_paths, str(temp_path))
    blocks = blocks_from_caddy_json(adapted)
    for block in blocks:
        key = _canonical_label_tuple(block.labels)
        fragment = next((frag for frag in block.fragments if frag.kind == "json_route"), None)
        if fragment is None:
            continue
        normalised = _normalise_json_fragment(fragment.content, scrub_paths=scrub_paths)
        if normalised is None:
            continue
        entries.append((key, normalised))
    return entries


def _normalise_json_fragment(content: str, *, scrub_paths: tuple[str, ...]) -> str | None:
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        return None
    if scrub_paths:
        _scrub_file_server_hide(data, scrub_paths)
    return json.dumps(data, sort_keys=True, separators=(",", ":"))


def _scrub_file_server_hide(node: Any, scrub_paths: tuple[str, ...]) -> None:
    if not scrub_paths:
        return
    if isinstance(node, dict):
        handler = node.get("handler")
        if handler == "file_server":
            hide_value = node.get("hide")
            hide_entries = _normalize_string_values(hide_value)
            if hide_entries:
                replaced = [CADDYFILE_HIDE_SENTINEL if entry in scrub_paths else entry for entry in hide_entries]
                node["hide"] = replaced
        for value in node.values():
            _scrub_file_server_hide(value, scrub_paths)
    elif isinstance(node, list):
        for item in node:
            _scrub_file_server_hide(item, scrub_paths)


@dataclass(slots=True)
class _BlockJsonMetadata:
    handles: tuple[str, ...]
    handlers: tuple[str, ...]
    hosts: tuple[str, ...]
    roots: tuple[str, ...]
    paths: tuple[str, ...]
    groups: tuple[str, ...]
    encodings: tuple[str, ...]
    locations: tuple[str, ...]
    dials: tuple[str, ...]
    status_codes: tuple[str, ...]


def _block_json_metadata(block: models.ServerBlock) -> _BlockJsonMetadata:
    handles: list[str] = []
    handlers: list[str] = []
    hosts: list[str] = []
    roots: list[str] = []
    paths: list[str] = []
    groups: list[str] = []
    encodings: list[str] = []
    locations: list[str] = []
    dials: list[str] = []
    status_codes: list[str] = []
    fragments = sorted(block.fragments, key=lambda f: f.fragment_index)
    for fragment in fragments:
        if fragment.kind != "json_route":
            continue
        try:
            route = json.loads(fragment.content)
        except json.JSONDecodeError:
            continue
        if not isinstance(route, dict):
            continue
        _collect_route_metadata(
            route,
            handles,
            handlers,
            hosts,
            roots,
            paths,
            groups,
            encodings,
            locations,
            dials,
            status_codes,
            prefix=(),
        )
    return _BlockJsonMetadata(
        handles=tuple(handles),
        handlers=tuple(handlers),
        hosts=tuple(_dedupe_preserve_order(hosts)),
        roots=tuple(_dedupe_preserve_order(roots)),
        paths=tuple(_dedupe_preserve_order(paths)),
        groups=tuple(_dedupe_preserve_order(groups)),
        encodings=tuple(_dedupe_preserve_order(encodings)),
        locations=tuple(_dedupe_preserve_order(locations)),
        dials=tuple(_dedupe_preserve_order(dials)),
        status_codes=tuple(_dedupe_preserve_order(status_codes)),
    )


def _collect_route_metadata(
    node: dict[str, Any] | None,
    handles: list[str],
    handlers: list[str],
    hosts: list[str],
    roots: list[str],
    paths: list[str],
    groups: list[str],
    encodings: list[str],
    locations: list[str],
    dials: list[str],
    status_codes: list[str],
    *,
    prefix: tuple[str, ...],
) -> None:
    if not isinstance(node, dict):
        return
    _extend_unique(hosts, _hosts_from_matchers(node))
    _extend_unique(paths, _paths_from_matchers(node))
    _extend_unique(groups, _groups_from_matchers(node))
    entries = _normalise_handle_entries(node)
    for idx, entry in enumerate(entries):
        path_parts = (*prefix, f"handle[{idx}]")
        raw_handler = entry.get("handler") if isinstance(entry, dict) else None
        handler_name = _normalize_handler_name(raw_handler)
        label = ".".join(path_parts)
        if handler_name:
            _extend_unique(roots, _root_values(entry))
            _extend_unique(encodings, _encoding_values(entry))
            _extend_unique(locations, _location_values(entry))
            _extend_unique(dials, _dial_values(entry))
            _extend_unique(paths, _handler_path_values(entry))
            _extend_unique(status_codes, _status_code_values(entry))
            if handler_name != "subroute":
                handles.append(f"{label}: {handler_name}")
                if handler_name not in handlers:
                    handlers.append(handler_name)
        _recurse_nested_routes(
            entry,
            handles,
            handlers,
            hosts,
            roots,
            paths,
            groups,
            encodings,
            locations,
            dials,
            status_codes,
            parent_path=path_parts,
        )

    nested_routes = node.get("routes") if isinstance(node, dict) else None
    if isinstance(nested_routes, list):
        for idx, route in enumerate(nested_routes):
            if isinstance(route, dict):
                _collect_route_metadata(
                    route,
                    handles,
                    handlers,
                    hosts,
                    roots,
                    paths,
                    groups,
                    encodings,
                    locations,
                    dials,
                    status_codes,
                    prefix=(*prefix, f"routes[{idx}]"),
                )


def _normalise_handle_entries(node: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(node, dict):
        return []
    handle_value = node.get("handle")
    handlers_value = node.get("handlers")
    entries = handle_value or handlers_value
    if entries is None:
        return []
    if isinstance(entries, dict):
        return [entries]
    if isinstance(entries, list):
        return [entry for entry in entries if isinstance(entry, dict)]
    return []


def _recurse_nested_routes(
    entry: dict[str, Any] | None,
    handles: list[str],
    handlers: list[str],
    hosts: list[str],
    roots: list[str],
    paths: list[str],
    groups: list[str],
    encodings: list[str],
    locations: list[str],
    dials: list[str],
    status_codes: list[str],
    *,
    parent_path: tuple[str, ...],
) -> None:
    if not isinstance(entry, dict):
        return
    nested_routes = entry.get("routes")
    if not isinstance(nested_routes, list):
        return
    for idx, route in enumerate(nested_routes):
        if isinstance(route, dict):
            _collect_route_metadata(
                route,
                handles,
                handlers,
                hosts,
                roots,
                paths,
                groups,
                encodings,
                locations,
                dials,
                status_codes,
                prefix=(*parent_path, f"routes[{idx}]"),
            )


def _root_values(entry: dict[str, Any]) -> list[str]:
    value = entry.get("root")
    if isinstance(value, str):
        return [value] if value else []
    if isinstance(value, Iterable):
        roots: list[str] = []
        for item in value:
            if isinstance(item, str) and item:
                roots.append(item)
        return roots
    return []


def _hosts_from_matchers(node: dict[str, Any]) -> list[str]:
    matchers = node.get("match")
    if not isinstance(matchers, list):
        return []
    hosts: list[str] = []
    for matcher in matchers:
        if not isinstance(matcher, dict):
            continue
        hosts.extend(_normalize_host_values(matcher.get("host")))
        hosts.extend(_normalize_host_values(matcher.get("hosts")))
    return hosts


def _normalize_host_values(value: Any) -> list[str]:
    return _normalize_string_values(value)


def _paths_from_matchers(node: dict[str, Any]) -> list[str]:
    matchers = node.get("match")
    if not isinstance(matchers, list):
        return []
    paths: list[str] = []
    for matcher in matchers:
        if not isinstance(matcher, dict):
            continue
        paths.extend(_normalize_string_values(matcher.get("paths")))
        paths.extend(_normalize_string_values(matcher.get("path")))
    return paths


def _groups_from_matchers(node: dict[str, Any]) -> list[str]:
    matchers = node.get("match")
    if not isinstance(matchers, list):
        return []
    groups: list[str] = []
    for matcher in matchers:
        if not isinstance(matcher, dict):
            continue
        group = matcher.get("group")
        if isinstance(group, str) and group:
            groups.append(group)
    return groups


def _extend_unique(target: list[str], items: Iterable[str]) -> None:
    for item in items:
        if item and item not in target:
            target.append(item)


def _dedupe_preserve_order(items: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        ordered.append(item)
    return ordered


def _normalize_string_values(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value else []
    if isinstance(value, Iterable):
        results: list[str] = []
        for entry in value:
            if isinstance(entry, str) and entry:
                results.append(entry)
        return results
    return []


def _encoding_values(entry: dict[str, Any]) -> list[str]:
    value = entry.get("encodings")
    if isinstance(value, dict):
        return [str(key) for key in value.keys() if key]
    return _normalize_string_values(value)


def _location_values(entry: dict[str, Any]) -> list[str]:
    return _normalize_string_values(entry.get("location"))


def _dial_values(entry: dict[str, Any]) -> list[str]:
    values: list[str] = []
    values.extend(_normalize_string_values(entry.get("dial")))
    upstreams = entry.get("upstreams")
    if isinstance(upstreams, list):
        for upstream in upstreams:
            if isinstance(upstream, dict):
                dial = upstream.get("dial")
                if isinstance(dial, str) and dial:
                    values.append(dial)
    return values


def _handler_path_values(entry: dict[str, Any]) -> list[str]:
    values = _normalize_string_values(entry.get("path"))
    values.extend(_normalize_string_values(entry.get("paths")))
    return values


def _status_code_values(entry: dict[str, Any]) -> list[str]:
    value = entry.get("status_code")
    if value is None:
        return []
    if isinstance(value, int):
        return [str(value)]
    if isinstance(value, str):
        trimmed = value.strip()
        return [trimmed] if trimmed else []
    if isinstance(value, Iterable):
        codes: list[str] = []
        for item in value:
            if isinstance(item, int):
                codes.append(str(item))
            elif isinstance(item, str):
                trimmed = item.strip()
                if trimmed:
                    codes.append(trimmed)
        return codes
    return []


def _normalize_handler_name(name: str | None) -> str | None:
    if not name:
        return None
    lowered = name.strip().lower()
    if not lowered:
        return None
    if lowered in {"subroute", "log", "rewrite"}:
        return None
    if lowered == "headers":
        return "header"
    return lowered
