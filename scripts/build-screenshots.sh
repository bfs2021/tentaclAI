#!/usr/bin/env bash
# Rebuild docs/screenshots/{topology,services}.png from a made-up demo site (no real hosts, names or tokens).
# Uses the local tentacontrol image (docker compose build control first) and the Playwright container.
set -euo pipefail
cd "$(dirname "$0")/.."
TC="$PWD"
S="$(mktemp -d)"; D=$S/stack
trap 'docker rm -f tc-demo >/dev/null 2>&1 || true; rm -rf "$S"' EXIT
mkdir -p "$D" "$S/data" "$S/out"
git ls-files | rsync -a --files-from=- ./ "$D/"
printf 'TC_DIR=/demo\nTC_DOMAIN=home.lan\nTC_HOSTNAME=homeserver\nHA_URL=http://homeassistant.home.lan:8123\nHA_TOKEN=demo\nGRAFANA_TOKEN=demo\nGH_TOKEN=demo\nWIKI_USER=demo\nWIKI_PASSWORD=demo\nAPP_DB_USER=demo\nAPP_DB_PASSWORD=demo\nN8N_MCP_TOKEN=demo\n' > $D/.env
mkdir -p $D/systems $D/ssh/config.d
j() { echo "$2" > $D/$1; }
j systems/homeserver.json '{"name":"homeserver","description":"Home server – Docker host of the homelab","ssh_host":"homeserver","notes":["Ask and back up before restarts, updates and deletions."]}'
j systems/vps.json '{"name":"vps","description":"VPS – reverse proxy and mail","ssh_host":"vps","notes":["Restarting the proxy interrupts all sites – ask first."]}'
j systems/nas.json '{"name":"nas","description":"NAS – storage and backups","ssh_host":"nas","notes":[]}'
j services/homeserver.json '{"name":"homeserver","description":"Docker host homeserver – homelab stacks","system":"homeserver","docker":{"mode":"ssh","ssh_host":"homeserver","stacks_dir":"/srv/stacks","access":"write"}}'
j services/grafana.json '{"name":"grafana","description":"Grafana – dashboards, data sources, alerts","system":"homeserver","api":{"base_url":"https://grafana.home.lan/api","auth":{"type":"bearer","token":"${GRAFANA_TOKEN}"}}}'
j services/paperless.json '{"name":"paperless","description":"Paperless-ngx – documents, tags, correspondents","system":"homeserver","api":{"base_url":"https://paperless.home.lan/api","auth":{"type":"header","header":"Authorization","value":"Token ${PAPERLESS_TOKEN}"}}}'
j services/appdb.json '{"name":"appdb","description":"PostgreSQL of the apps (read only)","system":"homeserver","db":{"engine":"postgres","host":"db.home.lan","database":"app","user":"${APP_DB_USER}","password":"${APP_DB_PASSWORD}","access":"read"}}'
j services/vps.json '{"name":"vps","description":"Docker host vps – proxy, mail","system":"vps","docker":{"mode":"ssh","ssh_host":"vps","stacks_dir":"/opt/stacks","access":"read"}}'
j services/nas-config.json '{"name":"nas-config","description":"Config folder on the NAS","system":"nas","files":{"ssh_host":"nas","path":"/volume1/config","access":"read"}}'
j services/homeassistant.json '{"name":"homeassistant","description":"Home Assistant – states, history, automations","api":{"base_url":"${HA_URL}/api","auth":{"type":"bearer","token":"${HA_TOKEN}"}},"mcp":{"type":"http","url":"${HA_URL}/api/mcp","headers":{"Authorization":"Bearer ${HA_TOKEN}"}}}'
j services/wiki.json '{"name":"wiki","description":"Team wiki (form login)","web":{"base_url":"https://wiki.home.lan","auth":{"type":"form","login_url":"/login","fields":{"username":"${WIKI_USER}","password":"${WIKI_PASSWORD}"}}}}'
j services/github.json '{"name":"github","description":"GitHub – repos, issues, PRs","api":{"base_url":"https://api.github.com","auth":{"type":"bearer","token":"${GH_TOKEN}"}},"mcp":{"type":"http","url":"https://api.githubcopilot.com/mcp/","headers":{"Authorization":"Bearer ${GH_TOKEN}"}}}'
j services/n8n.json '{"name":"n8n","description":"n8n – build and trigger workflows","mcp":{"type":"http","url":"https://n8n.home.lan/mcp-server/http","headers":{"Authorization":"Bearer ${N8N_MCP_TOKEN}"}}}'
printf 'Host homeserver\n    HostName homeserver.home.lan\n    User admin\n    # tentaclai: sudo=sudo -n\nHost vps\n    HostName vps.example.org\n    User root\nHost nas\n    HostName nas.home.lan\n    User claude\n' > $D/ssh/config.d/demo.conf
docker pull -q mcr.microsoft.com/playwright:v1.55.0-noble >/dev/null 2>&1
docker run -d --rm --name tc-demo -e STACK_DIR=/demo -e DATA_DIR=/data -e CLAUDE_CONTAINER=tc-demo-none -v /var/run/docker.sock:/var/run/docker.sock:ro -v $D:/demo -v $S/data:/data local/tentacontrol:latest >/dev/null; sleep 4
IP=$(docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' tc-demo); T=$(docker logs tc-demo 2>&1 | grep -o 'setup?token=[A-Za-z0-9_-]*' | tail -1 | cut -d= -f2)
cat > $S/out/t.mjs <<EOF
import { chromium } from 'playwright';
const b = await chromium.launch(); const p = await b.newPage({ viewport: { width: 1440, height: 860 }, deviceScaleFactor: 2, colorScheme: 'dark' });
const B='http://$IP:8080'; const errs=[]; p.on('pageerror', e=>errs.push(e.message));
const fake = async () => p.evaluate(()=>{ const c=document.querySelector('.cstate'); if(c) c.innerHTML='<span class="dot ok"></span>tentaclAI running · 3 d'; });
await p.goto(B+'/setup?token=$T'); await p.fill('#un','demo'); await p.fill('#em','demo@example.org'); await p.fill('#pw','demodemo1234'); await p.fill('#pw2','demodemo1234'); await p.click('button'); await p.waitForTimeout(800);
if ((await p.url()).includes('login')) { await p.fill('input[type=password]','demodemo1234'); await p.click('button'); await p.waitForTimeout(800); }
await p.goto(B+'/topology'); await p.waitForTimeout(3500); await fake();
for (let i = 0; i < 15; i++) {
  await p.click('#topo-reset'); await p.waitForTimeout(250);
  await p.evaluate(()=>{ const cy=document.getElementById('cy')._cyreg.cy; cy.fit(undefined, 70);
    const z=cy.zoom(); if (!window._best || z > window._best.z) window._best={z, pos: cy.nodes().not('[kind="system"]').map(n=>[n.id(), {...n.position()}])}; });
}
await p.evaluate(()=>{ const cy=document.getElementById('cy')._cyreg.cy; window._best.pos.forEach(([id,q])=>cy.\$id(id).position(q));
  cy.nodes('[kind="ssh"]').forEach(n=>n.data('live','ok')); cy.\$id('svc:homeserver').select(); cy.\$id('svc:homeserver').emit('tap'); cy.fit(undefined, 70);
  document.getElementById('topo-msg').textContent=''; document.activeElement && document.activeElement.blur(); });
await p.waitForTimeout(600); await p.screenshot({path:'/out/topology.png'});
await p.goto(B+'/services'); await p.waitForTimeout(800); await fake(); await p.screenshot({path:'/out/services.png'});
console.log('errors', JSON.stringify(errs)); await b.close();
EOF
docker run --rm -v $S/out:/out -w /out mcr.microsoft.com/playwright:v1.55.0-noble bash -c 'npm i -s playwright@1.55.0 >/dev/null 2>&1; node t.mjs' 2>&1 | tail -1;

cp "$S/out/topology.png" "$S/out/services.png" "$TC/docs/screenshots/"
echo "→ docs/screenshots/topology.png, services.png"
