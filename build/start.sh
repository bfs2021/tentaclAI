#!/usr/bin/env bash
# Container main process: keeps the remote-control session alive in tmux "claude-rc", then idles.
# Settings are read from $TC_DIR/.env on every (re)start of the loop, so changes apply with "RC neu starten".
set -uo pipefail

tmux has-session -t claude-rc 2>/dev/null || tmux new-session -d -s claude-rc -c /workspace rc-loop
exec sleep infinity
