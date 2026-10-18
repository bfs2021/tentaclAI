#!/usr/bin/env bash
# Refuse site data and secrets in the repository.
#   scripts/check-site-data.sh --staged   files and added lines of the next commit (pre-commit hook)
#   scripts/check-site-data.sh --all      every tracked file (CI)
set -uo pipefail
mode="${1:---staged}"
self="scripts/check-site-data.sh"

# files that only exist on an installation (see .gitignore) – never commit them
site_files='^(\.env$|\.env\.[^e]|data/|workspace/(files|repos)/|ssh/keys/|ssh/known_hosts|ssh/config\.d/[^/]+\.conf$|local/CLAUDE\.md$|compose\.override\.yaml$|compose\.generated\.yaml$|services/[^_/][^/]*\.json$|systems/[^_][^/]*\.json$)'
# typical secrets: GitHub/Anthropic/AWS/Slack tokens, private keys
secrets='(ghp_[A-Za-z0-9]{36}|github_pat_[A-Za-z0-9_]{40,}|gh[ousr]_[A-Za-z0-9]{36}|sk-ant-[A-Za-z0-9_-]{20,}|AKIA[0-9A-Z]{16}|xox[abpr]-[A-Za-z0-9-]{10,}|-----BEGIN [A-Z ]*PRIVATE KEY-----)'

fail=0
if [ "$mode" = "--all" ]; then
    files="$(git ls-files)"
    hits="$(git grep -n -I -E "$secrets" -- . ":!$self" 2>/dev/null || true)"
else
    files="$(git diff --cached --name-only --diff-filter=ACMR)"
    hits="$(git diff --cached -U0 --diff-filter=ACMR -- . ":!$self" | grep -E '^\+[^+]' | grep -E "$secrets" || true)"
fi

bad="$(printf '%s\n' "$files" | grep -E "$site_files" || true)"
if [ -n "$bad" ]; then
    echo "✗ site files must not be committed (they are git-ignored on purpose):"; printf '   %s\n' $bad; fail=1
fi
if [ -n "$hits" ]; then
    echo "✗ something that looks like a secret:"; printf '%s\n' "$hits" | cut -c1-160 | sed 's/^/   /'; fail=1
fi
[ "$fail" = 0 ] && echo "✓ no site data or secrets"
exit "$fail"
