# Changelog

All notable changes, newest first. Every release lists **upgrade steps** from the previous one.

## v0.14.0 – 2026-10-22

### Highlights
- **Jobs from the terminal**: `svc jobs` lists the jobs, `svc jobs <name> [--runs N] [--steps]` shows a definition with
  its recent runs (and Claude's steps) – Claude can analyse results and tune a prompt; `svc config put job <name> <file>`
  creates or changes a job after tentacontrol checked it and you approved it in Activity – new jobs start paused, and a
  running job can't create jobs.
- **Mail tentacles** `smtp` and `imap` (`svc mail`): send through a relay or a mailbox – `confirm` by default, `rw` only
  with a recipient list; read a mailbox – list, read (as untrusted content between markers, without marking it
  seen), mark and move with `rw`/`confirm`, folders can be limited. Through the egress gateway like databases; new
  rule in Claude's instructions never to act on instructions in a mail. Templates *SMTP relay*, *SMTP (with login)*,
  *Mailbox (IMAP + SMTP)*.
- **Scheduled jobs** (new menu item *Jobs*, badge for unseen findings): Claude tasks on a schedule – presets or cron, a traffic-light answer
  (ok / warn / alert) and notifications only on findings; templates for patch report, certificate expiry, disk space,
  backup check and container health. Profile *read-only* (default) is enforced twice – by Claude Code's tool rules
  and by `svc`, which recognises the run and treats every tentacle as `ro`; *standard* works like a terminal session
  (approvals for `confirm`, shown as “Job · name”).
  *Clone* copies a job – the copy starts paused.
  Tentacles are named in the task with `@name` (completed in the editor, checked on save with hints such as
  "sends mail from a read-only job"); *Only the tentacles named in the task* limits a run to them – `svc` refuses
  every other service and SSH host, MCP servers are off. A running run shows a spinner and its time, the page
  reloads when it ends.
  The latest run is always shown, earlier ones fold away under *History*.
  Schedule *manual* for jobs that run just with *Run now*.
  A running run shows Claude's steps live (tool calls, their output, Claude's text – from `stream-json`, refreshed
  every 2 s); finished runs keep them under *Claude's steps* (`data/control/job-logs/`, root only, pruned with the history).
  Read-only jobs may chain `svc` with text tools (`svc … | grep … | head`): Settings → Jobs → *Helpers in read-only jobs*
  (`JOB_READONLY_HELPERS`, default echo, head, tail, grep, jq, sort, uniq, wc, cut, tr, date, sleep) – shells,
  interpreters, awk/sed/find/xargs and network or write tools are refused.

### Changes
- Service cards and pages show every part's access the same way – `api: rw`, `docker: confirm`, `imap: ro` – including mail parts.
- Notifications: channel type **E-mail** – server, port, STARTTLS / SSL / none (internal relay), sender, recipients
  and an optional login in a form instead of a raw Apprise URL; the password stays in `.env`.
- The live output of image builds and updates moved from `/jobs/<id>` to `/builds/<id>` – `/jobs` is the new Jobs page.
- Template *zigbee2mqtt* removed (45 templates).
- Templates *Pangolin* and *Backrest*: a note on testing – Pangolin's `GET /` answers without the key (use
  `/org/<orgId>/sites`), Backrest's API has no GET that checks the login.

### Fixes
- `svc mail … list` shows when the server received a mail (IMAP INTERNALDATE, with time zone) instead of the sender's Date header.
- `svc mail … move` refused on Dovecot (Mailcow) with "no IMAP MOVE": the server lists MOVE only after the login, and
  svc looked at the list from before it. It asks again after the login now; the sandbox test moves a mail too.

### Development
- `test-sandbox.sh`: a GreenMail SMTP/IMAP server (send, refused recipient, list, framed read, seen flag, mark, move,
  read-only job can't send); scheduled-job checks (read-only marker, standard writes, scope limits, forged markers,
  `svc config put job` with approval, `svc jobs`).

### Upgrade (from v0.13.0)
`./tentaclai.sh update`, `docker compose build`, `docker compose up -d` – both images changed (svc recognises job
runs, mail, `svc jobs`); recreating `tentaclai` ends running sessions and Remote Control. Jobs start empty. The new
notification events *Job finding* and *Job failed* go to the channels that get *Approval needed* until you change them in
Notifications. New optional `.env` variable `JOB_READONLY_HELPERS` (empty = the defaults). Nothing to migrate; to roll
back, check out `v0.13.0` and rebuild – `data/control/jobs.json`, `job-history.json` and `job-logs/` are then unused.

## v0.13.0 – 2026-10-21

### Highlights
- **New area *Claude*** – everything that steers Claude in one place, in tabs: **Instructions** (a diagram of what
  Claude reads every session and what it looks up when needed, an editor for the site rules `local/CLAUDE.md` with
  backup, the generated `CLAUDE.md` to view), **Memory** (what Claude noted for itself – view, delete entries),
  **Models**, **Skills**, **Usage** (moved from Settings) and **settings.json** (Claude Code's own settings without
  the parts the other tabs manage; secret-looking env values masked, JSON checked, last 5 versions kept).
  *Settings* keeps tentaclAI itself: General, Notifications, Security.
- **Approvals on every page:** a banner above each page (but Activity) shows pending approvals with Approve / + 15 min /
  Deny (network: Allow 10 min / Deny) – no need to switch to Activity.

### Changes
- Services and Systems: the create forms are folded away behind *Add service* / *Add system* buttons in the header.
- Every page has a small **?** in its header that opens the matching part of Help (replaces the *Help* button on
  Services); the *Add …* button comes first everywhere and stays on the right when the subtitle is long. Network:
  small **?** next to *Mode* and *IP targets*, and Help has its own *Network* section (mode, IP targets, rule order).
- Topology: the legend just says *SSH host* (the colors are explained in Help).

### Fixes
- Redaction: *key* only counts as a name part of its own (`API_KEY`, `key_file`, `apikey`) – labels like
  `…keycloak….command` stay readable.
- `local/CLAUDE.md` keeps its last 5 backups (svc and tentacontrol), not every one.
- **Site rules were not loaded:** `svc config put local` wrote `local/CLAUDE.md` with mode 0600 (svc inherits the
  entrypoint's umask 077), so Claude's `@` import skipped it without a word. svc writes it 0644 now and the
  entrypoint repairs the mode on start.
- tentacontrol cached a failed lookup of the sandbox's address for 5 s – right after the sandbox started, its
  first requests counted as "anyone else" and reached the login page. Misses are not cached any more, and any other
  address from the sandbox's network (subnet read from Docker) now counts as the sandbox – a failed lookup gives less
  access, not more.
- Safari showed the SVG favicon without its background (a bare red octopus): only the PNG icons are offered now.
- Pages with a wide tab bar no longer get wider than a phone screen – the tabs scroll.
- Usage: Claude Code's hint *28 % See what fills your context* showed its number alone on the right – it reads
  *28 % for context in every request – check with `/context`* now. A limit at 100 % shows a note that requests now
  run on extra usage (credits) if it is on – Claude Code's `/usage` doesn't report the credit balance.
- Browser terminals left their `docker exec` process running when the connection dropped – one stale tmux client per
  reload, and tmux sized Claude's window to the smallest of them. Closing a terminal now hangs its process up
  (a tmux client detaches, Claude keeps running).
- Safari showed a stand-in tab icon: it ignores SVG favicons. PNG icons (32 px, 180 px `apple-touch-icon`) are
  served too, also at `/favicon.ico` and `/apple-touch-icon.png` without login.
- Text in the browser terminal could not be selected on a Mac: tmux takes the mouse, and xterm.js only lets Shift + drag
  through on other systems. Option + drag selects on a Mac now; Cmd+C (Safari never fired xterm.js's own copy event) and `Ctrl+Shift+C` copy.
  A plain drag selects in tmux or in Claude's full-screen view: the copy (OSC 52, tmux now passes Claude's on with
  `set-clipboard on`) reaches the clipboard – right away, or on Cmd+C in Safari.
- The sidebar's approval badge stayed after the last decision (an empty answer was kept by the page).
- The favicon changed from page to page: `/favicon.ico` redirected to the login – it serves the logo now.
- **Plain `http://` from the sandbox now goes through the egress gateway:** only `HTTP_PROXY`/`HTTPS_PROXY` were set,
  but curl reads the lower-case `http_proxy` for `http://` URLs – such requests failed at name resolution instead of
  reaching the gateway (and asking for approval). Both spellings are set now. `svc` was not affected.
- `tc-scan` stopped on hosts with container labels like `…keycloak….command` holding escaped quotes: `svc`'s
  redaction cut the value at the first `\"` and left invalid JSON. `inspect` output is now redacted value by
  value as JSON (always valid), and `tc-scan` skips unreadable inspect output instead of aborting.

### Development
- `test-sandbox.sh`: the rename check cleans the last run's copies – it failed on a kept REUSE stack.
- CI: image layers come from the GitHub Actions cache (`TC_BUILD_CACHE=gha` in `test-sandbox.sh`) instead of a full
  build on every run.
- `test-sandbox.sh`: checks slower than 2 s print their time; refusal checks test the gateway's `403` instead of
  `000` (a timeout gives `000` too); the plain-http case is tested; a REUSE run waits until the gateway forgot a leftover
  test service (that wait was 30 s per run).

### Upgrade (from v0.12.0)
`./tentaclai.sh update`, `docker compose build`, `docker compose up -d` – both images changed (svc, entrypoint, tmux
settings, tentacontrol); recreating `tentaclai` ends running sessions and Remote Control. Nothing to migrate: the
entrypoint makes an existing `local/CLAUDE.md` readable for Claude again.

## v0.12.0 – 2026-10-21

### Highlights
- **Where a request comes from:** approvals in Activity, their notifications and the audit log show the requesting
  Claude session – e.g. *Remote Control · “Mailcow prüfen”*, *Claude terminal · /workspace*, *Onboarding patchmon*,
  *shell (no Claude session)*. sudo keeps exactly four variables for this (`CLAUDE_CODE_SESSION_ID`,
  `CLAUDE_CODE_ENTRYPOINT`, `TMUX`, `TMUX_PANE`) – never the session's access token; `svc` and tentacontrol check every
  value. The session title comes from the session files tentacontrol already reads for Usage. It is a hint for you,
  not a security feature: Claude controls these values.

### Fixes
- Usage: Claude Code's `/usage` now lists several periods (last 24 h, last 7 days) with tips – they ran together into
  one list. Each period is its own block now, with the share of each tip and the top skills / MCP servers.

### Upgrade (from v0.11.0)
`./tentaclai.sh update`, `docker compose build`, `docker compose up -d` – recreating `tentaclai` ends running sessions and
Remote Control (needed for the new sudo rule).

## v0.11.0 – 2026-10-21

### Highlights
- **Two-factor sign-in for tentacontrol** (Settings → **Security**): a code from an authenticator app after the password
  (TOTP, RFC 6238 – no new dependency). Set up with a QR code; 10 one-time **recovery codes** for a lost phone (stored as
  hashes, a sign-in with one sends a notification); **“Trust this browser”** skips the code for 30 days – signed cookie
  plus a server-side list, so single browsers or all can be revoked. Switching off needs the password and a code; on
  the host `./tentaclai.sh reset-2fa`. A password reset also revokes the trusted browsers. Optional – off until you
  switch it on.

### Changes
- **Docker hosts run over SSH only** – the `socket` mode for docker services is gone. It mounted the host's Docker
  socket into the sandbox, which made Claude root on the host and undid the privilege separation (`.env`, SSH key).
  Every docker host, the tentaclAI host too (an SSH host for `host.docker.internal`), is reached through `svc` over SSH.
  `svc` refuses an old `"mode": "socket"` entry with a hint, the service page marks it *not usable*.
- With it go `compose.generated.yaml` (and its comparison in the *Apply now* hint), the Docker CLI in the sandbox image
  (1.47 → 1.26 GB), the template *Docker host (local, socket)*, the per-host Claude terminal tabs and
  `./tentaclai.sh host`. `./tentaclai.sh init` points to SSH hosts instead of offering a socket service.
- tentacontrol keeps the Docker socket – it runs the sandbox; *Docker socket proxy* is off the roadmap.

### Upgrade (from v0.10.0)
`./tentaclai.sh update`, `docker compose build`, `docker compose up -d` – recreating `tentaclai` ends running sessions and
Remote Control. Two-factor sign-in stays off until you set it up under Settings → Security. On its first start tentacontrol removes `compose.generated.yaml` from `COMPOSE_FILE` in `.env` (the
stale file can be deleted). **If you used a docker service in `socket` mode:** add the host under Tentacles → SSH hosts
(this host: hostname `host.docker.internal`, a user allowed to run docker or a sudo prefix), then set `"mode": "ssh"`
and `"ssh_host": "<alias>"` in the service and remove `"socket"`.

## v0.10.0 – 2026-10-21

### Highlights
- **Rename a service**: *Rename* on the service page – the file, `svc <name>` calls in its examples and notes, and for
  git services root's mirror and Claude's working copy (`/workspace/repos/<name>`) move together; `svc sync` updates
  the catalog and MCP entries. Tokens, system and rules stay.

### Security
- **The sandbox can no longer reach tentacontrol's admin UI:** tentacontrol shares the sandbox network for `svc`'s
  approvals, which also exposed the login (behind it: the Docker socket) and the gateway's token-less
  `/internal/egress/*` endpoints – Claude could have created network approvals itself. Requests are now checked by
  their source: the sandbox gets only `svc`'s endpoints (with its token), the gateway only the egress endpoints,
  nobody else the egress endpoints; the web terminal refuses the sandbox too.
- **Secrets of other services are blanked** in free read-only output that `svc` passes on: `docker … inspect`
  (container env, labels), `compose … config` (interpolated `.env` values) and `systemctl show|cat` – values of
  secret-looking names and passwords in URLs become `[redacted by svc]`.
- tentacontrol no longer follows symlinks in Claude's session directory (usage, skill descriptions).
- `svc` keeps the `.env` values to itself instead of putting them into its process environment – ssh, docker, compose
  and git no longer inherit every service token (a local `docker compose` would even have preferred them over a
  stack's own `.env` values).
- Credentials are recognised by where a service uses them (`auth`, `headers`, MCP `env`, `password`/`token` fields),
  not only by their name – e.g. `NETBIRD_PAT` was shown in clear on the service page and in Settings. More name
  hints too (`PASS`, `PAT`, `AUTH`, `CREDENTIAL`, `PRIVATE`, `COOKIE`).
- Escaped the error output of the service page's test button.

### Fixes
- `./tentaclai.sh update` goes to the **latest release** instead of the newest commit on `main` (`--main` for that);
  the Updates page announces releases, so both now agree.
- The egress gateway's list of seen destinations is capped (2000 entries, unused for 90 days are dropped) – it grew
  without limit.
- A change on Settings → Skills/Models that couldn't be applied said "the sandbox is not running" even when it ran an
  older version – the message now names the version and says to recreate it.
- README: DNS is not a way out – the sandbox resolves only the stack's containers.
- A second service created from the same template shared the first one's token variables (most templates use fixed
  names like `HA_TOKEN`) – entering a token for one silently changed the other. It now gets its own variables
  (`PATCHMON_TOKEN_KEY` → `PATCHMON_SKYNET_TOKEN_KEY`); services of a different kind keep sharing on purpose (e.g.
  `GH_TOKEN` for the GitHub API and a git repo). The service page shows which other services use a variable.
  Existing pairs are not changed automatically – check the *Tokens & variables* card.
- Egress gateway: a service that was just enabled (or whose last token was just set) asked for a network approval
  on its first use – the gateway only re-reads its policy every 15 s. Before it asks, it now re-reads the policy once
  and decides again.

### Changes
- Service templates fill in **`example.com`** instead of the site's `TC_DOMAIN` – no guessed addresses; the service page
  warns as long as `example.com` is still in a service. `TC_DOMAIN` now only suggests the tentacontrol address in
  `./tentaclai.sh init`.
- Template **PatchMon**: the integration API takes a token *key and secret* as Basic auth under `/api/v1/api` –
  the template had a single bearer token and the wrong base URL. Existing PatchMon services: switch `auth` to
  `{"type": "basic", "user": "${PATCHMON_TOKEN_KEY}", "password": "${PATCHMON_TOKEN_SECRET}"}`, `base_url` to
  `…/api/v1/api` and `test_path` to `/hosts`.
- Services without a `test_path`: the *Test API* card warns that `svc test` only checks `GET /` (often a 404, or a
  check that passes without the token), pre-fills the path from the template of the same name and saves it with
  *Save as test path*; `svc test` prints the same hint.
- Settings → Skills: `inventory` and `onboard` are marked *required by tentaclAI* and can't be switched off – the
  inventory terminal and *Onboard with Claude* start them; an "off" skill can't even be called as `/command`.
  */command only* is allowed (verified: it still runs when started as `claude /inventory`).
- tentacontrol asks Claude Code for connectors and skills once an hour and right after a sign-in (was: connectors every
  10 minutes, skills every 30) – `claude mcp list` health-checks every MCP server each time. *Check* still works at once.

### Development
- CI: `actions/checkout@v5` and `actions/setup-python@v6` (Node 20 actions are deprecated); Dependabot also watches the
  GitHub Actions, the egress image and `tests/requirements.txt`.
- `scripts/release.sh` pushes main and waits for the green GitHub `checks` run of that commit before it creates the
  release commit, tag and release – a red CI stops it before anything is tagged.
- tentacontrol's `main.py` (1550 lines) is split: app, middleware and background loops stay (165 lines), pages and
  endpoints live in `control/app/routes/` (one router per area), shared helpers in `web.py`, the small JSON state
  files go through `jsonstore.py`. The gateway's rule order is one function (`tclib.egress_rule`), shared with the
  Network page – they disagreed on allowlist vs. tentacle.
- Stage 1 renders every parameterless page found in the app's routes – no hand-kept list; stage 2/3 check a few pages
  and the vendored assets in the real container.
- `scripts/test-quick.sh`: test packages in `~/.cache/tentaclai-test-deps` (outside the stack, so backups skip them),
  the dummy stack in `/tmp` is removed after each run, `httpx2` for Starlette's TestClient (no deprecation warning left).

### Documentation
- README: configuration table complete (`EGRESS_*`, `CLAUDE_TELEMETRY`, `UPDATE_*`, `NOTIFY_URL_*`), repository layout
  with `egress/`, `routes/`, `scripts/`, `tests/` and the three `data/` directories, the Usage page, corrected: where
  the Claude login lives (`data/claude`), `HOST_DOCS` is referenced (not imported), DNS is no way out.
- Help: services have seven parts (not four), test path, rename and shared token variables, the Usage page, the new
  security measures, more troubleshooting; CONTRIBUTING: the three test stages, `release.sh`, template guidance.

### Upgrade (from v0.9.1)
`./tentaclai.sh update` (or `git pull`), `docker compose build`, `docker compose up -d` – all three containers changed.
**Recreate `tentaclai`** (ends running sessions and Remote Control): the `svc` fixes – blanked secrets, no tokens in
child processes – only take effect in a new sandbox container. No `.env` changes. Services created from older templates
may lack `test_path` – their service page now says so and suggests the template's value. From this release on
`./tentaclai.sh update` goes to the latest release tag (`--main` for unreleased changes). Developers: the old
`.cache/test-deps` in the stack directory can be deleted.

## v0.9.1 – 2026-10-20

### Fixes
- tentacontrol's regular `claude -p /usage` and `/context` calls no longer leave a session file behind each time
  (`--no-session-persistence`); sessions without a single reply are no longer listed on the Usage page.
- tentacontrol starts its background tasks through FastAPI's `lifespan` instead of the deprecated `on_event`.

### Development
- `scripts/release.sh X.Y.Z "subtitle"`: checks (main, clean, not behind, Unreleased with Upgrade part), stage 1 and
  the full sandbox test, then CHANGELOG/VERSION, commit, tag, push and the GitHub release from the changelog notes;
  `--dry-run` changes nothing.

### Upgrade (from v0.9.0)
`git pull`, `docker compose build control`, `docker compose up -d control` – only tentacontrol changed, running Claude
sessions are not affected. Optional: session files that only hold a `/usage` or `/context` call can be deleted from
`data/claude/.claude/projects/` (they contain no reply).

## v0.9.0 – 2026-10-20

### Highlights
- **Settings → Models**: model and effort per task – *Default* (every session, terminal and Remote Control),
  *Subagents* and the set-up commands */inventory and /onboard*; aliases (`fable`, `opus`, `sonnet`, `haiku`) or an own
  model id, *Standard* = Claude Code decides. Applied right away (new sessions) and on every container start: `model`,
  `effortLevel` and `env.CLAUDE_CODE_SUBAGENT_MODEL` in Claude's `settings.json`, `model:`/`effort:` in the commands'
  frontmatter. The page also shows the usage per model of the last 14 days.

### Changes
- **Behaviour change:** subagents and `/inventory`·`/onboard` run on **Sonnet** by default (less plan quota for
  searches and set-up). The default model of the sessions stays Claude Code's choice. Back to the old behaviour:
  Settings → Models → *Standard*.

### Fixes
- Settings → Skills: the *Mode* select was squeezed to its arrow; descriptions of skills with a colon in the name
  (`anthropic-skills:pdf`) were missing.
- Usage: the billing line showed Claude Code's "Warning: … blocked by enterprise policy" line instead.

### Development
- Tests in three stages: `scripts/test-quick.sh` (no Docker, seconds – syntax, ShellCheck, pytest unit tests for tclib,
  parsers and connector/skill state, every tentacontrol page rendered with dummy data; also the CI `checks` job),
  `REUSE=1 ONLY=<sections> scripts/test-sandbox.sh` (targeted, reuses the test stack, rebuilds only changed images) and
  the full `scripts/test-sandbox.sh` before every release. Waits poll instead of sleeping; the gateway's policy interval
  is configurable (`EGRESS_REFRESH`, default 15 s, 1 s in the tests). Full run: 98 s → 29 s.

### Upgrade (from v0.8.0)
`git pull`, `docker compose build`, `docker compose up -d` – recreating `tentaclai` ends running sessions and Remote
Control. Nothing to migrate; subagents and the set-up commands use Sonnet afterwards (see Changes).

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
