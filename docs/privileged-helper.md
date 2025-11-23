# Privileged helper architecture

The interactive menu and `caddy-tui` CLI intentionally run as an unprivileged user. Certain
operations still need elevated access:

- Reading `/etc/caddy/Caddyfile` (or other root-owned config files).
- Writing regenerated configs back into `/etc/caddy/*`.
- Reloading the systemd-managed Caddy service.

Rather than running the entire application as root, we provide a small
`supported` helper binary (`caddy-tui-helper`) with a narrowly scoped command
surface. Operators can grant password-less sudo access to **only** this helper,
keeping the rest of the tool unprivileged.

## Components

| Component | Responsibility |
| --- | --- |
| `caddy_tui.helper_runner` | Client-side utilities used by the CLI/TUI to invoke helper commands and report errors. It stages temporary files in `~/.caddy-tui/cache`. |
| `caddy_tui.privileged_helper` | The helper entry point installed as `caddy-tui-helper`. Implemented with Click, it exposes `mirror`, `install`, `reload`, `restart`, and `status` subcommands. |
| `docs/privileged-helper.md` | This document. |

## Flows

### Import (mirror) flow

1. User triggers `caddy-tui import --caddyfile /etc/caddy/Caddyfile` (directly or via the TUI).
2. If the path is readable, the importer proceeds normally.
3. If permission is denied, `helper_runner.stage_caddyfile_copy` invokes the helper:

   ```text
   sudo caddy-tui-helper mirror --source /etc/caddy/Caddyfile --dest ~/.caddy-tui/cache/mirrors/Caddyfile.<ts> --owner $UID --group $GID
   ```

4. The helper copies the file and adjusts ownership so the unprivileged process can read it. The importer then continues using the staged path.

### Apply flow

1. `caddy-tui apply` (or future TUI actions) call `generate_caddyfile()`.
2. If writing directly to `/etc/caddy/Caddyfile.generated` fails due to permissions, the exporter writes to `~/.caddy-tui/cache/generated/` and asks the helper to install it:

   ```text
   sudo caddy-tui-helper install --source ~/.caddy-tui/cache/generated/Caddyfile.generated --dest /etc/caddy/Caddyfile.generated --mode 0o644
   ```

3. After the file is installed, the CLI can still run validation/reload. When full reload privileges are required, `helper_runner.reload_caddy_service()` calls:

   ```text
   sudo caddy-tui-helper reload --command "systemctl reload caddy"
   ```

4. If the helper detects that Caddy is down, the TUI can escalate to a restart via:

   ```text
   sudo caddy-tui-helper restart --command "systemctl restart caddy"
   ```

5. Health checks in the TUI/CLI probe the helper’s `status` subcommand so we can highlight whether Caddy is live or down:

   ```text
   sudo caddy-tui-helper status --command "systemctl is-active caddy"
   ```

### Status/TUI messaging

Whenever the helper is needed, the user sees the fully expanded command in the terminal output, making it easy to copy/paste or configure in `/etc/sudoers.d/caddy-tui`.

## Deployment

1. Install the package (`pip install caddy-tui`). `pyproject.toml` registers the helper under `[project.scripts]`.
2. Add a sudoers entry if desired:

   ```text
   sudo visudo -f /etc/sudoers.d/caddy-tui
   alexander  ALL=(ALL) NOPASSWD: /usr/local/bin/caddy-tui-helper
   ```

3. Ensure `~/.caddy-tui/cache` exists (the app creates it on-demand).

## Security notes

- The helper has no ability to run arbitrary commands; each subcommand validates its arguments and limits operations to copy/reload actions.
- All staging paths include the calling user’s UID/GID to prevent privilege escalation between users on multi-user systems.
- Future enhancements can move the helper behind a Unix socket for request/response flows, but the current design keeps things simple and auditable through sudo logs.
