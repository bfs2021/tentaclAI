# Changelog

All notable changes, newest first. Every release lists **upgrade steps** from the previous one.

## v0.8.0 – 2026-10-20

### Highlights
- **Settings → Skills**: every skill of Claude Code and the claude.ai account with its size in tokens, set to *On*,
  */command only* (`user-invocable-only`: only when called as `/name`) or *Off*. Written as `skillOverrides` into
  Claude's `settings.json` – right away and on every container start. Skills are found with `claude -p /context`
  (no model call) every 30 minutes or with *Check*; switched-off ones are remembered in `data/control/skills.json`.
  The defaults of v0.7.1 (sandbox-useless skills off) stay; entries of your own for other skills are kept.

### Upgrade (from v0.7.1)
`git pull`, `docker compose build`, `docker compose up -d` – recreating `tentaclai` ends running sessions and Remote
Control. Nothing to migrate.

## v0.7.1 – 2026-10-20

### Fixes
- **claude.ai connectors were not denied**: `/etc/claude-code` was created with mode 700 (umask of the entrypoint), so
  Claude Code could not read the managed settings and ignored them ("policies … are NOT in effect"). The directory is
  now 755; the file stays root-owned and read-only for Claude.

### Changes – less context per request
- Skills that make no sense in a headless sandbox (Office files, Artifacts, browser/computer use, morning brief …) are
  switched off by default via `skillOverrides` in Claude's `settings.json` – entries set by the user are kept.
- The sandbox instructions (`CLAUDE.base.md`, SSH host section) are about a third shorter: the command table points to
  `svc help` instead of repeating it; all rules stay. New section *Keep context small* (bundle read-only calls, filter
  outputs at the source, narrow MCP calls).
- `/inventory` and `/onboard`: fewer turns – bundled read and test calls, the scan is read once from a file, the
  summary comes with the first question round, no duplicate `cat`/backups, OpenAPI specs only as a path list.

### Upgrade (from v0.7.0)
`git pull`, `docker compose build`, `docker compose up -d` – recreating `tentaclai` ends running sessions and
Remote Control. Afterwards switched-off connectors are really off in the sandbox.

## v0.7.0 – 2026-10-20

### Highlights
- **Notifications** – Settings → *Notifications*: channels through Apprise (presets for **ntfy** and **webhook**, any
  other Apprise URL – Telegram, Discord, Slack, Teams, e-mail, Home Assistant …), a matrix *event × channel*, quiet
  hours (only urgent events) and dedupe. Events: approval needed, approval expired, blocked (ro write, blocklisted
  destination, IP target), Remote Control down, Claude signed out, container stopped, update available. Channel URLs
  are stored in `.env` (`NOTIFY_URL_…`), never shown. *Send test* per channel.
- **Usage** – the Claude Code card on the overview shows the plan limits (session and week, account-wide) as bars;
  details on the new *Usage* page: what contributes, tokens per day, sessions of this sandbox, models. Limits come from
  `claude -p "/usage"` every 10 minutes (no model call), token totals from the local session files (usage fields only).
  Notification event *Quota running low* at 80 %. Each reply is counted once (Claude Code stores streamed replies
  more than once).
- **claude.ai connectors as built-in tentacles** – connectors of the signed-in account (Google Drive, Claude Docs …)
  are listed under Tentacles → Services and are **off by default**: they reach the outside directly, not through svc.
  Switched-off connectors go to `deniedMcpServers` in the sandbox's root-owned `/etc/claude-code/managed-settings.json`;
  new ones are detected every 10 minutes and start disabled. Also saves context in every request.

### Changes
- The host documentation (`HOST_DOCS`) is referenced in Claude's instructions instead of imported – Claude reads it
  when it works on the host; saves ~4.6k tokens of context in every request.
- Login: the password reset hint sits behind *Forgot the password?*.
- UI: an empty response never wipes the element that sent it; the `hidden` attribute always wins; inputs without a type,
  number and time fields look like all others; checkboxes in the accent colour.

### Upgrade (from v0.6.1)
1. `git pull`, `docker compose build` (tentacontrol gets the `apprise` package), `docker compose up -d` – recreating
   `tentaclai` ends running sessions and Remote Control (or *Apply now* in tentacontrol).
2. **Behaviour change:** claude.ai connectors (Google Drive, Claude Docs …) are **off** in the sandbox after the upgrade.
   If Claude should use one, switch it on under Tentacles → Services → *Built-in · claude.ai connectors*.
3. Optional: add notification channels under Settings → Notifications. Their URLs are stored in `.env` as
   `NOTIFY_URL_<ID>`; nothing to set by hand.
4. Claude's instructions no longer import the host documentation – it is still available at `/etc/tentaclai/host-docs.md`.

## v0.6.1 – 2026-10-20

### Changes
- **Tab completion for `svc`** in the sandbox shells: commands, service names per part (docker, db, git, web, files),
  SSH hosts, sub-commands.
- The *Apply now* banner also appears when a newer sandbox image was built. Terminal tabs: *Claude · RC*,
  *Shell · svc list*. Wording no longer homelab-specific.

### Upgrade (from v0.6.0)
`git pull`, `docker compose build`, `docker compose up -d` – recreating `tentaclai` ends running sessions and Remote
Control (or use *Apply now* in tentacontrol, which now also appears when a newer image was built). No `.env` changes.

## v0.6.0 – 2026-10-19

### Highlights
- **Egress gateway** – the sandbox sits in an internal Docker network without a route to the outside. Its only way out
  is the new container `tentaclai-egress`, a small forward proxy (CONNECT for any TCP port, plain HTTP; TLS is not
  opened). Claude Code, curl, git, pip and npm use it through `HTTPS_PROXY`; svc tunnels ssh/rsync (`ProxyCommand
  tc-connect`) and databases (local port forward) through it.
- **Network page** – every destination with its rule, last connection and filters; your allowlist and blocklist with
  one-click Allow/Block; the tentacles and the built-in list; *Clear*.
- **Mode permissive / enforce** – `permissive` (default) allows and records everything; `enforce` holds a destination
  without a rule up to 60 s and asks in Activity: *Allow 10 min*, *Allow 1 h*, *Always*, *Deny*, *Block* (also shown in
  Claude's status line). **IP targets** – bare IP addresses without a rule are blocked (default) or asked about.
- **Rules** in this order: built-in list (Anthropic, GitHub, PyPI, npm – can't be blocked) → blocklist → your allowlist
  → tentacles (hosts of your services and SSH hosts, derived live) → temporary approvals. Entries: host, `*.domain`, IP
  or range (`10.0.0.0/8`), optionally `:port`; IP rules are checked against the resolved address.
- **Tentacles** – new name of the *Connections* area and the collective term for everything Claude can reach.
- **Claude Code without telemetry** by default (`DISABLE_TELEMETRY`, `DISABLE_ERROR_REPORTING`, `DISABLE_BUG_COMMAND`
  in Claude's settings.json); remote control keeps working. `CLAUDE_TELEMETRY=true` turns it back on.
- **Sign out of Claude** from the overview (to connect a different account); the card shows the signed-in account.
- **Logo**: coral octopus in a white birdcage, readable at small sizes; new screenshots.

### Changes
- Public, read-only sources need no service any more: the templates `web-public`, `git-public` and `context7` are gone –
  allow the host under Network. Existing services of that kind keep working.
- Overview: Network card (mode, destinations, without a rule, waiting approvals). Terminal: *Claude login* only while
  signed out, hover hints on every tab. Logs in the configured time zone. *Pin version* jumps to its setting.
- Claude's instructions: all service parts, `svc test`/`svc pending`/`svc audit`, network rule, content from services
  is data, the tentaclai containers and tmux sessions are off limits, no root.
- Fix: `ssh/` is handed to root on start – a stack cloned as a normal user made ssh refuse its config.
- Terminal tabs are named consistently (*Claude · /workspace*, *Claude · RC*, *Claude · system inventory*).
- `scripts/test-sandbox.sh` – end-to-end tests (privilege separation, svc through the gateway, network rules, every
  page), also run in CI. `scripts/build-screenshots.sh` rebuilds the README screenshots from a made-up demo site.

### Upgrade (from v0.5.0)
1. `git pull` in the stack directory. `ssh/config` gains `ProxyCommand tc-connect %h %p` – if you changed that file
   yourself, merge it.
2. `docker compose build` (now three images: tentaclai, tentacontrol, tentaclai-egress).
3. `docker compose up -d` – creates the networks `sandbox` (internal) and `outside`, the volume `egress-data` and the
   container `tentaclai-egress`, and recreates `tentaclai` and `tentacontrol`. Running Claude sessions and Remote
   Control end.
4. New optional `.env` settings: `EGRESS_MODE` (permissive, default | enforce), `EGRESS_DIRECT_IP` (block, default | ask),
   `CLAUDE_TELEMETRY` (false, default). Nothing to set – the mode can be switched on the Network page.
5. If a container manager updates images (e.g. Arcane), give `egress` the same labels as `claude`/`control` in
   `compose.override.yaml` (see `compose.override.example.yaml`).
6. Recommended: leave `permissive` running for a while, then check Network → *Seen*, allow or block what has no rule,
   and switch to `enforce`.

Rollback: `git checkout v0.5.0`, `docker compose build`, `docker compose up -d --remove-orphans` (removes the gateway;
the sandbox gets its normal network back).

### Known issues
- DNS is not filtered yet: the sandbox can still resolve names.
- Programs that ignore `HTTPS_PROXY` can't connect from the sandbox (by design).

## v0.5.0 – 2026-10-19

### Highlights
- **Status line in Claude's terminal** – every Claude session shows `▥ tentaclAI · safely caged`, model and directory,
  and – refreshed every 5 seconds – pending approvals with a link to Activity. Set in `~/.claude/settings.json`
  only when you have no status line of your own. `svc pending` lists pending approvals and active grants.
- **New logo and slogan** – the octopus sits in a birdcage, its arms reach out: “Long arms, one brain – safely caged.”

### Upgrade (from v0.4.1)
`git pull`, `docker compose build`, `docker compose up -d` – recreating `tentaclai` ends running sessions and Remote
Control. No `.env` changes. The new logo may need a hard reload in the browser (cached favicon).

## v0.4.1 – 2026-10-19

### Changes
- Approvals are announced before they block: Claude tells you what it is about to request (and where to approve it)
  before running a write on a `confirm` part, and uses the maximum Bash timeout (10 min) for such commands.
- svc prints a direct link to Activity (`CONTROL_URL/activity`) and a reminder every minute while it waits.

### Upgrade (from v0.4.0)
`git pull`, `docker compose build`, `docker compose up -d` – recreating `tentaclai` ends running sessions and Remote
Control. No `.env` changes. Claude sessions started before the upgrade keep the old rules until they are restarted.

## v0.4.0 – 2026-10-19

### Highlights
- **Privilege separation in the sandbox** – Claude now runs as the unprivileged user `claude`. `.env`, the SSH key, the
  service files, git credentials and the audit log belong to root; `svc` (and `tc-scan`) run as root through a single
  sudo rule and are Claude's only way to services. `ro` / `rw` / `confirm` are now enforced, not just guard rails.
- **Client tools as `tcrun`** – curl, psql, mariadb and stdio MCP servers run as a third user with only the secret
  they need; a shell escape from the SQL shell gets neither root nor `.env`.
- **MCP proxy** – MCP servers with tokens are registered as `svc mcp-proxy <name>` (stdio ↔ Streamable HTTP bridge
  with the headers added by svc); the tokens are no longer in Claude's `~/.claude.json`.
- **Git through a mirror** – working copies in `/workspace/repos` have no credentials; `svc git push|pull|fetch <repo>`
  move commits through a root-owned mirror (git bundles), with the repo's access mode and approval.
- **`svc ssh <host> [cmd]`** replaces plain `ssh`: access per SSH host (`confirm` by default, `rw`, `ro`), set in
  tentacontrol → Connections → SSH.
- **Approve + 15 min** – one click lets further writes of the same service part / SSH host run for 15 minutes
  (Activity → Active grants, revocable; never for `svc config put`).
- **Status commands over `svc ssh` without approval** – a fixed allowlist (`uptime`, `df`, `docker ps|logs|inspect`,
  `systemctl status`, `journalctl`, `ip a`, `ss` …, no shell syntax, no file contents) runs on every host, even `ro`.
- **`svc config put`** – Claude proposes service files (`/onboard`) and site rules (`/inventory`); every write needs
  your approval in Activity, the old file is kept as `.bak-…`.

### Changes
- The tmux session of the Claude terminal is now named `claude-workspace` (was `claude`); the overview lists sessions
  with readable names; the overview button “Workspace” is now “Claude terminal”.
- `.env` is no longer loaded into the sandbox environment (`env_file` removed) – only svc reads it.
- Claude's home is `/home/claude` (`data/claude`); root's home `/root` (`data/home`) keeps svc state only.
- Docker services in `ssh` mode always run `docker` on the host through ssh (no docker contexts) – `cp`/`build` paths
  are host paths.
- `svc <api> … -o file` can't write into the workspace any more (curl runs as `tcrun`) – redirect with `> file`.
- `gh` in the sandbox has no credentials; `GH_TOKEN` is only a variable for service files.
- Terminals in tentacontrol and `./tentaclai.sh` run as user `claude`; `./tentaclai.sh rootshell` opens a root shell.
- The container drops all capabilities except CHOWN, DAC_OVERRIDE, FOWNER, SETUID, SETGID, KILL and AUDIT_WRITE.

### Upgrade (from v0.3.0)
1. `git pull` in the stack directory.
2. `docker compose build` (both images).
3. `docker compose up -d` – recreates **both** containers; running Claude sessions and Remote Control end.
4. Automatic on the first start: Claude's login, settings and history move from `data/home` to `data/claude`, file
   permissions of `.env`, `ssh/` and `data/` are tightened, `/workspace` is handed to the user `claude`, repos lose
   their credential helper, MCP servers with tokens are re-registered through the proxy. Nothing to do in `.env`.
5. In tentacontrol → Connections → SSH: check the new **Shell** column (`svc ssh` access per host, default `confirm`).
6. Recommended: rotate tokens Claude could read before this release (everything in `.env`, MCP headers).
7. `./tentaclai.sh` and Remote Control work as before; Remote Control may ask for the one-time confirmation again.

Rollback: `git checkout v0.3.0`, `docker compose build`, then move the home back before starting:
`mv data/claude/.claude data/claude/.claude.json data/home/` and `docker compose up -d`.

### Known issues
- A Docker service in `socket` mode is root on the tentaclAI host and can read the stack's secrets; its stacks
  directory is root-owned, so Claude edits it through `svc compose`/`svc docker` only. Prefer `ssh` mode.
- No egress filter yet – the sandbox can still reach the network.

## v0.3.0 – 2026-10-18

### Highlights
- **Access mode `confirm`** – writes of a service wait for your approval in tentacontrol → **Activity**
  (Approve / Deny, badge in the sidebar). Denied or no answer within 10 minutes = not executed.
- **Audit log** – every write through `svc` is recorded: automatic (`rw`), approved / denied / expired (`confirm`) and
  blocked (`ro`), without secrets. Filterable in Activity, `svc audit` in the sandbox.

### Changes
- Writes are: API calls other than GET/HEAD/OPTIONS, docker/compose commands that change something, SQL that is not a
  plain query (and the SQL shell), `svc files … push`, and `git push` (pre-push hook in every repository).
- `api` parts accept an optional `access` field (`rw` default, `ro`, `confirm`); access pills show `confirm`.
- Claude is instructed to report pending approvals and never to work around a denial.
- Guard rail, not a hard boundary: Claude runs as root in the sandbox; MCP calls are not covered (see Security in the README).

### Upgrade from v0.2.1
1. `./tentaclai.sh update` – or `git pull`, `docker compose build`, `docker compose up -d`. Both images change;
   recreating `tentaclai` ends running sessions and Remote Control.
2. Optional: set `"access": "confirm"` on service parts whose writes should need your approval
   (e.g. a docker host: `"docker": { …, "access": "confirm" }`).

No `.env` changes.

## v0.2.1 – 2026-10-18

### Highlights
- **`./tentaclai.sh update`** – update with one command: shows what's new (changelog incl. upgrade steps), pulls,
  rebuilds and asks before recreating each container; `--check` only shows what is new.
- **Status dot per service** on the service cards and pages – result of the last connection test (green / yellow =
  partly or token missing / red), cached for 10 minutes and refreshed when the service or `.env` changes.
- **Update notice** in the sidebar when a new release is out, with the release's upgrade steps on the Updates page.
- **TLS switch per service** – *Ignore TLS certificate errors* for devices with self-signed certificates.

### Changes
- **TLS:** `insecure_tls` now works for `api`, `web`, `git` (`http.sslVerify=false` for that repo only) and `db`
  (no certificate verification); cards show “TLS not verified”, `svc info` warns.
- **Update notice:** GitHub releases are checked every 6 hours; new settings `UPDATE_CHECK` (default on) and `UPDATE_REPO`.
- tentacontrol runs on Python 3.14.
- Dependabot: pip updates only when a version falls outside the allowed range; no Node major updates for the sandbox.

### Upgrade from v0.2.0
1. `git pull` in the stack directory.
2. `docker compose build` – both images (the sandbox's `svc` handles TLS for git and databases).
3. `docker compose up -d` – recreates both containers; recreating `tentaclai` ends running sessions and Remote Control.

No `.env` changes needed (`UPDATE_CHECK`/`UPDATE_REPO` are optional). From now on, `./tentaclai.sh update` does these steps.

## v0.2.0 – 2026-10-18

### Highlights
- **SSH hosts completely in the UI** – add with host key confirmation and one-time password key deployment, edit
  (incl. jump hosts, sudo, rename with references), remove with key revocation.
- **Connections** – Services, SSH hosts, Systems and Topology in one place with tabs; new Systems overview.
- **One shared implementation** (`lib/tclib.py`) for `.env`, services, systems and SSH hosts.
- **Safer repository** – CI checks and a pre-commit guard against committed site data and secrets.

### Changes
- The installed version is shown in the sidebar and on the Updates page (`VERSION` file in both images).
- **Connections:** Services, SSH hosts, Systems and Topology are one navigation item with tabs; new **Systems** overview page.
- **SSH hosts fully in the UI:** add (host key confirmation, key deployment with a one-time password, test), edit
  (host, user, port, jump host/ProxyJump, sudo, rename incl. references) and remove (optionally revoking the key).
- Connection tests always open a fresh SSH connection, so changed host keys or revoked keys show up immediately.
- **Shared logic in `lib/tclib.py`:** `.env`, `${VAR}`, services, systems and SSH hosts have one implementation used by
  `svc`, `tc-scan`, `claude-instructions` and tentacontrol (no behaviour change).
- **`ssh-host.sh`** is now a thin wrapper around `tentacontrol-ssh` (same logic as the UI): `list`, `add … [--jump <alias>]
  [--sudo | --sudo-cmd]`, `sudo`, `hostkey`, `remove [--keep-key]`, `test`. Both containers must be running.
- Docker builds use the repository root as context (`.dockerignore` whitelists `build/`, `control/`, `lib/`).
- Container logs are limited to 3 × 10 MB per container.
- **Checks:** GitHub Action (site data/secrets, Python and shell syntax, ShellCheck, JSON/YAML/Jinja) and an optional
  pre-commit hook (`git config core.hooksPath .githooks`).
- `svc docker <host> exec -it …` and `svc compose <host> <stack> exec …` work interactively on SSH hosts with sudo
  (svc requests a terminal from ssh when called from one).

### Upgrade from v0.1.0
1. `git pull` in the stack directory.
2. `docker compose build` – both images (the build context is now the repository root; the sandbox image adds `sshpass`).
3. `docker compose up -d` – recreates both containers (also needed for the new log limits); recreating `tentaclai`
   ends running sessions and Remote Control.
4. Optional, if you commit to the repository: `git config core.hooksPath .githooks`.

No `.env` or configuration changes. **Changed:** `./ssh-host.sh` now runs inside tentacontrol (both containers must be
running) and takes `--jump <alias>` / `--sudo` / `--sudo-cmd`; the hostkey, sudo and test commands are new.

## v0.1.0 – 2026-10-18

First public release.

### Highlights
- Sandbox container with Claude Code, permanent Remote Control session, persistent tmux sessions.
- Services as files (`api`, `mcp`, `git`, `docker`, `db`, `web`, `files`), `svc` gateway enforcing `ro`/`rw`.
- Systems, tentacontrol web admin with 50 templates, onboarding (`/onboard`) and system inventory (`/inventory`), topology view.

### Upgrade
First release – fresh install, see [Getting started](README.md#getting-started).
