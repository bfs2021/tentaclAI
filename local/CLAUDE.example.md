# Site: <host name> (<what this host is for>)

<!-- Copied to local/CLAUDE.md by ./tentaclaude.sh init. Imported into Claude's instructions on every start.
     Easiest way to fill it: run the system inventory (/anamnese) – Claude scans and asks you. -->

Language: talk to the user in <German/English/…>.

## Overview
- Purpose of the host, who uses it, how critical it is.

## Hosts
| Host | Access | Role | Never touch |
|---|---|---|---|
| this host | – | Docker host | … |
| vps1 | `ssh vps1` | reverse proxy / tunnel | restarting X interrupts all tunnels – ask first |

## Stacks
| Stack | Purpose | Criticality | Rules |
|---|---|---|---|
| example | … | high | update only via `./update.sh` |

## Services & network
- Container manager (Arcane/Portainer/…): changes only via … / directly with `docker compose` is fine.
- Reverse proxy & DNS: new HTTP services get … ; DNS entries are created by …
- Remote access / VPN: …

## Backups
- What is backed up where and when; what to back up before changes.

## Rules & conventions
- Secrets in `.env` next to the compose file, referenced via `${VAR}`.
- Image tags: pinned / latest; who updates.
- When to ask before acting: …
