# Logical Caddyfile Storage Plan

Date: 2025-11-22
Author: GitHub Copilot (GPT-5.1-Codex)

## Objectives
- Ingest a Caddyfile **without** converting to JSON while preserving block order, labels, matchers, directives, comments, and unknown syntax.
- Store enough structured data that we can deterministically regenerate ("overwrite") the source Caddyfile from SQLite at any time.
- Keep future-proof escape hatches for syntax we do not parse yet so that no user edits are lost.
- Allow multiple configs (e.g. `/etc/caddy/Caddyfile`, staging copies) to coexist later even if we start with a single default.

## High-level ingestion/export flow
1. **Ingest**
   - Resolve/read the target Caddyfile (same helper copy mechanism).
   - Run `caddy adapt --adapter caddyfile --pretty` to validate syntax only. Separately, run `caddy fmt --disable-comments` (or a custom tokenizer) to produce tokens; we will implement our own light parser that mirrors `caddyconfig/caddyfile` tests for ordering/import resolution.
   - Within one transaction per config:
     1. Upsert the row in `configs` (set `last_imported_at`, `caddyfile_path`, `last_caddyfile_hash`).
     2. Delete existing child rows for that config (cascades will fan out).
     3. Insert `server_blocks` in file order, followed by `server_block_sites`, directives, args, kv, and raw fragments.
   - Persist any file-level fragments (header comments, trailing whitespace) as `raw_fragments` with `block_id` pointing to a sentinel "file" block (`is_global=1` + no labels) so export always replays them.

2. **Export**
   - Fetch the config and all related rows ordered by `block_index`/`line_index`/`arg_index`.
   - Reconstruct: prelude → `server_block_sites` header lines (comma-separated as originally recorded) → directives (with matchers, inline args, block body) → raw fragments.
   - Write to `caddyfile_path` (use helper for privileged installs), update `last_exported_at` and `last_caddyfile_hash` with the new bytes, then run `caddy validate` (adapter caddyfile) before reloading.

## Proposed schema
Below is the SQL we will mirror with SQLAlchemy models. The structure follows the user proposal with minor naming tweaks for internal consistency.

```sql
PRAGMA foreign_keys = ON;

CREATE TABLE meta (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL
);
INSERT INTO meta (key, value) VALUES ("schema_version", "2");

CREATE TABLE configs (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    name                TEXT NOT NULL UNIQUE,
    caddyfile_path      TEXT NOT NULL,
    last_imported_at    TEXT,
    last_exported_at    TEXT,
    last_caddyfile_hash TEXT
);

CREATE TABLE server_blocks (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    config_id     INTEGER NOT NULL REFERENCES configs(id) ON DELETE CASCADE,
    block_index   INTEGER NOT NULL,
    is_global     INTEGER NOT NULL DEFAULT 0,
    raw_prelude   TEXT,
    raw_postlude  TEXT,
    created_at    TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at    TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(config_id, block_index)
);

CREATE TABLE server_block_sites (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    block_id     INTEGER NOT NULL REFERENCES server_blocks(id) ON DELETE CASCADE,
    raw_label    TEXT NOT NULL,
    host         TEXT,
    port         INTEGER,
    scheme       TEXT,
    is_ipv6      INTEGER NOT NULL DEFAULT 0,
    is_wildcard  INTEGER NOT NULL DEFAULT 0,
    label_index  INTEGER NOT NULL,
    UNIQUE(block_id, label_index)
);

CREATE TABLE directives (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    block_id       INTEGER NOT NULL REFERENCES server_blocks(id) ON DELETE CASCADE,
    name           TEXT NOT NULL,
    matcher        TEXT,
    line_index     INTEGER NOT NULL,
    raw_leading    TEXT,
    raw_trailing   TEXT,
    has_block      INTEGER NOT NULL DEFAULT 0,
    raw_block_body TEXT,
    created_at     TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at     TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(block_id, line_index)
);

CREATE TABLE directive_args (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    directive_id  INTEGER NOT NULL REFERENCES directives(id) ON DELETE CASCADE,
    arg_index     INTEGER NOT NULL,
    value         TEXT NOT NULL,
    UNIQUE(directive_id, arg_index)
);

CREATE TABLE directive_kv (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    directive_id  INTEGER NOT NULL REFERENCES directives(id) ON DELETE CASCADE,
    section       TEXT,
    key           TEXT NOT NULL,
    value         TEXT NOT NULL,
    kv_index      INTEGER NOT NULL,
    UNIQUE(directive_id, section, kv_index)
);

CREATE TABLE raw_fragments (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    block_id       INTEGER NOT NULL REFERENCES server_blocks(id) ON DELETE CASCADE,
    fragment_index INTEGER NOT NULL,
    kind           TEXT NOT NULL,
    content        TEXT NOT NULL,
    UNIQUE(block_id, fragment_index)
);
```

### Notes & decisions
- `configs.name` will default to "default" so the CLI behaves like today. Future multi-config support just means exposing a selector.
- `raw_prelude`/`raw_postlude` store contiguous whitespace/comments that sit outside directives. When exporting we print them verbatim, so even `# TODO` comments survive.
- `raw_fragments.kind` allows us to stash anything we cannot yet model (e.g., experimental syntax) and replay verbatim. We can also record file-level BOMs or final newline state here.
- `directive_kv` is deliberately optional. If we cannot sensibly break a nested block into key/value pairs yet, we keep the full text in `raw_block_body` and leave this table empty.
- All timestamps are stored as ISO8601 strings (SQLite TEXT). We will normalize to UTC when writing.
- We will enforce `PRAGMA foreign_keys = ON` immediately after opening the engine; this lets cascades clean up automatically when a config or block is deleted.

## ORM mapping outline
| Table | SQLAlchemy class | Key relationships |
| --- | --- | --- |
| meta | `Meta` | unchanged (adds schema_version row).
| configs | `Config` | `Config.server_blocks = relationship("ServerBlock", cascade="all, delete-orphan")`.
| server_blocks | `ServerBlock` | belongs to Config, has `sites`, `directives`, `fragments`.
| server_block_sites | `ServerBlockSite` | belongs to block.
| directives | `Directive` | belongs to block, has `args`, `kv_pairs`.
| directive_args | `DirectiveArg` | belongs to directive.
| directive_kv | `DirectiveKeyValue` | belongs to directive.
| raw_fragments | `RawFragment` | belongs to block.

We will drop the interim `HttpServer`, `SiteRoute`, etc. models once migrations move historical data into the new layout.

## Parser strategy
1. **Lexer**: implement a lightweight tokenizer that mirrors the Go `Dispenser`. We only need tokens + positional info to rebuild spacing. Comments and whitespace become tokens too so we can decide whether they belong to a directive (`raw_leading`/`raw_trailing`) or a block-level fragment.
2. **Block builder**: iterate tokens to build server blocks and directives:
   - Keep track of the current matcher (`@name`), directive name, argument list, and whether a `{` block follows. Nested braces become `raw_block_body` text for now (we preserve indentation as-is).
   - Snippets `(name)` and `import` statements can be represented as directives with special `name` values. Recursive/glob imports should be resolved before storing; we can reuse Caddy’s own parser by invoking `caddy fmt --input` to preprocess includes, or we can run `caddy list-modules`? For MVP we will call `caddy adapt --config <file> --adapter caddyfile` to ensure the file is valid, then parse the same source ourselves without following imports (imports will be expanded by the parser since our tokenizer sees the final file; we can optionally run `caddy fmt --adapter caddyfile --stdin` to get the fully-resolved text).

## Exporter reconstruction rules
- Print file header fragments ordered by `fragment_index` where `block_id` references the synthetic "file" block (block_index = -1). 
- For each `server_block` ordered by `block_index`:
  1. Print `raw_prelude`.
  2. Compose the label line: join `server_block_sites.raw_label` by commas or leave blank for global block. Append ` {` on same line, newline, print directive bodies, then closing `}` plus `raw_postlude`.
  3. Emit directives ordered by `line_index`. Each directive prints:
     - `raw_leading`
     - optional matcher token, directive name, args, newline or inline trailing comment (`raw_trailing`).
     - if `has_block`, emit `{
` + `raw_block_body` + `}
` (subject to stored indentation).
  4. Emit `raw_fragments` for the block.

Because we never reformat tokens, rewriting a file immediately after importing is idempotent.

## Migration plan
1. Introduce `schema_version` meta entry and new tables alongside existing ones. Keep both code paths until migrations succeed.
2. Build a one-off migration script that:
   - Reads every `Site`/`Route`/`RouteHandler` from the current schema.
   - Creates a single config ("default") and a placeholder block for each site label, mapping the stored JSON into a `raw_block_body` block comment so no data is lost.
   - Marks `schema_version=2` after successful migration.
3. Remove the old tables/models after the importer/exporter switch to the logical schema ships.

## Open questions / next steps
1. **Tokenizer implementation**: Evaluate whether we can vendor Caddy’s Go parser via `xcaddy build` and call it via CLI to output JSON describing blocks. If not, we implement a Python parser guided by `parse_test.go`.
2. **Directive block structure**: decide when to split nested directives into rows vs. leaving them inside `raw_block_body`. (Probably start with raw text, add structure table later.)
3. **Import expansion**: Determine whether we store `import` statements verbatim or resolve them into inline directives. For lossless round-trip we likely keep the literal `import ...` directive and record its args so exporter prints the same line.
4. **CLI/TUI updates**: Update commands (`list-sites`, future editors) to operate on `configs`/`server_blocks` rather than the old site table.
5. **Tests**: Mirror key cases from `parse_test.go` to ensure we ingest/export correctly (variadic args, matchers, nested blocks, snippet imports, etc.).

Once we sign off on this plan, implementation work will focus on the parser, ORM models, importer/exporter rewrites, and migration tooling.
