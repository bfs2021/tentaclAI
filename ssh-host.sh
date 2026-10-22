#!/usr/bin/env bash
# SSH hosts for tentaclAI on the command line – same logic as tentacontrol → Tentacles → SSH hosts.
#
#   ./ssh-host.sh list
#   ./ssh-host.sh add <alias> <user@host[:port]> [--jump <alias>] [--sudo | --sudo-cmd "<cmd>"]
#   ./ssh-host.sh sudo <alias> [<cmd>|off]
#   ./ssh-host.sh hostkey <alias>
#   ./ssh-host.sh remove <alias> [--keep-key]
#   ./ssh-host.sh test [alias]
#
# Runs tentacontrol-ssh inside the tentacontrol container (both containers must be running).
set -euo pipefail
tty=-i; [ -t 0 ] && tty=-it
exec docker exec $tty tentacontrol tentacontrol-ssh "$@"
