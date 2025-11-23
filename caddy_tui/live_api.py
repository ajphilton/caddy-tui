"""Helpers for querying the Caddy admin API."""
from __future__ import annotations

from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
import json

from .caddyfile_parser import parse_caddyfile_text


@dataclass(slots=True)
class LiveApiStatus:
    state: str
    block_count: int | None
    caddyfile_text: str | None
    format: str
    json_payload: str | None = None
    error: str | None = None


def fetch_live_status(endpoint: str | None, *, timeout: float = 2.5) -> LiveApiStatus | None:
    if not endpoint:
        return None
    request = Request(endpoint)
    request.add_header("Accept", "text/caddyfile, text/plain, application/json")
    try:
        with urlopen(request, timeout=timeout) as response:
            content_type = response.headers.get("Content-Type", "")
            raw = response.read().decode("utf-8", errors="replace")
    except HTTPError as exc:  # pragma: no cover - depends on live service
        detail = exc.read().decode("utf-8", errors="replace")
        return LiveApiStatus(state="down", block_count=None, caddyfile_text=None, format="http", json_payload=None, error=detail or str(exc))
    except URLError as exc:  # pragma: no cover - depends on live service
        return LiveApiStatus(state="down", block_count=None, caddyfile_text=None, format="network", json_payload=None, error=str(exc))

    lowered = content_type.lower()
    if "caddyfile" in lowered or "text/plain" in lowered:
        return _from_caddyfile(raw)
    if "json" in lowered:
        return _from_json(raw)
    # Best effort: try to guess based on payload
    stripped = raw.lstrip()
    if stripped.startswith("{"):
        return _from_json(raw)
    return _from_caddyfile(raw)


def _from_caddyfile(text: str) -> LiveApiStatus:
    block_count: int | None
    try:
        parsed = parse_caddyfile_text(text)
        block_count = len(parsed.blocks)
    except Exception as exc:  # pragma: no cover - parser already tested elsewhere
        block_count = None
        text = text or ""
        return LiveApiStatus(state="live", block_count=block_count, caddyfile_text=text, format="caddyfile", json_payload=None, error=str(exc))
    return LiveApiStatus(state="live", block_count=block_count, caddyfile_text=text, format="caddyfile", json_payload=None)


def _from_json(payload: str) -> LiveApiStatus:
    try:
        data = json.loads(payload)
    except json.JSONDecodeError as exc:  # pragma: no cover - JSON parsing validated elsewhere
        return LiveApiStatus(state="live", block_count=None, caddyfile_text=None, format="json", json_payload=payload, error=str(exc))
    block_count = _count_http_routes(data)
    return LiveApiStatus(state="live", block_count=block_count, caddyfile_text=None, format="json", json_payload=payload)


def _count_http_routes(data: dict) -> int | None:
    apps = data.get("apps")
    if not isinstance(apps, dict):
        return None
    http_app = apps.get("http")
    if not isinstance(http_app, dict):
        return None
    servers = http_app.get("servers")
    if not isinstance(servers, dict):
        return None
    total = 0
    for server in servers.values():
        routes = server.get("routes") if isinstance(server, dict) else None
        if isinstance(routes, list):
            total += len(routes)
    return total
