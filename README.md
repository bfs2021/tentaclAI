<p align="center">
  <img src="control/app/static/logo.svg" width="96" height="96" alt="tentaclaude logo">
</p>

<h1 align="center">tentaclaude</h1>

<p align="center"><strong>Eight arms, one brain.</strong><br>
Sandbox + service gateway for Claude Code.</p>

<p align="center">
  <img alt="Claude Code" src="https://img.shields.io/badge/Claude_Code-sandbox-7c5cf0">
  <img alt="Docker" src="https://img.shields.io/badge/runs_on-Docker-22d3ee">
  <img alt="Services" src="https://img.shields.io/badge/services-docker%20·%20api%20·%20mcp%20·%20git%20·%20db%20·%20web%20·%20files-a78bfa">
</p>

Claude Code in a container that works **only through the services you connect** – Docker hosts, APIs, MCP servers,
git repositories, databases, web sources and files on SSH hosts – each with its own rules (`ro` / `rw`), grouped into
**systems**. Use it from the host terminal, from any container console, remotely via **Remote Control**
(claude.ai/code, Claude app) – and set everything up in the web admin **tentacontrol**.

<p align="center">
  <img src="docs/screenshots/topology.png" alt="Topology: what Claude can reach and how" width="900">
</p>

| | |
|---|---|
| **One brain, many arms** | Every service is one JSON file: Docker host (local socket or SSH, optional sudo), REST API, MCP server, git repo, PostgreSQL/MySQL, website with login, directory on a host. |
| **Guard rails** | `ro`/`rw` per service enforced by `svc`, tokens never in commands, read-only DB sessions, push blocked for read-only repos. |
| **Systems & topology** | Group services per host with shared rules; see everything as a live graph with containers, search and saved layout. |
| **Always reachable** | Remote control session with QR code, web terminal, guided onboarding (`/onboard`) and system inventory (`/inventory`). |

<p align="center">
  <img src="docs/screenshots/services.png" alt="Services grouped by system" width="900">
</p>

The stack is **generic**. Everything site-specific lives in three places:

| File | Contents |
|---|---|
| `.env` | domain, resources, remote control, tokens – created by `./tentaclaude.sh init`, editable in tentacontrol |
| `compose.override.yaml` | reverse proxy, extra mounts, labels for your container manager (template: `compose.override.example.yaml`) |
| `local/CLAUDE.md` | site rules and knowledge for Claude (imported into its instructions together with `HOST_DOCS`) |

Plus your instance data: `services/*.json` (incl. your Docker hosts), `ssh/config.d/*.conf`, `data/`, `workspace/`.
`compose.generated.yaml` is written by tentacontrol from the docker services (socket + stacks mounts) – never by hand.

## Install

```bash
git clone <this repo> /srv/stacks/tentaclaude && cd /srv/stacks/tentaclaude
./tentaclaude.sh init              # guided setup: paths, domain, access (port / nginx-proxy / Traefik → compose.override.yaml),
                                   # git identity, remote control, limits – then builds + starts tentacontrol
./tentaclaude.sh init --defaults   # same without questions
```

Afterwards run the **system inventory** (tentacontrol → Overview → System inventory, or `./tentaclaude.sh inventory`):
`tc-scan` collects host, stacks, containers, proxies, services and SSH hosts read-only (never env/secrets), Claude asks
about purpose, criticality, taboos, backups and conventions and writes the result – after your approval – into `local/CLAUDE.md`.
For every new service: **Test connection** and **Onboard with Claude** (`/onboard <service>`) on its page in tentacontrol –
Claude tests and explores it read-only, asks what it is for and what it may do, and records that in the service file.

Public repository: `.gitignore` keeps `.env`, `compose.override.yaml`, `local/`, `services/*.json`, `ssh/keys`, `ssh/config.d`,
`data/` and `workspace/` out; the `*.example` files are the templates for them.

Then in tentacontrol: **Settings** (check paths, domain, git identity) → **Overview → Create & start** →
**Terminal** (Claude login, confirm Remote Control) → **Services** (from templates, add tokens).
Without a reverse proxy tentacontrol listens on `127.0.0.1:8484` (`CONTROL_BIND`, `CONTROL_PORT`).

## Layout

```
tentaclaude/
├── compose.yaml              generic: services claude (tentaclaude, no Docker access) + control (tentacontrol)
├── compose.generated.yaml    mounts for local docker services (generated)
├── compose.override.*.yaml   site additions (example + your own)
├── .env / .env.example       settings & secrets (mode 600)
├── tentaclaude.sh            host helper: init, open Claude/RC/shell, svc, passwd, setup-url
├── ssh-host.sh               connect/disconnect SSH targets (config + host key + key deployment + test)
├── build/                    tentaclaude image: Claude Code, docker CLI/compose, gh, git, python, node, tmux
│   ├── entrypoint.sh         site CA, git identity, ssh key, Claude instructions, svc sync
│   ├── CLAUDE.base.md        generic rules for Claude (claude-instructions renders them with site values)
│   ├── start.sh / rc-loop    remote control in tmux "claude-rc" (reads RC_* from .env on every restart)
│   ├── cc                    Claude in persistent tmux sessions
│   ├── svc                   service CLI: list/info/sync, API calls with injected auth, git repos
│   ├── tc-scan               read-only inventory (all docker hosts, stacks, services, SSH hosts)
│   └── commands/             Claude slash commands: /inventory (site inventory → local/CLAUDE.md),
│                             /onboard <service> (test + explore a service, agree on purpose and rules)
├── control/                  tentacontrol: FastAPI + HTMX + xterm.js
├── services/                 one JSON per service (api / mcp / git), _templates/ with 43 templates
├── ssh/                      mounted as /root/.ssh: config, config.d/<host>.conf, keys/, known_hosts
├── local/CLAUDE.md           site rules for Claude (template: local/CLAUDE.example.md)
├── data/home, data/control   persistent /root (Claude login) / tentacontrol password hash + session secret
└── workspace/                /workspace: projects, repos/ (git services), SERVICES.md (generated)
```

## Configuration (.env)

| Variable | Meaning |
|---|---|
| `TC_DIR` | absolute host path of this stack (mounted under the same path) |
| `COMPOSE_FILE` | `compose.yaml:compose.generated.yaml:compose.override.yaml` |
| `DOCKER_SOCKET` | Docker socket of this host – only for tentacontrol (runs/rebuilds tentaclaude) |
| `HOST_DOCS` | optional Markdown file about the host, imported into Claude's instructions |
| `CA_CERT` | optional own root CA (PEM) – trusted by curl, git, python and node in the container |
| `TC_HOSTNAME`, `TC_DOMAIN` | name of the Docker host for Claude; domain that prefills templates (`{{DOMAIN}}`) |
| `CONTROL_URL`, `CONTROL_BIND`, `CONTROL_PORT` | where tentacontrol is reachable |
| `CLAUDE_CPUS`, `CLAUDE_MEMORY` | resource limits of the claude container |
| `CLAUDE_CODE_VERSION`, `GIT_USER_*` | Claude Code version, git identity |
| `RC_ENABLED`, `RC_NAME`, `RC_WORKDIR`, `RC_EXTRA_ARGS`, `RC_RESTART_DELAY` | remote control – re-read on every session restart ("Restart" on the overview) |
| everything else | service tokens, referenced as `${VAR}` in `services/*.json` |

## Services (Docker / API / MCP / git)

Everything Claude can reach is a service. One file `services/<name>.json` per service – any combination of:

```json
{
  "name": "paperless",
  "description": "Paperless-ngx – documents",
  "api": { "base_url": "https://paperless.example.lan/api",
           "auth": { "type": "header", "header": "Authorization", "value": "Token ${PAPERLESS_TOKEN}" } },
  "mcp": { "type": "http", "url": "https://paperless.example.lan/mcp", "headers": { "Authorization": "Token ${PAPERLESS_TOKEN}" } },
  "git": { "url": "https://github.com/OWNER/REPO.git", "access": "ro", "auth": { "type": "token", "token": "${GH_TOKEN}" } }
}
```

Docker hosts are services too:

```json
{ "name": "nas",    "docker": { "mode": "socket", "socket": "/var/run/docker.sock", "stacks_dir": "/srv/stacks", "access": "rw" } }
{ "name": "vps1",   "docker": { "mode": "ssh", "ssh_host": "vps1", "stacks_dir": "/opt/stacks", "access": "ro" } }
```

- `socket`: socket + stacks dir are mounted into the claude container (via `compose.generated.yaml`, applied with "Apply now");
  docker context `<name>`. `ssh`: docker context `ssh://<ssh_host>`, compose runs on the remote host in its stacks dir.
- `access`: `ro` = only read-only docker/compose commands through `svc`; `rw` = changes allowed (Claude asks first).
- Without any docker service the sandbox has **no** Docker access at all.

- `api.auth.type`: `none` | `bearer {token}` | `header {header, value}` | `basic {user, password}` | `query {param, value}` | `oauth2_client_credentials {token_url, client_id, client_secret, scope?, audience?, client_auth?}` (token cached until shortly before expiry); `insecure_tls` for self-signed devices.
- `git`: cloned to `/workspace/repos/<name>`; `ro` blocks push (pushurl + pre-push hook); tokens only via the credential helper `svc git-credential`, never in `.git/config`.
- A part is inactive while one of its `${VAR}`s is empty; `"enabled": false` parks the service.
- `svc` reads `.env` on every call – new tokens work immediately (only `GH_TOKEN`, `GIT_*`, `RC_*`, paths and limits need a container recreate).
- Templates (`services/_templates/`, selectable in tentacontrol): Home Assistant, Homematic CCU, Node-RED, n8n, Arcane, Portainer, Proxmox, TrueNAS, Grafana, InfluxDB, Pangolin, NetBird, UniFi, AdGuard, OPNsense, pfSense, Mailcow, SentinelOne, NinjaOne, Mealie, Immich, Paperless, Nextcloud, Jellyfin, *arr, GitHub/GitLab/Gitea, git repos, custom API/MCP, …

In the container:

```bash
svc list | info <name>                 # status / details
svc <name> GET /path [curl args]       # API call, auth injected (tokens never in argv)
svc sync                               # MCP → Claude config, clone missing repos, regenerate SERVICES.md
svc git | svc git sync [name]          # repos: status / clone + fetch + apply ro/rw
svc docker [name <args>]               # docker hosts / docker CLI against one host (svc docker gate ps)
svc compose <name> <stack> <args>      # compose in <stacks_dir>/<stack> of that host (local or via ssh)
svc test <name>                        # connection test of every part of a service
cc [dir] | cc rc                       # Claude in tmux / remote-control session
tc-scan                                # read-only inventory of all docker hosts, services and SSH hosts
```

## Databases, web sources, files

- `db` – PostgreSQL / MySQL / MariaDB, direct connection, `access ro|rw` (ro = read-only session). `svc db <name> "<SQL>"`, `tables`, `describe`.
- `web` – websites with auth `none|basic|bearer|header|form` (cookie login, optional CSRF field), same-host only. `svc web <name> <path>` → readable text.
- `files` – directory/file on an SSH host without bind mount (`ssh_host` or the system's). `svc files <name> ls|tree|cat|grep|pull|diff|push`.

Templates under Services (categories Databases, Web sources, Files); details in tentacontrol → Help.

## Systems

A system (`systems/<name>.json`) groups the services of one host or appliance – SSH host, docker host, APIs – with a description
and rules that apply to every member. Services join with `"system": "<name>"` (tentacontrol → Services → system → Edit);
services without a system stand alone. Docker services of mode `ssh` without `ssh_host` use the system's SSH host.
In the container: `svc systems [name]`, the catalog lists systems and their rules. Example: `systems/_example.json`.

## Topology

tentacontrol → Topology: graph of tentaclaude, systems, services and SSH hosts (cytoscape.js, vendored) with live SSH status,
optional stacks/containers of all docker hosts, search, movable nodes with a saved layout and PNG export.

## SSH

Own key `ssh/keys/tentaclaude_ed25519`, one file per host in `ssh/config.d/`. `./ssh-host.sh add <alias> <user@host[:port]>`
(or tentacontrol → SSH) writes the config, confirms the host key, deploys the key and tests the login. `host.docker.internal` = the Docker host.

**Non-root users:** `--sudo` (or `--sudo-cmd "doas"`) when adding, later `./ssh-host.sh sudo <alias> [<cmd>|off]` or the sudo column
in tentacontrol → SSH. Stored as `# tentaclaude: sudo=sudo -n` in the Host block; `svc docker`/`svc compose`, `tc-scan` and Claude's
instructions use it. Needs passwordless sudo, ideally limited (e.g. `deploy ALL=(root) NOPASSWD: /usr/bin/docker`).

## tentacontrol

Overview, Terminal (xterm.js into the container – Claude login, RC, shell), Services (JSON editor, tokens write-only, test calls,
git clone/fetch, templates), Settings (`.env`), SSH, Updates (rebuild image + recreate with live log), Logs, Help.
Own login (bcrypt, 12 h session, SameSite=Strict, Secure behind https, Origin check, login throttling).
Password reset: `./tentaclaude.sh passwd`. tentacontrol updates itself only from the host: `docker compose up -d --build control`.

## Security

- Both containers have the Docker socket = **root on the host**. The sandbox separates tools and files, not privileges.
- tentaclaude runs as root (stack files are usually root-owned); Claude's permission prompts stay active.
- Secrets never reach the browser; `svc` passes them to curl via 0600 files on tmpfs.
- `data/home` holds the Claude login, `ssh/keys` the private key – protect/backup accordingly.

## Roadmap

- Audit log: every write action through `svc` (non-GET requests, rw docker/compose commands, SQL writes) is logged and viewable in tentacontrol.
- Access mode `confirm`: in addition to ro/rw, write actions need approval with a click in tentacontrol.
- Scheduled jobs: run Claude tasks on a schedule (e.g. patch report, certificate expiry); per-service monitoring builds on this.
- Monitoring per service: Claude checks connected services regularly and reports status and recommendations.
- Topology: next – connections between systems and containers (reverse proxy → container, tunnels), status from monitoring.
- Export/import: configuration and templates, e.g. for a second installation or to share templates.
- AI assistant in tentacontrol for the whole configuration (can be switched off).
- OAuth with browser login (authorization code, e.g. Google) as auth type, with templates. Client credentials already exist.
- Real sandbox & security: harden the container (non-root, read-only root filesystem, no capabilities), egress allowlist (only service hosts reachable), keep secrets away from Claude (secret broker instead of a readable `.env`), Docker socket only through a filtering proxy, preconfigured Claude Code permissions/sandbox, prompt-injection safeguards for content from services.
- SSH hosts fully in the UI: add and edit hosts with a form instead of the terminal (alias, user@host, port, optional ProxyJump), confirm the host key fingerprint in the UI, enter the password once to deploy the key (never stored), automatic test afterwards.
- Edit SSH targets: change existing hosts in the UI (hostname, user, port, ProxyJump, rename the alias including references in docker services and systems); today only the sudo setting is editable.
