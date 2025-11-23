"""Lightweight parser that segments a Caddyfile into server blocks."""
from __future__ import annotations

from dataclasses import dataclass
from typing import List


@dataclass(slots=True)
class ParsedFragment:
    kind: str
    content: str


@dataclass(slots=True)
class ParsedBlock:
    labels: list[str]
    is_global: bool
    raw_prelude: str
    raw_postlude: str
    fragments: list[ParsedFragment]


@dataclass(slots=True)
class ParsedConfig:
    blocks: list[ParsedBlock]


class CaddyfileParseError(RuntimeError):
    pass


def parse_caddyfile_text(text: str) -> ParsedConfig:
    """Parse the Caddyfile text into ordered blocks.

    This parser is intentionally conservative: it only cares about brace
    balancing so it can faithfully capture the text for each server block while
    also recording the host labels that appear in the header. Comments and
    whitespace between blocks are preserved via the per-block ``raw_prelude``
    and ``raw_postlude`` fields.
    """

    length = len(text)
    pos = 0
    blocks: list[ParsedBlock] = []
    pending_ws = ""

    pos, leading = _consume_ws_and_comments(text, pos)
    pending_ws += leading

    while pos < length:
        brace_index = _find_next_char(text, "{", pos)
        if brace_index == -1:
            # Remainder is trailing whitespace/comments.
            pending_ws += text[pos:]
            break
        header_text = text[pos:brace_index]
        labels = _split_labels(header_text)
        is_global = len(labels) == 0

        block_start = pos
        pos = brace_index + 1
        body_start = pos

        closing_index = _find_matching_brace(text, brace_index)
        if closing_index is None:
            raise CaddyfileParseError("Unbalanced braces in Caddyfile")
        block_end = closing_index + 1

        body_text = text[body_start:closing_index]
        header_segment = text[block_start:brace_index + 1]
        footer_segment = text[closing_index:block_end]

        block = ParsedBlock(
            labels=labels,
            is_global=is_global,
            raw_prelude=pending_ws,
            raw_postlude="",
            fragments=[
                ParsedFragment(kind="header", content=header_segment),
                ParsedFragment(kind="body", content=body_text),
                ParsedFragment(kind="footer", content=footer_segment),
            ],
        )
        blocks.append(block)
        pending_ws = ""
        pos = block_end
        pos, consumed = _consume_ws_and_comments(text, pos)
        pending_ws += consumed

    if blocks:
        blocks[-1].raw_postlude = pending_ws
    elif pending_ws:
        # Entire file is whitespace/comments; synthesise a block so we can
        # retain the content.
        blocks.append(
            ParsedBlock(
                labels=[],
                is_global=True,
                raw_prelude="",
                raw_postlude=pending_ws,
                fragments=[],
            )
        )

    return ParsedConfig(blocks=blocks)


def _consume_ws_and_comments(text: str, pos: int) -> tuple[int, str]:
    length = len(text)
    fragments: List[str] = []
    while pos < length:
        ch = text[pos]
        if ch in " \t\r\n":
            start = pos
            while pos < length and text[pos] in " \t\r\n":
                pos += 1
            fragments.append(text[start:pos])
        elif ch == "#":
            start = pos
            newline = text.find("\n", pos)
            if newline == -1:
                fragments.append(text[start:])
                pos = length
            else:
                fragments.append(text[start : newline + 1])
                pos = newline + 1
        else:
            break
    return pos, "".join(fragments)


def _find_next_char(text: str, target: str, start: int) -> int:
    idx = text.find(target, start)
    return idx if idx != -1 else -1


def _find_matching_brace(text: str, open_index: int) -> int | None:
    depth = 0
    idx = open_index
    length = len(text)
    while idx < length:
        ch = text[idx]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return idx
        idx += 1
    return None


def _split_labels(header_text: str) -> list[str]:
    cleaned = header_text.replace("\n", " ")
    tokens: list[str] = []
    for part in cleaned.split():
        for chunk in part.split(","):
            value = chunk.strip()
            if value:
                tokens.append(value)
    return tokens