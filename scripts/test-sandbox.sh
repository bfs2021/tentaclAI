#!/usr/bin/env bash
# Stages 2 and 3 – end-to-end tests of the security boundaries in a scratch copy with its own compose project (tctest),
# never on the running installation. Needs Docker and internet (example.com, github.com).
#   scripts/test-sandbox.sh                      stage 3: everything, clean up afterwards (before every release)
#   ONLY=priv,egress scripts/test-sandbox.sh     stage 2: only these sections (priv svc egress notify skills pages)
#   REUSE=1 scripts/test-sandbox.sh              keep the test stack in /tmp/tctest-reuse and reuse it next time: only
#                                                images whose sources changed are rebuilt, tentaclAI state is reset,
#                                                the helper containers (postgres, sshd, ntfy …) keep running
#   KEEP=1 scripts/test-sandbox.sh               keep the stack running afterwards (docker compose -p tctest down -v)
#   remove a reused stack: REUSE=1 CLEAN=1 scripts/test-sandbox.sh
set -euo pipefail
SRC="$(cd "$(dirname "$0")/.." && pwd)"
export COMPOSE_PROJECT_NAME=tctest
SECTIONS="priv svc egress notify skills pages"
ONLY="${ONLY:-$SECTIONS}"
PASS=0 FAIL=0 T0=$SECONDS
ok() { PASS=$((PASS + 1)); printf '  \033[32m✓\033[0m %s\n' "$1"; }
bad() { FAIL=$((FAIL + 1)); printf '  \033[31m✗\033[0m %s\n' "$1"; }
check() { if [ "$2" = "$3" ]; then ok "$1"; else bad "$1 (expected '$3', got '$2')"; fi; }
want() { case ",${ONLY// /,}," in *",$1,"*) return 0 ;; *) return 1 ;; esac; }
section() { [ -n "${TS:-}" ] && echo "   ($((SECONDS - TS)) s)"; TS=$SECONDS; echo "== $1"; }
# wait_for <seconds> <command…>: poll every 0.5 s instead of fixed sleeps
wait_for() { local end=$((SECONDS + $1)); shift; until "$@" >/dev/null 2>&1; do [ $SECONDS -ge "$end" ] && return 1; sleep 0.5; done; }
for s in ${ONLY//,/ }; do case " $SECTIONS " in *" $s "*) ;; *) echo "unknown section '$s' – one of: $SECTIONS"; exit 2 ;; esac; done

if [ -n "${REUSE:-}" ]; then
    T=/tmp/tctest-reuse
    mkdir -p "$T"
else
    if docker ps -aq -f name='^tctest-' | grep -q .; then
        echo "a tctest stack exists already (REUSE/KEEP) – remove it first: REUSE=1 CLEAN=1 $0, or run with REUSE=1"; exit 2
    fi
    T="$(mktemp -d /tmp/tctest.XXXXXX)"
fi
remove_stack() {
    (cd "$T" && docker compose down -v >/dev/null 2>&1) || true
    docker image rm local/tentaclai:test local/tentacontrol:test local/tentaclai-egress:test >/dev/null 2>&1 || true
    # files there belong to root / the sandbox users now – remove them through a container
    docker run --rm -v "$T:/t" alpine:3.20 sh -c 'rm -rf /t/* /t/.[!.]* 2>/dev/null; true' >/dev/null 2>&1 || true
    rmdir "$T" 2>/dev/null || true
}
if [ -n "${REUSE:-}" ] && [ -n "${CLEAN:-}" ]; then remove_stack; echo "removed $T"; exit 0; fi
cleanup() {
    echo "   total $((SECONDS - T0)) s"
    if [ -n "${REUSE:-}" ]; then echo "stack kept for the next REUSE=1 run (remove: REUSE=1 CLEAN=1 $0)"; return; fi
    [ -n "${KEEP:-}" ] && { echo "stack kept: cd $T && docker compose down -v"; return; }
    remove_stack
}
trap cleanup EXIT

section "setup in $T"
(cd "$SRC" && git ls-files -co --exclude-standard | grep -v '^workspace/\|^data/' |
    while IFS= read -r f; do [ -e "$f" ] && printf '%s\n' "$f"; done) | rsync -a --files-from=- "$SRC/" "$T/"
cd "$T"
[ "$PWD" = "$T" ] || { echo "not in the scratch copy – abort"; exit 1; }
mkdir -p workspace data fixtures ssh/config.d ssh/keys
cat > .env <<EOF
TC_DIR=$T
COMPOSE_FILE=compose.yaml:compose.test.yaml
RC_ENABLED=false
UPDATE_CHECK=false
EGRESS_MODE=enforce
EGRESS_DIRECT_IP=block
API_TOKEN=api-secret-123
PG_PASSWORD=pg-secret-789
EOF
chmod 644 .env  # deliberately loose: the entrypoint must fix it
cat > fixtures/api.py <<'EOF'
import json
from http.server import BaseHTTPRequestHandler, HTTPServer
class H(BaseHTTPRequestHandler):
    def do_GET(self):
        ok = self.headers.get("Authorization") == "Bearer api-secret-123"
        self.send_response(200 if ok else 401); self.end_headers(); self.wfile.write(json.dumps({"auth_ok": ok}).encode())
HTTPServer(("0.0.0.0", 8000), H).serve_forever()
EOF
cat > fixtures/hook.py <<'PY'
from http.server import BaseHTTPRequestHandler, HTTPServer
class H(BaseHTTPRequestHandler):
    def do_POST(self):
        open("/tmp/hook.log", "ab").write(self.rfile.read(int(self.headers["Content-Length"])) + b"\n")
        self.send_response(200); self.end_headers()
HTTPServer(("0.0.0.0", 8000), H).serve_forever()
PY
# EGRESS_REFRESH: the gateway fetches its policy every second instead of every 15 s
cat > compose.test.yaml <<'EOF'
services:
  claude: {image: "local/tentaclai:test", container_name: tctest-claude}
  egress: {image: "local/tentaclai-egress:test", container_name: tctest-egress, environment: {EGRESS_REFRESH: "1"}}
  control: {image: "local/tentacontrol:test", container_name: tctest-control, environment: {CLAUDE_CONTAINER: tctest-claude}, ports: !reset []}
  api: {image: "python:3.12-alpine", container_name: tctest-api, command: ["python", "/srv/api.py"], volumes: ["./fixtures/api.py:/srv/api.py:ro"], networks: [outside]}
  pg: {image: "postgres:16-alpine", container_name: tctest-pg, environment: {POSTGRES_PASSWORD: pg-secret-789}, networks: [outside]}
  ntfy: {image: "binwiederhier/ntfy:v2.11.0", container_name: tctest-ntfy, command: ["serve"], networks: [outside]}
  hook: {image: "python:3.12-alpine", container_name: tctest-hook, command: ["python", "/srv/hook.py"], volumes: ["./fixtures/hook.py:/srv/hook.py:ro"], networks: [outside]}
  sshd:
    image: lscr.io/linuxserver/openssh-server:latest
    container_name: tctest-sshd
    environment: {USER_NAME: deploy, PUBLIC_KEY_FILE: /keys/tentaclai_ed25519.pub, PASSWORD_ACCESS: "false"}
    volumes: ["./ssh/keys:/keys:ro"]
    networks: [outside]
EOF
echo '{"name":"testapi","api":{"base_url":"http://tctest-api:8000","auth":{"type":"bearer","token":"${API_TOKEN}"}}}' > services/testapi.json
echo '{"name":"testdb","db":{"engine":"postgres","host":"tctest-pg","database":"postgres","user":"postgres","password":"${PG_PASSWORD}","ssl":"disable","access":"ro"}}' > services/testdb.json
echo '{"name":"testdbrw","db":{"engine":"postgres","host":"tctest-pg","database":"postgres","user":"postgres","password":"${PG_PASSWORD}","ssl":"disable","access":"rw"}}' > services/testdbrw.json
printf 'Host testssh\n    HostName tctest-sshd\n    User deploy\n    Port 2222\n    # tentaclai: access=rw\n' > ssh/config.d/testssh.conf
[ -f ssh/keys/tentaclai_ed25519 ] || ssh-keygen -q -t ed25519 -N '' -C tctest -f ssh/keys/tentaclai_ed25519

section "build and start"
# rebuild an image only when its sources changed (hash of what its Dockerfile copies) or it is missing
src_hash() { find "$@" -type f ! -path '*/__pycache__/*' -print0 | sort -z | xargs -0 sha1sum | sha1sum | cut -c1-16; }
built=""
for svc in claude:build control:control egress:egress; do
    name=${svc%%:*}; dir=${svc#*:}
    img=$( [ "$name" = claude ] && echo tentaclai || { [ "$name" = control ] && echo tentacontrol || echo tentaclai-egress; } )
    h=$(src_hash "$dir" lib VERSION)
    if [ "$(cat ".img-$name" 2>/dev/null)" != "$h" ] || ! docker image inspect "local/$img:test" >/dev/null 2>&1; then
        docker compose build -q "$name"; echo "$h" > ".img-$name"; built="$built $name"
    fi
done
echo "   rebuilt:${built:- nothing}"
if docker ps -q -f name=tctest-control | grep -q .; then
    # reused stack: fresh tentaclAI state (password, rules, grants, channels, policy cache), helper containers stay up
    docker compose rm -sf claude egress control >/dev/null 2>&1
    docker volume rm -f tctest_egress-data >/dev/null
    docker run --rm -v "$T/data:/d" alpine:3.20 rm -rf /d/control >/dev/null
fi
docker compose up -d >/dev/null 2>&1
wait_for 180 sh -c "docker logs tctest-claude 2>&1 | grep -q 'CLAUDE.md updated'" || echo "   (sandbox start not seen in the log)"
wait_for 60 docker exec tctest-pg pg_isready -q
wait_for 120 docker exec tctest-sshd sh -c 'nc -z localhost 2222'  # sshd sets up slowly
wait_for 60 docker exec tctest-egress test -s /data/policy.json    # first policy of the gateway
C="docker exec -u claude tctest-claude"
CI="docker exec -i -u claude tctest-claude"
IP=$(docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}} {{end}}' tctest-control | awk '{print $1}')
J="$T/cookies"; H='HX-Request: true'
wait_for 60 sh -c "docker logs tctest-control 2>&1 | grep -q 'setup?token='"
TOK=$(docker logs tctest-control 2>&1 | grep -o 'setup?token=[A-Za-z0-9_-]*' | tail -1 | cut -d= -f2)
rm -f "$J"
curl -s -c "$J" -b "$J" -o /dev/null -X POST "http://$IP:8080/setup" -d "token=$TOK&password=testpass123&password2=testpass123"
curl -s -c "$J" -b "$J" -o /dev/null -X POST "http://$IP:8080/login" -d "password=testpass123"
pick() { curl -s -b "$J" "http://$IP:8080/partials/approvals" | grep -o "approvals/[0-9]*/$1" | tail -1 | grep -o '[0-9]*' | head -1; }
decide() { curl -s -b "$J" -H "$H" -X POST -o /dev/null "http://$IP:8080/approvals/$(pick "$1")/$1"; }
pending() { $C svc pending 2>/dev/null | grep -q "$1"; }                          # an approval for this destination waits
policy_has() { docker exec tctest-egress grep -q -- "$1" /data/policy.json; }    # the gateway got this rule

if want priv; then
section "privilege separation"
check ".env is root-only" "$(stat -c %a .env)" "600"
check "Claude can't read .env" "$($C cat "$T/.env" >/dev/null 2>&1 && echo yes || echo no)" "no"
check "Claude can't read the SSH key" "$($C cat /root/.ssh/keys/tentaclai_ed25519 >/dev/null 2>&1 && echo yes || echo no)" "no"
check "no secret in Claude's environment" "$($C env | grep -c 'secret' || true)" "0"
check "Claude can't write service files" "$($C sh -c "echo x > $T/services/x.json" 2>/dev/null && echo yes || echo no)" "no"
check "Claude can read the managed settings" "$($C cat /etc/claude-code/managed-settings.json >/dev/null 2>&1 && echo yes || echo no)" "yes"
check "Claude can't change the managed settings" "$($C sh -c 'echo {} > /etc/claude-code/managed-settings.json' 2>/dev/null && echo yes || echo no)" "no"
check "sandbox skills switched off" "$($C python3 -c 'import json,os;print(json.load(open(os.path.expanduser("~/.claude/settings.json")))["skillOverrides"]["anthropic-skills:pptx"])' 2>/dev/null)" "off"
check "sudo only for svc" "$($C sudo -n id >/dev/null 2>&1 && echo yes || echo no)" "no"
check "SQL shell refused on a ro database" "$(echo 'select 1;' | $CI svc db testdb shell >/dev/null 2>&1 && echo yes || echo no)" "no"
check "psql shell escape runs as tcrun" "$(echo '\! id -un' | $CI svc db testdbrw shell 2>/dev/null | tail -1)" "tcrun"
fi

if want svc; then
section "svc through the gateway"
check "API call with injected token" "$($C svc testapi GET / 2>/dev/null)" '{"auth_ok": true}'
check "database query" "$($C svc db testdb 'select 41+1' 2>/dev/null | sed -n 3p | tr -d ' ')" "42"
check "ssh (tentacle, rw)" "$($C svc ssh testssh echo ssh-ok 2>&1 | tail -1)" "ssh-ok"
check "plain ssh has no key" "$($C ssh -o BatchMode=yes testssh true >/dev/null 2>&1 && echo yes || echo no)" "no"
fi

if want egress; then
section "egress gateway"
check "no direct route out" "$($C curl -s -m 5 --noproxy '*' -o /dev/null -w '%{http_code}' https://example.com || true)" "000"
check "built-in list (github.com)" "$($C curl -s -m 15 -o /dev/null -w '%{http_code}' https://github.com || true)" "200"
( $C curl -s -m 70 -o /dev/null -w '%{http_code}' https://example.com > "$T/o.txt" 2>&1 || true ) &
wait_for 30 pending example.com:443 || true
check "unknown destination waits for approval" "$($C svc pending 2>/dev/null | grep -c 'example.com:443' || true)" "1"
decide net10; wait
check "Allow 10 min lets it through" "$(cat "$T/o.txt")" "200"
check "grant covers the next connection" "$($C curl -s -m 10 -o /dev/null -w '%{http_code}' https://example.com || true)" "200"
( $C curl -s -m 70 -o /dev/null -w '%{http_code}' https://example.org > "$T/o.txt" 2>&1 || true ) &
wait_for 30 pending example.org:443 || true
decide deny; wait
check "Deny refuses" "$(cat "$T/o.txt")" "000"
EX=$(docker exec tctest-egress python -c "import socket;print(socket.gethostbyname('example.net'))")
check "IP target without a rule is blocked" "$($C curl -s -m 10 -k -o /dev/null -w '%{http_code}' "https://$EX" || true)" "000"
curl -s -b "$J" -H "$H" -X POST -o /dev/null "http://$IP:8080/network/rule" -d kind=block -d "entry=${EX%.*}.0/24"
curl -s -b "$J" -H "$H" -X POST -o /dev/null "http://$IP:8080/network/rule" -d kind=allow -d "entry=example.net"
wait_for 30 sh -c "docker exec tctest-egress grep -q -- '${EX%.*}.0/24' /data/policy.json && docker exec tctest-egress grep -q example.net /data/policy.json" || true
check "allowed name in a blocked IP range is refused" "$($C curl -s -m 10 -o /dev/null -w '%{http_code}' https://example.net || true)" "000"
check "built-in can't be blocked" "$(curl -s -b "$J" -H "$H" -X POST -D - -o /dev/null "http://$IP:8080/network/rule" -d kind=block -d entry=github.com | grep -ci 'built-in' || true)" "1"
fi

if want notify; then
section "notifications"
TOPIC="tctest$(date +%s)"  # helper containers survive REUSE runs: own ntfy topic, empty webhook log
docker exec tctest-hook sh -c ': > /tmp/hook.log' 2>/dev/null || true
curl -s -b "$J" -H "$H" -o /dev/null -X POST "http://$IP:8080/notifications/channel" -d kind=ntfy -d name=phone -d server=http://tctest-ntfy -d topic=$TOPIC
curl -s -b "$J" -H "$H" -o /dev/null -X POST "http://$IP:8080/notifications/channel" -d kind=webhook -d name=hook -d url=http://tctest-hook:8000/n
curl -s -b "$J" -H "$H" -o /dev/null -X POST "http://$IP:8080/notifications/routes" -d r_approval=phone -d r_approval=hook -d r_blocked=hook -d dedupe=0
check "test button sends" "$(curl -s -b "$J" -H "$H" -X POST -D - -o /dev/null "http://$IP:8080/notifications/channel/phone/test" | grep -c 'Test sent' || true)" "1"
( $C curl -s -m 70 -o /dev/null https://example.edu || true ) &
wait_for 30 pending example.edu:443 || true
decide deny; wait
curl -s -b "$J" -H "$H" -o /dev/null -X POST "http://$IP:8080/network/rule" -d kind=block -d entry=example.com
wait_for 30 policy_has '"example.com"' || true
$C curl -s -m 10 -o /dev/null https://example.com || true
wait_for 30 docker exec tctest-hook grep -q 'example.com:443 blocked' /tmp/hook.log || true
NT=$(docker exec tctest-ntfy wget -qO- "http://localhost/$TOPIC/json?poll=1" 2>/dev/null || true)
HK=$(docker exec tctest-hook cat /tmp/hook.log 2>/dev/null || true)
check "ntfy got the test message" "$(echo "$NT" | grep -c 'tentaclAI test' || true)" "1"
check "ntfy got 'Approval needed'" "$(echo "$NT" | grep -c 'Approval needed: example.edu' || true)" "1"
check "webhook got 'Approval needed'" "$(echo "$HK" | grep -c 'Approval needed: example.edu' || true)" "1"
check "webhook got the blocked destination" "$(echo "$HK" | grep -c 'example.com:443 blocked' || true)" "1"
fi

if want skills; then
section "skills"
SK() { $C python3 -c 'import json,os,sys;print(json.load(open(os.path.expanduser("~/.claude/settings.json"))).get("skillOverrides",{}).get(sys.argv[1],"on"))' "$1" 2>/dev/null; }
curl -s -b "$J" -H "$H" -o /dev/null -X POST "http://$IP:8080/skills/mode" -d name=dataviz -d mode=on
check "default-off skill switched on" "$(SK dataviz)" "on"
curl -s -b "$J" -H "$H" -o /dev/null -X POST "http://$IP:8080/skills/mode" -d name=dataviz -d mode=user-invocable-only
check "skill as /command only" "$(SK dataviz)" "user-invocable-only"
curl -s -b "$J" -H "$H" -o /dev/null -X POST "http://$IP:8080/skills/mode" -d name=dataviz -d mode=bogus
check "unknown mode refused" "$(SK dataviz)" "user-invocable-only"
check "other defaults stay off" "$(SK anthropic-skills:pptx)" "off"
fi

if want pages; then
section "tentacontrol pages render"
for p in / /network "/network?tab=allow" "/network?tab=block" /services /ssh /activity /terminal /topology /help /settings /notifications /skills /usage /logs; do
    check "GET $p" "$(curl -s -b "$J" -o /dev/null -w '%{http_code}' "http://$IP:8080$p")" "200"
done
fi
section "done"

echo; echo "passed: $PASS  failed: $FAIL"
[ "$FAIL" -eq 0 ]
