"""Render live snapshot JSON routes into Caddyfile-style text."""
from __future__ import annotations

from typing import Any, Iterable
import json

from . import models


INDENT_SPACES = 4
REDIRECT_CODES = {301, 302, 303, 307, 308}


def render_live_block_like_caddyfile(block: models.ServerBlock) -> str | None:
    """Best-effort conversion of a live JSON route block to Caddyfile text."""
    fragment = _first_json_route_fragment(block)
    if fragment is None:
        return None
    try:
        route = json.loads(fragment.content)
    except json.JSONDecodeError:
        return None
    if not isinstance(route, dict):
        return None

    header = _block_header_line(block)
    body_lines = _render_route_body(route, indent=INDENT_SPACES)
    if not body_lines:
        body_lines = [_indent(INDENT_SPACES) + "# no handlers"]

    lines = [header, *body_lines, "}"]
    body_text = "\n".join(lines) + "\n"
    return f"{block.raw_prelude or ''}{body_text}{block.raw_postlude or ''}"


def _first_json_route_fragment(block: models.ServerBlock) -> models.RawFragment | None:
    for fragment in sorted(block.fragments, key=lambda f: f.fragment_index):
        if fragment.kind == "json_route":
            return fragment
    return None


def _block_header_line(block: models.ServerBlock) -> str:
    labels = [
        site.raw_label.strip()
        for site in sorted(block.sites, key=lambda s: s.label_index)
        if site.raw_label and site.raw_label.strip()
    ]
    if labels:
        return f"{', '.join(labels)} {{"
    return "{"


def _render_route_body(route: dict[str, Any], *, indent: int) -> list[str]:
    lines: list[str] = []
    lines.extend(_match_comment_lines(route, indent))
    if route.get("terminal"):
        lines.append(_indent(indent) + "# terminal")

    handles = _handle_entries(route)
    if not handles:
        nested_routes = route.get("routes")
        if isinstance(nested_routes, list):
            for nested in nested_routes:
                lines.append(_indent(indent) + "handle {")
                lines.extend(_render_route_body(nested, indent=indent + INDENT_SPACES))
                lines.append(_indent(indent) + "}")
        return lines

    for entry in handles:
        lines.extend(_render_handle_entry(entry, indent))
    return lines


def _match_comment_lines(route: dict[str, Any], indent: int) -> list[str]:
    matchers = route.get("match")
    if not isinstance(matchers, list):
        return []
    lines: list[str] = []
    for matcher in matchers:
        if not isinstance(matcher, dict):
            continue
        desc = _describe_matcher(matcher)
        if desc:
            lines.append(_indent(indent) + f"# match {desc}")
    return lines


def _describe_matcher(matcher: dict[str, Any]) -> str:
    parts: list[str] = []
    hosts = _string_list(matcher.get("host")) or _string_list(matcher.get("hosts"))
    if hosts:
        parts.append("host " + ", ".join(hosts))
    paths = _string_list(matcher.get("path")) + _string_list(matcher.get("paths"))
    if paths:
        parts.append("path " + ", ".join(paths))
    methods = _string_list(matcher.get("method")) + _string_list(matcher.get("methods"))
    if methods:
        parts.append("method " + ", ".join(methods))
    if matcher.get("expression"):
        parts.append(f"expr {matcher['expression']}")
    return "; ".join(parts)


def _handle_entries(node: dict[str, Any]) -> list[dict[str, Any]]:
    handle_value = node.get("handle")
    handlers_value = node.get("handlers")
    entries = handle_value if handle_value is not None else handlers_value
    if isinstance(entries, dict):
        return [entries]
    if isinstance(entries, list):
        return [entry for entry in entries if isinstance(entry, dict)]
    return []


def _render_handle_entry(entry: dict[str, Any], indent: int) -> list[str]:
    handler = (entry.get("handler") or "").lower()
    if handler == "subroute":
        return _render_subroute(entry, indent)
    if handler == "reverse_proxy":
        return [_indent(indent) + _render_reverse_proxy(entry)]
    if handler == "static_response":
        return _render_static_response(entry, indent)
    if handler == "encode":
        return [_indent(indent) + _render_encode(entry)]
    if handler == "file_server":
        return _render_file_server(entry, indent)
    if handler in {"headers", "header"}:
        return _render_header(entry, indent)
    if handler == "php_fastcgi":
        return _render_php_fastcgi(entry, indent)
    if handler == "handle_response":
        return _render_handle_response(entry, indent)
    if handler == "rewrite":
        return _render_rewrite(entry, indent)
    if handler == "copy_response_headers":
        return _render_copy_response_headers(entry, indent)
    if handler == "request_body":
        return _render_request_body(entry, indent)
    return [_indent(indent) + f"# handler {handler or 'unknown'}"]


def _render_subroute(entry: dict[str, Any], indent: int) -> list[str]:
    routes = entry.get("routes")
    if not isinstance(routes, list):
        return [_indent(indent) + "handle {}"]
    lines: list[str] = []
    for route in routes:
        if not isinstance(route, dict):
            continue
        lines.append(_indent(indent) + "handle {")
        nested_lines = _render_route_body(route, indent=indent + INDENT_SPACES)
        if not nested_lines:
            nested_lines = [_indent(indent + INDENT_SPACES) + "# no handlers"]
        lines.extend(nested_lines)
        lines.append(_indent(indent) + "}")
    if not lines:
        lines.append(_indent(indent) + "handle {}")
    return lines


def _render_reverse_proxy(entry: dict[str, Any]) -> str:
    targets = _reverse_proxy_targets(entry)
    line = "reverse_proxy"
    if targets:
        line += " " + " ".join(targets)
    return line


def _reverse_proxy_targets(entry: dict[str, Any]) -> list[str]:
    targets: list[str] = []
    upstreams = entry.get("upstreams")
    if isinstance(upstreams, list):
        for upstream in upstreams:
            if isinstance(upstream, dict):
                dial = upstream.get("dial")
                if isinstance(dial, str) and dial:
                    targets.append(dial)
    targets.extend(_string_list(entry.get("to")))
    return targets


def _render_static_response(entry: dict[str, Any], indent: int) -> list[str]:
    headers = entry.get("headers") or {}
    location = _first_header_value(headers, "Location")
    status_code = entry.get("status_code")
    body = entry.get("body") or entry.get("content")
    if location and isinstance(status_code, int) and status_code in REDIRECT_CODES and not body:
        return [_indent(indent) + f"redir {location} {status_code}"]
    pieces: list[str] = ["respond"]
    if body:
        pieces.append(_quote(str(body)))
    if status_code:
        pieces.append(str(status_code))
    return [_indent(indent) + " ".join(pieces)]


def _render_encode(entry: dict[str, Any]) -> str:
    value = entry.get("encodings") or entry.get("formats")
    names: list[str] = []
    if isinstance(value, dict):
        names.extend(str(key) for key in value.keys())
    else:
        names.extend(_string_list(value))
    line = "encode"
    if names:
        line += " " + " ".join(names)
    return line


def _render_file_server(entry: dict[str, Any], indent: int) -> list[str]:
    line = "file_server"
    if entry.get("browse"):
        line += " browse"
    lines = [_indent(indent) + line]
    root = entry.get("root")
    if isinstance(root, str) and root:
        lines.append(_indent(indent + INDENT_SPACES) + f"root {root}")
    index = entry.get("index")
    if isinstance(index, list):
        for value in index:
            lines.append(_indent(indent + INDENT_SPACES) + f"index {value}")
    elif isinstance(index, str) and index:
        lines.append(_indent(indent + INDENT_SPACES) + f"index {index}")
    return lines


def _render_header(entry: dict[str, Any], indent: int) -> list[str]:
    response = entry.get("response") or {}
    set_headers = response.get("set") or entry.get("set")
    lines: list[str] = []
    if isinstance(set_headers, dict):
        for key, values in set_headers.items():
            for value in _string_list(values):
                lines.append(_indent(indent) + f"header {key} {_quote(value)}")
    if not lines:
        lines.append(_indent(indent) + "header /* configure headers */")
    return lines


def _render_php_fastcgi(entry: dict[str, Any], indent: int) -> list[str]:
    upstream = entry.get("upstream") or entry.get("address")
    line = "php_fastcgi"
    if isinstance(upstream, str) and upstream:
        line += f" {upstream}"
    lines = [_indent(indent) + line]
    root = entry.get("root")
    if isinstance(root, str) and root:
        lines.append(_indent(indent + INDENT_SPACES) + f"root {root}")
    return lines


def _render_handle_response(entry: dict[str, Any], indent: int) -> list[str]:
    routes = entry.get("routes")
    if not isinstance(routes, list):
        return [_indent(indent) + "handle_response {}"]
    lines: list[str] = []
    for route in routes:
        if not isinstance(route, dict):
            continue
        lines.append(_indent(indent) + "handle_response {")
        lines.extend(_render_route_body(route, indent=indent + INDENT_SPACES))
        lines.append(_indent(indent) + "}")
    if not lines:
        lines.append(_indent(indent) + "handle_response {}")
    return lines


def _render_rewrite(entry: dict[str, Any], indent: int) -> list[str]:
    destination = entry.get("to") or entry.get("uri")
    if isinstance(destination, str) and destination:
        return [_indent(indent) + f"rewrite {destination}"]
    return [_indent(indent) + "rewrite"]


def _render_copy_response_headers(entry: dict[str, Any], indent: int) -> list[str]:
    headers = entry.get("headers")
    if isinstance(headers, list):
        joined = " ".join(headers)
        return [_indent(indent) + f"copy_response_headers {joined}"]
    return [_indent(indent) + "copy_response_headers"]


def _render_request_body(entry: dict[str, Any], indent: int) -> list[str]:
    if entry.get("action") == "replace" and isinstance(entry.get("value"), str):
        return [_indent(indent) + f"request_body replace {_quote(entry['value'])}"]
    return [_indent(indent) + "request_body"]


def _first_header_value(headers: dict[str, Any], key: str) -> str | None:
    candidates = headers.get(key) or headers.get(key.lower())
    if isinstance(candidates, str) and candidates:
        return candidates
    if isinstance(candidates, Iterable):
        for candidate in candidates:
            if isinstance(candidate, str) and candidate:
                return candidate
    return None


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, Iterable):
        results: list[str] = []
        for entry in value:
            if isinstance(entry, str) and entry:
                results.append(entry)
        return results
    return []


def _quote(value: str) -> str:
    escaped = value.replace('"', '\\"')
    return f'"{escaped}"'


def _indent(width: int) -> str:
    return " " * width
