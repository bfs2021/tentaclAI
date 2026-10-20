#!/usr/bin/env bash
# Stage 1 – fast checks without Docker (seconds): syntax, ShellCheck, JSON/YAML, unit tests and every tentacontrol
# page rendered with dummy data (tests/). Run after every change. Python packages go to ~/.cache/tentaclai-test-deps
# (pip --target, no venv or system packages needed, outside the stack so backups skip it); rebuilt when a requirements
# file changes.
#   scripts/test-quick.sh               everything
#   scripts/test-quick.sh -k skills     extra arguments go to pytest
set -euo pipefail
cd "$(dirname "$0")/.."
t0=$(date +%s)
fail() { echo "✗ $1"; exit 1; }

echo "== syntax"
python3 -m py_compile lib/tclib.py build/svc build/tc-scan build/claude-instructions control/passwd.py control/sshcli.py \
    control/app/*.py || fail "python syntax"
for f in tentaclai.sh ssh-host.sh build/entrypoint.sh build/start.sh build/rc-loop build/cc scripts/*.sh .githooks/pre-commit; do
    bash -n "$f" || fail "shell syntax: $f"
done
if command -v shellcheck >/dev/null; then
    shellcheck --severity=error tentaclai.sh ssh-host.sh build/entrypoint.sh build/start.sh build/rc-loop build/cc \
        scripts/*.sh .githooks/pre-commit || fail "shellcheck"
else
    echo "  (shellcheck not installed – skipped, CI runs it)"
fi
scripts/check-site-data.sh --all >/dev/null || fail "site data or secrets in tracked files"

DEPS="${XDG_CACHE_HOME:-$HOME/.cache}/tentaclai-test-deps"
STAMP="$DEPS/.stamp-$(cat control/requirements.txt tests/requirements.txt | sha1sum | cut -c1-12)"
if [ ! -e "$STAMP" ]; then
    echo "== test dependencies → $DEPS"
    rm -rf "$DEPS"
    python3 -m pip install --quiet --disable-pip-version-check --root-user-action=ignore --target "$DEPS" \
        -r control/requirements.txt -r tests/requirements.txt
    touch "$STAMP"
fi
export PYTHONPATH="$DEPS" PYTHONDONTWRITEBYTECODE=1

echo "== JSON and YAML"
python3 - <<'PY' || fail "json/yaml"
import glob, json, yaml
for p in sorted(glob.glob("services/_templates/*.json") + ["services/_template.json", "systems/_example.json"]):
    json.load(open(p))
for p in ["compose.yaml", "compose.override.example.yaml", *glob.glob(".github/**/*.yml", recursive=True)]:
    yaml.safe_load(open(p))
PY

echo "== unit tests and pages"
python3 -m pytest -q -p no:cacheprovider tests "$@" || fail "pytest"
echo "✓ quick checks passed in $(( $(date +%s) - t0 )) s"
