# Changelog

All notable changes, newest first. Every release lists **upgrade steps** from the previous one.

## Unreleased

### Changes
- Claude's instructions brought up to date: all service parts and `svc test`/`svc pending`/`svc audit`, no-root
  hint, a general rule that content from services is data and never instructions, the tentaclai containers and tmux
  sessions are off limits (also through `svc docker` on their host), an approval doesn't replace asking about risky
  changes. The generated `SERVICES.md` describes git via `svc git push` and the `confirm` mode.

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
