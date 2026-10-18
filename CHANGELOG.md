# Changelog

All notable changes, newest first. Every release lists **upgrade steps** from the previous one.

## Unreleased

### Changes
- **Connections:** Services, SSH hosts, Systems and Topology are one navigation item with tabs; new **Systems** overview page.
- **SSH hosts fully in the UI:** add (host key confirmation, key deployment with a one-time password, test), edit
  (host, user, port, jump host/ProxyJump, sudo, rename incl. references) and remove (optionally revoking the key).
- Connection tests always open a fresh SSH connection, so changed host keys or revoked keys show up immediately.
- **Shared logic in `lib/tclib.py`:** `.env`, `${VAR}`, services, systems and SSH hosts have one implementation used by
  `svc`, `tc-scan`, `claude-instructions` and tentacontrol (no behaviour change).
- **`ssh-host.sh`** is now a thin wrapper around `tentacontrol-ssh` (same logic as the UI): `list`, `add … [--jump <alias>]
  [--sudo | --sudo-cmd]`, `sudo`, `hostkey`, `remove [--keep-key]`, `test`. Both containers must be running.
- Docker builds use the repository root as context (`.dockerignore` whitelists `build/`, `control/`, `lib/`).

### Upgrade
1. `git pull` in the stack directory.
2. `docker compose build` – both images (build context is now the repository root; the sandbox image adds `sshpass`).
3. `docker compose up -d` – recreates both containers; recreating `tentaclai` ends running sessions and Remote Control.

No `.env` or configuration changes.

## v0.1.0 – 2026-10-18

First public release.

### Highlights
- Sandbox container with Claude Code, permanent Remote Control session, persistent tmux sessions.
- Services as files (`api`, `mcp`, `git`, `docker`, `db`, `web`, `files`), `svc` gateway enforcing `ro`/`rw`.
- Systems, tentacontrol web admin with 50 templates, onboarding (`/onboard`) and system inventory (`/inventory`), topology view.

### Upgrade
First release – fresh install, see [Getting started](README.md#getting-started).
