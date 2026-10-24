# Central management – agent protocol v1

A tentaclAI installation can be managed by a central server (tentaclAI Enterprise): it then is an **agent**. The agent
part is in tentacontrol (`control/app/central.py`, the sidebar's *Enterprise* page) and open; the server is a separate
product. This document is the contract between the two.

The agent always connects to the server, never the other way round (HTTPS, JSON). It works without the server: it
keeps the last profile and the audit entries not sent yet, and catches up when the server is back.

## Enrolling

The server issues an **enrollment code** – one string to paste into tentacontrol:

```
tce1.<base64url(JSON)>      JSON: {"url": "https://central.example.lan", "token": "<random>", "key": "<fingerprint>", "ca": "<PEM>"}
```

`key` is the fingerprint of the server's Ed25519 key (first 16 hex digits of SHA-256 over the raw public key, in groups
of four). `ca` (optional) is the certificate the agent trusts for the server's HTTPS – a private CA or the server's own
certificate; a certificate pasted in tentacontrol's enroll form wins over it. The agent creates its own Ed25519 key pair (kept in `data/control/central/`, 0600) and sends:

```
POST /api/agent/v1/enroll
{"token", "public_key", "instance_id", "hostname", "name", "version", "api": 1, "areas": [...]}
→ 200 {"agent_id", "central_key", "central_name", "profile_rev"}
```

`areas` are the parts of the configuration the local admin allows the server to manage (see Profiles). The agent
refuses a `central_key` whose fingerprint isn't the one in the code. Errors: 400 (bad request), 403 (token unknown,
used or expired), 409 (no free seat in the server's license).

## Signed requests

Every later request carries:

```
X-TC-Agent:     <agent_id>
X-TC-Time:      <unix seconds>
X-TC-Nonce:     <random, 16+ characters>
X-TC-Signature: base64url(Ed25519 over  METHOD \n PATH_WITH_QUERY \n TIME \n NONCE \n hex(SHA-256(body)) )
```

The server accepts a time within ±300 s and each nonce once. 401: unknown agent or bad signature; 410: the agent was
revoked (the agent stops and shows it).

## Calls

| Call | Body → answer | When |
|---|---|---|
| `POST /heartbeat` | status (below) → `{"profile_rev", "want_config", "time", "run_jobs"}` | every 30 s |
| `GET /profile` | → `{"profile": {...}, "sig"}` | when `profile_rev` changed |
| `POST /audit` | `{"entries": [...]}` (oldest first, ≤ 500) → `{"stored": n}` | while entries are waiting |
| `POST /config` | `{"config": <export document>, "hash", "settings_catalog"}` → `{}` | when the configuration changed or `want_config` |
| `POST /report` | `{"rev", "results": [...], "drift": [...]}` → `{}` | after applying a profile, when the result changed |
| `POST /runs` | `{"runs": [...]}` (oldest first, ≤ 50) → `{"stored": n}` | when runs of the server's jobs finished |
| `GET /packages/<name>/<version>` | → the package ZIP | when a profile names a package |
| `POST /leave` | `{}` → `{}` | the local admin leaves |

**Status** (heartbeat): `name, hostname, instance_id, version, edition, modules: [{name, title, origin, version, active}],
services: [{name, parts, test}], audit: {sent_through, waiting}, profile: {rev, applied, drift, missing, refused},
areas, jobs: [{id, name, enabled, next, last_status}]` (the server's jobs). No secrets – values of credentials never leave the agent.

**Audit entries** are sent as stored (`audit.jsonl`, `tclib.audit_entry`) plus two fields: `owner`, the account of the
installation (Claude, jobs and the system act for it; a user's own actions carry `user`), and `source`, the readable
origin ("Claude terminal · “title”", "Job · backup") when the entry has one. The server de-duplicates by `id`. After
enrolling the agent sends the last 7 days, then everything new. The local retention keeps entries that weren't sent.

**Config** is tentacontrol's export document (Settings → Export, format 1, all parts, plain secrets left out) – the
server shows it and builds profiles from it.
With it comes `settings_catalog`, the settings of Settings a profile can set (`[{key, label, help, type, group}]`,
type `text` or `bool` – `true`/`false`), so the server's profile editor offers them by name.

## Profiles

A profile is what the server wants on an agent. Signed by the server: `sig` = base64url(Ed25519 over the canonical
JSON of `profile` – `json.dumps(profile, sort_keys=True, separators=(",", ":"), ensure_ascii=False)` as UTF-8).

```json
{"rev": 7, "agent_id": "A-0001", "issued": "2026-10-24T18:00:00Z", "name": "Developers",
 "sections": {"services": {"wiki": {...}}, "settings": {"TZ": "Europe/Vienna"}, "network": {...}, ...},
 "modes": {"services:wiki": "enforced", "settings:TZ": "default"},
 "local_items": {"services": true, "network": false},
 "modules": {"tentaclai_health": {"mode": "enforced", "enabled": true},
             "tcx_reports": {"mode": "enforced", "enabled": true, "package": {"version": "1.2.0", "sha256": "…"}}},
 "allow_leave": true}
```

- `sections` uses the parts and formats of the export document; the item ids are those of the import preview
  (`services:<name>`, `settings:<KEY>`, `network:<kind>:<entry>`, `jobs:<id>`, `skills:<name>`,
  `instructions:local`, `notifications:channel:<id>`, `notifications:rules`, `ssh_hosts:<alias>`, `systems:<name>`).
- **enforced** – applied and locked: tentacontrol refuses local changes, and a change made around it (a file edited
  on the host) is reverted at the next sync and reported.
- **default** – applied where nothing local is set; a local change stays and is reported as overridden. A new profile
  value replaces it only while the local value is still the previous profile value.
- items not in the profile are local. `local_items: false` for a part: local items there are reported (never deleted).
- `modules`: built-in modules switched on/off; packages are fetched from the server and installed only when signed by
  tentaclAI (Modules → Install from file rules).
- Parts the local admin didn't allow are ignored and reported as refused. Credentials: definitions use `${VAR}`; values
  stay in the agent's `.env` – missing ones are reported and shown in tentacontrol.
- Root on the host can always change files – management is governance, not a sandbox. Every change the agent applies
  lands in its own audit log.

## Jobs from the server

A profile's `jobs:<id>` items are jobs like those of `svc config put job`; one the agent doesn't have yet starts as its
definition says (`"enabled"`, default on – an import starts jobs paused). When the agent lets the server manage jobs:
- A job the server handed out and no longer does (deleted there, or out of the profile) is deleted on the agent too.
- `run_jobs` in the heartbeat answer (job ids) starts those jobs now (trigger `central`) – only the server's own jobs.
- Finished runs of the server's jobs go to `POST /runs`: `{id, job, name, trigger, status, queued, started, ended,
  seconds, error, result: {status, title, summary, details}, proposals}` (proposals: how many – they are picked on the
  agent). A cursor (the last `ended`) keeps them from being sent twice.
