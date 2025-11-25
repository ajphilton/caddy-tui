# Changelog

All notable changes to this project will be documented in this file.

The format roughly follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.1] - 2025-11-25
- Added a live Caddyfile viewer (`p` in the TUI) that refreshes the helper snapshot, renders every block, and falls back to the configured `CADDY_TUI_LIVE_CADDYFILE` path when the admin API is unavailable.
- Added a CLI reference table (`h` in the TUI) that introspects the Click group to list every command with its usage string and description.
- Documented the new shortcuts in the README and bumped version metadata for the PyPI release.

## [0.2.0] - 2025-11-23
- Added TUI block CRUD actions (add/edit/delete) powered by the new `block_editor` helpers so changes stay in the caddy-tui snapshot until exported.
- Documented project structure, CLI skeleton, database schema builder, and import/export hooks in README.
- Added MIT license, MANIFEST.in, and expanded `pyproject.toml` metadata (classifiers, URLs, keywords) to prep the project for PyPI publishing.
- Included publishing instructions and changelog update guidance to keep releases consistent.

## [0.1.0] - 2025-11-22
- Initial scaffold with CLI, TUI placeholder, importer/exporter skeleton, and automated tests.
