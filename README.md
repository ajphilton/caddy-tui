# caddy-tui

A terminal UI + CLI helper that keeps Caddy configuration in SQLite, lets you edit via a Textual interface, and safely regenerates validated configs before reloading Caddy.

## Quick start

```bash
pip install -e .
caddy-tui init
caddy-tui import --caddyfile /etc/caddy/Caddyfile
caddy-tui apply
caddy-tui tui
```

The CLI subcommands are designed for both humans and automation (including Copilot agents) so every workflow is scriptable.
