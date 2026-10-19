# bash completion for svc (tentaclAI) – installed as /etc/bash_completion.d/svc
# Service names come straight from services/*.json (readable for user claude); SSH hosts from `svc ssh` (root only).

_svc_names() {  # [part] – names of enabled services, optionally only those with that part
    local dir
    dir="$(cat /etc/tentaclai/tc-dir 2>/dev/null)/services"
    python3 - "$dir" "${1:-}" 2>/dev/null <<'PY'
import glob, json, os, sys
d, part = sys.argv[1], sys.argv[2]
for p in sorted(glob.glob(os.path.join(d, "*.json"))):
    if os.path.basename(p).startswith("_"):
        continue
    try:
        s = json.load(open(p))
    except (OSError, ValueError):
        continue
    if s.get("enabled", True) is False or (part and part not in s):
        continue
    print(s.get("name") or os.path.basename(p)[:-5])
PY
}

_svc_ssh_hosts() {
    svc ssh 2>/dev/null | awk 'NR > 1 {print $1}'
}

_svc() {
    local cur prev words cword
    _init_completion 2>/dev/null || { cur="${COMP_WORDS[COMP_CWORD]}"; words=("${COMP_WORDS[@]}"); cword=$COMP_CWORD; }
    local cmd="${words[1]:-}"
    if [ "$cword" -eq 1 ]; then
        COMPREPLY=($(compgen -W "list info test sync systems git docker compose db web files ssh pending audit config help $(_svc_names api)" -- "$cur"))
        return
    fi
    case "$cmd" in
        info|test) [ "$cword" -eq 2 ] && COMPREPLY=($(compgen -W "$(_svc_names)" -- "$cur")) ;;
        systems) [ "$cword" -eq 2 ] && COMPREPLY=($(compgen -W "$(ls "$(cat /etc/tentaclai/tc-dir 2>/dev/null)/systems" 2>/dev/null | sed -n 's/^\([^_].*\)\.json$/\1/p')" -- "$cur")) ;;
        git)
            case "$cword" in
                2) COMPREPLY=($(compgen -W "sync fetch pull push" -- "$cur")) ;;
                3) COMPREPLY=($(compgen -W "$(_svc_names git)" -- "$cur")) ;;
            esac ;;
        docker)
            case "$cword" in
                2) COMPREPLY=($(compgen -W "$(_svc_names docker) dirs" -- "$cur")) ;;
                3) COMPREPLY=($(compgen -W "ps logs inspect images stats top exec restart start stop pull" -- "$cur")) ;;
            esac ;;
        compose)
            case "$cword" in
                2) COMPREPLY=($(compgen -W "$(_svc_names docker)" -- "$cur")) ;;
                4) COMPREPLY=($(compgen -W "ps logs config pull up down restart exec images" -- "$cur")) ;;
            esac ;;
        db)
            case "$cword" in
                2) COMPREPLY=($(compgen -W "$(_svc_names db)" -- "$cur")) ;;
                3) COMPREPLY=($(compgen -W "tables describe shell --csv" -- "$cur")) ;;
            esac ;;
        web)
            case "$cword" in
                2) COMPREPLY=($(compgen -W "$(_svc_names web)" -- "$cur")) ;;
                3) COMPREPLY=($(compgen -W "--links --raw --login" -- "$cur")) ;;
            esac ;;
        files)
            case "$cword" in
                2) COMPREPLY=($(compgen -W "$(_svc_names files)" -- "$cur")) ;;
                3) COMPREPLY=($(compgen -W "ls tree cat grep pull diff push" -- "$cur")) ;;
            esac ;;
        ssh) [ "$cword" -eq 2 ] && COMPREPLY=($(compgen -W "$(_svc_ssh_hosts)" -- "$cur")) ;;
        config)
            case "$cword" in
                2) COMPREPLY=($(compgen -W "put" -- "$cur")) ;;
                3) COMPREPLY=($(compgen -W "service local" -- "$cur")) ;;
                *) COMPREPLY=($(compgen -f -- "$cur")) ;;
            esac ;;
        *)  # svc <api service> METHOD path
            [ "$cword" -eq 2 ] && COMPREPLY=($(compgen -W "GET POST PUT PATCH DELETE" -- "$cur")) ;;
    esac
}
complete -F _svc svc
