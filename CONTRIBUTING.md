# Contributing

Thanks for your interest in tentaclAI! Issues, ideas and pull requests are welcome.

## Ground rules

- **Never commit site data.** `.env`, `services/*.json`, `systems/*.json`, `ssh/`, `local/CLAUDE.md`, `compose.override.yaml`
  and `data/` are git-ignored on purpose. Check `git status` before every commit – no tokens, hostnames or IPs of your setup.
- **Keep it generic.** Code, templates and docs must work for any installation; site specifics belong in the files above.
- **English** for code, UI, messages, docs and templates.
- **Guard rails first.** Changes to `svc` must keep `ro`/`rw` enforcement and never put secrets into command lines, logs or the UI.

## Development

- Test in an isolated instance (own compose project name, own container names and port, scratch `data/`) instead of your
  running stack – recreating the real `tentaclai` container ends running sessions and Remote Control.
- Quick checks: `python3 -m py_compile build/svc build/tc-scan control/app/*.py` and `bash -n *.sh build/*.sh`.
- UI: tentacontrol is FastAPI + Jinja + HTMX; static assets are vendored in the image, no CDNs at runtime.

## Service templates

New templates go to `services/_templates/<name>.json` with a `_meta` block (`title`, `category`, `setup`), `${VAR}`
placeholders for secrets (prefer `EXAMPLE_…` names – they are renamed per service) and `{{DOMAIN}}` for the site domain.

## Pull requests

Small, focused PRs with a short description of *what* and *why*. PRs are squash-merged.
Changes in behaviour get an entry under **Unreleased** in `CHANGELOG.md` – including upgrade steps if an existing installation needs any.
