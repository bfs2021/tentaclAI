# Changelog

All notable changes, newest first. Every release lists **upgrade steps** from the previous one.

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
