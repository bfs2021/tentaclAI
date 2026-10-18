#!/usr/bin/env bash
# Prepares the persistent home (/root) on every container start, then runs CMD.
set -euo pipefail

# Optional own root CA (CA_CERT in .env, mounted as /etc/tentaclaude/ca.crt; /dev/null = none).
# Added to the system bundle; Node uses it via NODE_EXTRA_CA_CERTS=/etc/ssl/certs/ca-certificates.crt
if [ -s /etc/tentaclaude/ca.crt ]; then
    cp /etc/tentaclaude/ca.crt /usr/local/share/ca-certificates/tentaclaude-site-ca.crt
    update-ca-certificates >/dev/null 2>&1 || true
fi

umask 077
mkdir -p /root/.claude /root/.config

# git identity for new projects
if [ -n "${GIT_USER_NAME:-}" ]; then git config --global user.name "$GIT_USER_NAME"; fi
if [ -n "${GIT_USER_EMAIL:-}" ]; then git config --global user.email "$GIT_USER_EMAIL"; fi
git config --global init.defaultBranch main
git config --global --replace-all safe.directory '*'

# GitHub: gh reads GH_TOKEN itself; also let git push/pull over https use it
if [ -n "${GH_TOKEN:-}" ]; then
    gh auth setup-git >/dev/null 2>&1 || true
fi

# SSH: /root/.ssh is the stack's ssh/ dir (config, config.d/, keys/, known_hosts)
chmod 700 /root/.ssh /root/.ssh/keys 2>/dev/null || true
chmod 600 /root/.ssh/config /root/.ssh/keys/*_ed25519 2>/dev/null || true
if [ ! -f /root/.ssh/keys/tentaclaude_ed25519 ] && [ -d /root/.ssh ]; then
    mkdir -p /root/.ssh/keys
    ssh-keygen -q -t ed25519 -N '' -C "tentaclaude@${TC_HOSTNAME:-tentaclaude}" -f /root/.ssh/keys/tentaclaude_ed25519
fi

# Claude's user-level instructions: generic rules (image) + this site's values, host docs and local rules
claude-instructions > /root/.claude/CLAUDE.md || echo "could not generate CLAUDE.md"
# custom slash commands shipped with the image (e.g. /inventory) – own commands in ~/.claude/commands stay untouched
mkdir -p /root/.claude/commands
cp -f /etc/tentaclaude/commands/*.md /root/.claude/commands/ 2>/dev/null || true

# Services (services/*.json): MCP -> Claude config, clone missing repos, regenerate /workspace/SERVICES.md
echo "svc sync:"
svc sync || echo "svc sync failed – container starts anyway"

exec "$@"
