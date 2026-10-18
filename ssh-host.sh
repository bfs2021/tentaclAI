#!/usr/bin/env bash
# Connect (or disconnect) a host to tentaclai via SSH. Run on the Docker host (or inside tentaclai / via tentacontrol).
#
#   ./ssh-host.sh add <alias> <user@host[:port]> [--sudo] [--sudo-cmd "<cmd>"]
#   ./ssh-host.sh sudo <alias> [<cmd>|off]     show / set / remove the sudo prefix of a host
#   ./ssh-host.sh remove <alias>
#   ./ssh-host.sh list
#   ./ssh-host.sh test [alias]
#
# add:
#   1. writes ssh/config.d/<alias>.conf
#   2. fetches the host key, shows its fingerprint and stores it in ssh/known_hosts
#   3. appends the tentaclai public key to ~/.ssh/authorized_keys of <user> on the target
#      (like ssh-copy-id); logs in with your usual key or asks for the password once
#   4. tests the login from inside the container
# --sudo: the user is not root – privileged commands (docker, compose, admin tasks) are run with a prefix,
#         default "sudo -n" (non-interactive → the user needs NOPASSWD sudo, ideally limited to what Claude needs).
#         Stored as "# tentaclai: sudo=<cmd>" inside the Host block.
set -euo pipefail

STACK_DIR="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
SSH_DIR="$STACK_DIR/ssh"
PUBKEY_FILE="$SSH_DIR/keys/tentaclai_ed25519.pub"
CONTAINER=tentaclai

die() { echo "Error: $*" >&2; exit 1; }
usage() { sed -n '2,/^set -euo/p' "$0" | sed '$d'; exit 1; }

[ -f "$PUBKEY_FILE" ] || die "$PUBKEY_FILE missing"
PUBKEY="$(cat "$PUBKEY_FILE")"
KEY_BODY="$(awk '{print $2}' "$PUBKEY_FILE")"

SUDO_RE='^[A-Za-z0-9 ._/=-]{1,60}$'

sudo_of() {  # alias -> sudo prefix from the "# tentaclai: sudo=" line in its Host block
    awk -v a="$1" '
        tolower($1)=="host" { cur=0; for (i=2;i<=NF;i++) if ($i==a) cur=1; next }
        cur && /^[ \t]*#[ \t]*tentacl(ai|aude):[ \t]*sudo[ \t]*=/ { sub(/^[^=]*=[ \t]*/, ""); sub(/[ \t]+$/, ""); print; exit }
    ' "$SSH_DIR"/config.d/*.conf 2>/dev/null
}

set_sudo() {  # alias cmd ("" = remove)
    local alias="$1" cmd="$2" f
    f="$(grep -lE "^[[:space:]]*[Hh]ost([[:space:]].*)?[[:space:]]$alias([[:space:]]|\$)" "$SSH_DIR"/config.d/*.conf | head -1)"
    [ -n "$f" ] || die "host $alias not found in $SSH_DIR/config.d"
    local tmp; tmp="$(mktemp "$f.XXXX")"
    awk -v a="$alias" -v c="$cmd" '
        tolower($1)=="host" { cur=0; for (i=2;i<=NF;i++) if ($i==a) cur=1; print; if (cur && c!="") print "    # tentaclai: sudo=" c; next }
        cur && /^[ \t]*#[ \t]*tentacl(ai|aude):[ \t]*sudo[ \t]*=/ { next }
        { print }
    ' "$f" > "$tmp"
    chmod --reference="$f" "$tmp" 2>/dev/null || chmod 600 "$tmp"
    mv "$tmp" "$f"
}

container_test() {
    local alias="$1" sudo; sudo="$(sudo_of "$alias")"
    local probe='echo "OK: $(whoami)@$(hostname)"'
    [ -n "$sudo" ] && probe="$probe; $sudo true 2>/dev/null && echo \"sudo ($sudo): OK\" || echo \"sudo ($sudo): FAILED – the user needs NOPASSWD sudo\""
    if [ "$(docker inspect -f '{{.State.Running}}' "$CONTAINER" 2>/dev/null)" = "true" ]; then
        docker exec "$CONTAINER" ssh -o BatchMode=yes -o ConnectTimeout=10 "$alias" "$probe"
    else
        # container not running: same image, same ssh dir, throwaway
        docker run --rm --entrypoint ssh -v "$SSH_DIR:/root/.ssh" --add-host host.docker.internal:host-gateway \
            local/tentaclai:latest -o BatchMode=yes -o ConnectTimeout=10 "$alias" "$probe"
    fi
}

cmd_add() {
    local alias="${1:-}" target="${2:-}"; shift 2 || usage
    [[ "$alias" =~ ^[A-Za-z0-9._-]+$ ]] || die "alias may only contain letters, digits, . _ -"
    [[ "$target" == *@* ]] || die "give the target as user@host[:port]"
    local user="${target%%@*}" hostport="${target#*@}" host port=22 sudo=""
    host="${hostport%%:*}"; [[ "$hostport" == *:* ]] && port="${hostport##*:}"
    while [ $# -gt 0 ]; do
        case "$1" in
            --sudo) sudo="${sudo:-sudo -n}"; shift ;;
            --sudo-cmd) sudo="${2:?--sudo-cmd needs a value}"; shift 2 ;;
            *) die "unknown option: $1" ;;
        esac
    done
    [ -z "$sudo" ] || [[ "$sudo" =~ $SUDO_RE ]] || die "invalid sudo command"
    local conf="$SSH_DIR/config.d/$alias.conf"
    [ -e "$conf" ] && die "$conf already exists (first: $0 remove $alias)"

    echo "→ host key of $host:$port"
    local scan; scan="$(ssh-keyscan -p "$port" -t ed25519,ecdsa,rsa "$host" 2>/dev/null)" || true
    [ -n "$scan" ] || die "ssh-keyscan returned nothing – host/port reachable?"
    echo "$scan" | ssh-keygen -lf - | sed 's/^/   /'
    read -r -p "Fingerprint correct? [y/N] " ok; [[ "$ok" =~ ^[jJyY]$ ]] || die "aborted"
    local khost="$host"; [ "$port" != 22 ] && khost="[$host]:$port"
    echo "$scan" | sed "s/^[^ ]*/$khost/" >> "$SSH_DIR/known_hosts"

    echo "→ adding key for $user@$host (may ask for the password)"
    ssh -p "$port" -o StrictHostKeyChecking=accept-new "$user@$host" \
        "umask 077; mkdir -p ~/.ssh; touch ~/.ssh/authorized_keys; grep -qF '$KEY_BODY' ~/.ssh/authorized_keys && echo '   key was already present' || { echo '$PUBKEY' >> ~/.ssh/authorized_keys; echo '   added'; }"

    umask 077
    {
        echo "# added $(date +%F) by ssh-host.sh"
        echo "Host $alias"
        echo "    HostName $host"
        echo "    User $user"
        [ "$port" != 22 ] && echo "    Port $port"
        [ -n "$sudo" ] && echo "    # tentaclai: sudo=$sudo"
    } > "$conf"
    echo "→ created $conf"

    echo "→ testing from the container"
    container_test "$alias"
}

cmd_remove() {
    local alias="${1:-}"; [ -n "$alias" ] || usage
    local conf="$SSH_DIR/config.d/$alias.conf"
    [ -f "$conf" ] || die "$conf not found"
    local user host port
    user="$(awk '$1=="User"{print $2}' "$conf")"; host="$(awk '$1=="HostName"{print $2}' "$conf")"
    port="$(awk '$1=="Port"{print $2}' "$conf")"; port="${port:-22}"
    read -r -p "Remove the key at $user@$host and delete $conf? [y/N] " ok; [[ "$ok" =~ ^[jJyY]$ ]] || die "aborted"
    ssh -p "$port" "$user@$host" "f=~/.ssh/authorized_keys; [ -f \$f ] && cp \$f \$f.bak && grep -vF '$KEY_BODY' \$f.bak > \$f; echo '   key removed'" \
        || echo "   could not remove the key – please delete it from authorized_keys on $host manually"
    rm -f "$conf"
    echo "→ deleted $conf (known_hosts entry is kept)"
}

cmd_sudo() {
    local alias="${1:-}"; [ -n "$alias" ] || usage
    if [ $# -lt 2 ]; then echo "${alias}: $(sudo_of "$alias" | grep . || echo 'no sudo (commands run as the SSH user)')"; return; fi
    local cmd="$2"; [ "$cmd" = off ] && cmd=""
    [ -z "$cmd" ] || [[ "$cmd" =~ $SUDO_RE ]] || die "invalid sudo command"
    set_sudo "$alias" "$cmd"
    echo "→ $alias: ${cmd:-sudo removed}"
    container_test "$alias"
}

cmd_list() {
    for f in "$SSH_DIR"/config.d/*.conf; do
        echo "== $(basename "$f" .conf)"; grep -vE '^\s*(#|$)' "$f" | sed 's/^/   /'
    done
}

cmd_test() {
    local aliases=("$@")
    if [ ${#aliases[@]} -eq 0 ]; then
        mapfile -t aliases < <(awk '$1=="Host"{for(i=2;i<=NF;i++) print $i}' "$SSH_DIR"/config.d/*.conf)
    fi
    for a in "${aliases[@]}"; do printf '%-12s ' "$a"; container_test "$a" 2>&1 | tail -1 || true; done
}

case "${1:-}" in
    add) shift; cmd_add "$@" ;;
    remove|rm) shift; cmd_remove "$@" ;;
    list|ls) cmd_list ;;
    test) shift; cmd_test "$@" ;;
    sudo) shift; cmd_sudo "$@" ;;
    *) usage ;;
esac
