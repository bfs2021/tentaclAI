# CLAUDE.md – developing tentaclAI

Guidance for Claude Code (and humans) working **on** this repository. It is not the instruction file of the sandbox –
that one is generated from `build/CLAUDE.base.md` by `build/claude-instructions`.

## What this is

tentaclAI = sandbox + service gateway for Claude Code. Two containers (`compose.yaml`):

- `claude` → container **tentaclai** (`build/`): Claude Code, `svc` (service gateway), `tc-scan`, remote control loop.
- `control` → container **tentacontrol** (`control/`): web admin, FastAPI + Jinja + HTMX, vendored JS (no CDN at runtime).

Display name **tentaclAI**, technical name **tentaclai** (container, image, paths, scripts). See `README.md` for the
architecture and `CONTRIBUTING.md` for the ground rules.

## Rules

- **Generic code only.** Site data lives in git-ignored files (`.env`, `services/*.json`, `systems/*.json`, `ssh/`,
  `local/CLAUDE.md`, `compose.override.yaml`, `data/`). Before every commit check `git diff --cached --name-only` –
  never commit them, nor tokens, hostnames or IPs of a real installation.
- **English** for code, UI, messages, docs, templates and commit messages.
- **Guard rails stay intact:** `svc` enforces `ro`/`rw`, secrets never go into argv, logs, the UI or Claude's context.
- **Test in isolation**, not on a running installation: own compose project / container names / port, scratch `data/`.
  Recreating the real `tentaclai` container ends running sessions and Remote Control – only after asking.
- Keep README, tentacontrol → Help (`control/app/templates/help.html`) and this file in sync with behaviour changes.

## Releases

Versioning: semantic versions, git tag `vX.Y.Z` on `main`, published as a GitHub release.

Every release gets an entry in **`CHANGELOG.md`** (newest first) and the same text as GitHub release notes, with:

1. **Highlights** – what is new, in a few bullets.
2. **Changes / fixes** – noteworthy changes, especially anything that behaves differently.
3. **Upgrade** – *mandatory*: exact steps to update an existing installation from the previous release, e.g.
   - `git pull` in the stack directory
   - which images to rebuild (`docker compose build`, or `build claude` / `build control`)
   - which containers to recreate – and that recreating `tentaclai` ends sessions and Remote Control
   - new or changed `.env` variables (name, default, whether required)
   - renamed or moved files and paths, config format changes, data migrations (with commands)
   - breaking changes and how to roll back
   If nothing special is needed, say so explicitly (e.g. “`git pull`, `docker compose build`, `docker compose up -d`”).
4. **Known issues** – if any.

Before tagging: run the quick checks from `CONTRIBUTING.md`, test the upgrade path from the previous release in an
isolated instance when the Upgrade section is more than a plain rebuild.
