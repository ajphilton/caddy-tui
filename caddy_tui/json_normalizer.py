"""Convert Caddy admin JSON into ParsedBlock structures used for snapshots."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable
import json

from .caddyfile_parser import ParsedBlock, ParsedFragment


@dataclass(slots=True)
class NormalisedRoute:
    server_name: str
    route_index: int
    labels: list[str]
    payload: dict[str, Any]


def blocks_from_caddy_json(payload: str | dict[str, Any]) -> list[ParsedBlock]:
    data = json.loads(payload) if isinstance(payload, str) else payload
    http_app = (_get_dict(data, "apps") or {}).get("http", {})
    servers = _get_dict(http_app, "servers") or {}

    blocks: list[ParsedBlock] = []
    for server_name in sorted(servers.keys()):
        server = servers.get(server_name) or {}
        routes = server.get("routes") or []
        for index, route in enumerate(routes):
            labels = _labels_for_route(server_name, server, route, index)
            blocks.append(
                ParsedBlock(
                    labels=labels,
                    is_global=len(labels) == 0,
                    raw_prelude=f"# server: {server_name} route: {index}\n",
                    raw_postlude="",
                    fragments=[
                        ParsedFragment(
                            kind="json_route",
                            content=json.dumps(route, sort_keys=True, indent=2),
                        )
                    ],
                )
            )

    if not blocks:
        blocks.append(
            ParsedBlock(
                labels=[],
                is_global=True,
                raw_prelude="",
                raw_postlude="",
                fragments=[
                    ParsedFragment(
                        kind="json_config",
                        content=json.dumps(data, sort_keys=True, indent=2),
                    )
                ],
            )
        )

    return blocks


def _labels_for_route(server_name: str, server: dict[str, Any], route: dict[str, Any], index: int) -> list[str]:
    labels: list[str] = []
    matchers = route.get("match") or []
    for matcher in matchers:
        labels.extend(_extract_hosts(matcher))
        labels.extend(_prefix_list(matcher.get("path"), "path"))
        labels.extend(_prefix_list(matcher.get("paths"), "path"))
        labels.extend(_prefix_list(matcher.get("method"), "method"))
        labels.extend(_prefix_list(matcher.get("methods"), "method"))

    if not labels:
        listeners = server.get("listen") or []
        labels.extend(str(listener) for listener in listeners if listener)

    if not labels:
        labels.append(f"{server_name}::route{index}")

    return _dedupe_preserve_order(labels)


def _extract_hosts(matcher: dict[str, Any]) -> list[str]:
    hosts = matcher.get("host") or matcher.get("hosts") or []
    return [str(host) for host in hosts if host]


def _prefix_list(values: Any, prefix: str) -> list[str]:
    if values is None:
        return []
    if isinstance(values, str):
        values = [values]
    if not isinstance(values, Iterable):  # pragma: no cover - guard rail
        return []
    return [f"{prefix}:{value}" for value in values if value]


def _get_dict(data: dict[str, Any], key: str) -> dict[str, Any] | None:
    node = data.get(key)
    return node if isinstance(node, dict) else None


def _dedupe_preserve_order(items: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        ordered.append(item)
    return ordered
