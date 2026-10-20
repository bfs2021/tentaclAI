# Contributing

Thanks for your interest in tentaclAI! Issues, ideas and pull requests are welcome.

## Ground rules

- **Never commit site data.** `.env`, `services/*.json`, `systems/*.json`, `ssh/`, `local/CLAUDE.md`, `compose.override.yaml`
  and `data/` are git-ignored on purpose. Check `git status` before every commit – no tokens, hostnames or IPs of your setup.
- **Keep it generic.** Code, templates and docs must work for any installation; site specifics belong in the files above.
- **English** for code, UI, messages, docs and templates.
- **One implementation.** Parsing of `.env`, services, systems and SSH hosts lives in `lib/tclib.py` – extend it instead of
  writing another parser.
- **Guard rails first.** Changes to `svc` must keep `ro`/`rw` enforcement and never put secrets into command lines, logs or the UI.

## Development

- Enable the pre-commit check once per clone: `git config core.hooksPath .githooks` – it refuses site files and
  anything that looks like a token or private key (`scripts/check-site-data.sh`). CI runs the same check on every push.

- End-to-end tests: `scripts/test-sandbox.sh` builds the images and runs privilege separation, `svc` through the egress
  gateway, network rules and every page in a scratch copy with its own compose project (`tctest`) – never touching your
  running stack. CI runs it on every push. For manual experiments use the same isolation (own project, container names,
  port, scratch `data/`) – recreating the real `tentaclai` container ends running sessions and Remote Control.
- Quick checks after every change: `scripts/test-quick.sh` (no Docker, a few seconds – syntax, ShellCheck, unit tests,
  every tentacontrol page rendered with dummy data). End-to-end: `scripts/test-sandbox.sh` – targeted with
  `REUSE=1 ONLY=<sections>`, in full before every release (see `CLAUDE.md` → Releases).
- UI: tentacontrol is FastAPI + Jinja + HTMX; static assets are vendored in the image, no CDNs at runtime.

## Service templates

New templates go to `services/_templates/<name>.json` with a `_meta` block (`title`, `category`, `setup`), `${VAR}`
placeholders for secrets (prefer `EXAMPLE_…` names – they are renamed per service) and `{{DOMAIN}}` where the site's domain belongs (filled with the placeholder `example.com` – the user enters the real address).

## Pull requests

Small, focused PRs with a short description of *what* and *why*. PRs are squash-merged.
Changes in behaviour get an entry under **Unreleased** in `CHANGELOG.md` – including upgrade steps if an existing installation needs any.
