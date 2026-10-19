#!/usr/bin/env bash
# End-to-end tests of the security boundaries – in a scratch copy with its own compose project (tctest), never on the
# running installation. Needs Docker and internet (example.com, github.com). Takes a few minutes (image builds).
#   scripts/test-sandbox.sh            build, test, clean up
#   KEEP=1 scripts/test-sandbox.sh     keep the test stack running afterwards (docker compose -p tctest down -v)
set -euo pipefail
SRC="$(cd "$(dirname "$0")/.." && pwd)"
T="$(mktemp -d /tmp/tctest.XXXXXX)"
export COMPOSE_PROJECT_NAME=tctest
PASS=0 FAIL=0
ok() { PASS=$((PASS + 1)); printf '  \033[32m✓\033[0m %s\n' "$1"; }
bad() { FAIL=$((FAIL + 1)); printf '  \033[31m✗\033[0m %s\n' "$1"; }
check() { if [ "$2" = "$3" ]; then ok "$1"; else bad "$1 (expected '$3', got '$2')"; fi; }
cleanup() {
    [ -n "${KEEP:-}" ] && { echo "stack kept: cd $T && docker compose down -v"; return; }
    (cd "$T" && docker compose down -v >/dev/null 2>&1) || true
    docker image rm local/tentaclai:test local/tentacontrol:test local/tentaclai-egress:test >/dev/null 2>&1 || true
    rm -rf "$T"
}
trap cleanup EXIT

echo "== setup in $T"
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
cat > compose.test.yaml <<'EOF'
services:
  claude: {image: "local/tentaclai:test", container_name: tctest-claude}
  egress: {image: "local/tentaclai-egress:test", container_name: tctest-egress}
  control: {image: "local/tentacontrol:test", container_name: tctest-control, environment: {CLAUDE_CONTAINER: tctest-claude}, ports: !reset []}
  api: {image: "python:3.12-alpine", container_name: tctest-api, command: ["python", "/srv/api.py"], volumes: ["./fixtures/api.py:/srv/api.py:ro"], networks: [outside]}
  pg: {image: "postgres:16-alpine", container_name: tctest-pg, environment: {POSTGRES_PASSWORD: pg-secret-789}, networks: [outside]}
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
ssh-keygen -q -t ed25519 -N '' -C tctest -f ssh/keys/tentaclai_ed25519

echo "== build and start"
docker compose build -q
docker compose up -d >/dev/null 2>&1
for _ in $(seq 1 90); do docker logs tctest-claude 2>&1 | grep -q 'CLAUDE.md updated' && break; sleep 2; done
until docker exec tctest-pg pg_isready -q 2>/dev/null; do sleep 1; done
sleep 16  # first policy refresh of the gateway
C="docker exec -u claude tctest-claude"
CI="docker exec -i -u claude tctest-claude"
IP=$(docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}} {{end}}' tctest-control | awk '{print $1}')
J="$T/cookies"; H='HX-Request: true'
TOK=$(docker logs tctest-control 2>&1 | grep -o 'setup?token=[A-Za-z0-9_-]*' | tail -1 | cut -d= -f2)
curl -s -c "$J" -b "$J" -o /dev/null -X POST "http://$IP:8080/setup" -d "token=$TOK&password=testpass123&password2=testpass123"
curl -s -c "$J" -b "$J" -o /dev/null -X POST "http://$IP:8080/login" -d "password=testpass123"
pick() { curl -s -b "$J" "http://$IP:8080/partials/approvals" | grep -o "approvals/[0-9]*/$1" | tail -1 | grep -o '[0-9]*' | head -1; }
decide() { curl -s -b "$J" -H "$H" -X POST -o /dev/null "http://$IP:8080/approvals/$(pick "$1")/$1"; }

echo "== privilege separation"
check ".env is root-only" "$(stat -c %a .env)" "600"
check "Claude can't read .env" "$($C cat "$T/.env" >/dev/null 2>&1 && echo yes || echo no)" "no"
check "Claude can't read the SSH key" "$($C cat /root/.ssh/keys/tentaclai_ed25519 >/dev/null 2>&1 && echo yes || echo no)" "no"
check "no secret in Claude's environment" "$($C env | grep -c 'secret' || true)" "0"
check "Claude can't write service files" "$($C sh -c "echo x > $T/services/x.json" 2>/dev/null && echo yes || echo no)" "no"
check "sudo only for svc" "$($C sudo -n id >/dev/null 2>&1 && echo yes || echo no)" "no"
check "SQL shell refused on a ro database" "$(echo 'select 1;' | $CI svc db testdb shell >/dev/null 2>&1 && echo yes || echo no)" "no"
check "psql shell escape runs as tcrun" "$(echo '\! id -un' | $CI svc db testdbrw shell 2>/dev/null | tail -1)" "tcrun"

echo "== svc through the gateway"
check "API call with injected token" "$($C svc testapi GET / 2>/dev/null)" '{"auth_ok": true}'
check "database query" "$($C svc db testdb 'select 41+1' 2>/dev/null | sed -n 3p | tr -d ' ')" "42"
check "ssh (tentacle, rw)" "$($C svc ssh testssh echo ssh-ok 2>/dev/null | tail -1)" "ssh-ok"
check "plain ssh has no key" "$($C ssh -o BatchMode=yes testssh true >/dev/null 2>&1 && echo yes || echo no)" "no"

echo "== egress gateway"
check "no direct route out" "$($C curl -s -m 5 --noproxy '*' -o /dev/null -w '%{http_code}' https://example.com || true)" "000"
check "built-in list (github.com)" "$($C curl -s -m 15 -o /dev/null -w '%{http_code}' https://github.com || true)" "200"
( $C curl -s -m 70 -o /dev/null -w '%{http_code}' https://example.com > "$T/o.txt" 2>&1 || true ) & sleep 4
check "unknown destination waits for approval" "$($C svc pending 2>/dev/null | grep -c 'example.com:443' || true)" "1"
decide net10; wait
check "Allow 10 min lets it through" "$(cat "$T/o.txt")" "200"
check "grant covers the next connection" "$($C curl -s -m 10 -o /dev/null -w '%{http_code}' https://example.com || true)" "200"
( $C curl -s -m 70 -o /dev/null -w '%{http_code}' https://example.org > "$T/o.txt" 2>&1 || true ) & sleep 4
decide deny; wait
check "Deny refuses" "$(cat "$T/o.txt")" "000"
EX=$(docker exec tctest-egress python -c "import socket;print(socket.gethostbyname('example.net'))")
check "IP target without a rule is blocked" "$($C curl -s -m 10 -k -o /dev/null -w '%{http_code}' "https://$EX" || true)" "000"
curl -s -b "$J" -H "$H" -X POST -o /dev/null "http://$IP:8080/network/rule" -d kind=block -d "entry=${EX%.*}.0/24"
curl -s -b "$J" -H "$H" -X POST -o /dev/null "http://$IP:8080/network/rule" -d kind=allow -d "entry=example.net"
sleep 16
check "allowed name in a blocked IP range is refused" "$($C curl -s -m 10 -o /dev/null -w '%{http_code}' https://example.net || true)" "000"
check "built-in can't be blocked" "$(curl -s -b "$J" -H "$H" -X POST -D - -o /dev/null "http://$IP:8080/network/rule" -d kind=block -d entry=github.com | grep -ci 'built-in' || true)" "1"

echo "== tentacontrol pages render"
for p in / /network "/network?tab=allow" "/network?tab=block" /services /ssh /activity /terminal /topology /help /settings /logs; do
    check "GET $p" "$(curl -s -b "$J" -o /dev/null -w '%{http_code}' "http://$IP:8080$p")" "200"
done

echo; echo "passed: $PASS  failed: $FAIL"
[ "$FAIL" -eq 0 ]
