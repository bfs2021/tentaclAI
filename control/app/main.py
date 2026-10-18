"""tentacontrol – web admin for the tentaclai sandbox + service gateway for Claude Code."""
import asyncio
import html
import json
import os
import time

import segno
from fastapi import FastAPI, Form, Request, WebSocket
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from . import auth, dockerops, jobs, services_store, sshhosts, sshops, systems_store, topology
from .config import CLAUDE_SERVICE, COMPOSE, DATA_DIR, RC_VARS, RESTART_VARS, SETTINGS_GROUPS, is_secret, site
from .envfile import read_env, update_env
from .terminal import PROFILES, bridge, onboard_cmd, ssh_add_cmd, ssh_remove_cmd
from . import generated

BASE = os.path.dirname(__file__)
app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
templates = Jinja2Templates(directory=os.path.join(BASE, "templates"))
# cache busting: static URLs carry the newest mtime of app.css/app.js, so browsers load a new version right after an update
templates.env.globals["asset_v"] = str(int(max(os.path.getmtime(os.path.join(BASE, "static", f)) for f in ("app.css", "app.js", "logo.svg"))))
app.mount("/static", StaticFiles(directory=os.path.join(BASE, "static")), name="static")
app.mount("/vendor", StaticFiles(directory="/app/vendor"), name="vendor")

PUBLIC = ("/login", "/setup", "/static/", "/vendor/", "/healthz")
RESTART_FLAG = os.path.join(DATA_DIR, "restart-needed")


@app.middleware("http")
async def guard(request: Request, call_next):
    path = request.url.path
    if not path.startswith(PUBLIC):
        if not auth.logged_in(request):
            if request.headers.get("hx-request"):
                return PlainTextResponse("", headers={"HX-Redirect": "/login"})
            return RedirectResponse("/login" if auth.has_password() else "/setup", status_code=303)
        if request.method == "POST" and not auth.same_origin(request):
            return PlainTextResponse("invalid origin", status_code=403)
    response = await call_next(request)
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    return response


app.add_middleware(SessionMiddleware, secret_key=auth.session_secret(), session_cookie="tentacontrol",
                   max_age=12 * 3600, same_site="strict", https_only=False)


@app.middleware("http")
async def secure_cookie(request: Request, call_next):
    """mark the session cookie Secure whenever the site is reached via https (directly or through a proxy)"""
    response = await call_next(request)
    if request.url.scheme == "https":
        raw = [(k, v) for k, v in response.raw_headers]
        response.raw_headers[:] = [(k, v + b"; secure" if k == b"set-cookie" and v.startswith(b"tentacontrol=")
                                    and b"secure" not in v.lower() else v) for k, v in raw]
    return response


@app.on_event("startup")
async def announce_setup():
    if not auth.has_password():
        print("=" * 72, flush=True)
        print("tentacontrol: no password set yet. One-time setup link:", flush=True)
        print(f"  https://<host>/setup?token={auth.SETUP_TOKEN}", flush=True)
        print("  (or: ./tentaclai.sh passwd)", flush=True)
        print("=" * 72, flush=True)


def page(request, name, **ctx):
    st = dockerops.status()
    ctx.update(request=request, nav=name.split(".")[0], cstatus=st, restart_needed=restart_needed(st), site=site(),
               flash=request.session.pop("flash", None))
    return templates.TemplateResponse(request, name, ctx)


def _baseline(st):
    """RESTART_VARS values the running container was created with (recorded on the first change after creation)"""
    try:
        if os.path.getmtime(RESTART_FLAG) <= st.get("created_ts", 0):
            return {}  # container was recreated since → flag is stale
        with open(RESTART_FLAG) as f:
            data = json.load(f)
        return data.get("baseline", {}) if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _norm(path):
    return path[4:] if path.startswith("/var/run/") else path


def _mounts_differ(st):
    """compose.generated.yaml (docker services, mode socket) vs. the mounts the container actually has"""
    try:
        want = {(_norm(s), d) for s, d in (m.rsplit(":", 1) for m in generated.mounts(services_store.STACK_DIR))}
    except OSError:
        return False
    stack_dir = services_store.STACK_DIR
    have = {(_norm(s), d) for s, d in st.get("mounts", [])
            if d.startswith("/run/tentaclai/docker-") or (s == d and d != stack_dir)}
    return want != have


def restart_needed(st=None):
    st = st or dockerops.status()
    if not st.get("exists"):
        return False
    env = read_env()
    if any(env.get(k, "") != v for k, v in _baseline(st).items()):
        return True
    return _mounts_differ(st)


def mark_restart(old_env, changed):
    """remember the pre-change values, so reverting a setting clears the hint again"""
    st = dockerops.status()
    base = _baseline(st)
    for k in RESTART_VARS & set(changed):
        base.setdefault(k, old_env.get(k, ""))
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(RESTART_FLAG, "w") as f:
        json.dump({"ts": time.time(), "baseline": base}, f)


def toast(msg, kind="ok"):
    return {"HX-Trigger": json.dumps({"toast": {"msg": msg, "kind": kind}})}


def respond(request, msg, kind="ok", refresh=False, redirect=None):
    """htmx answer: toast now, or flash message that survives a reload/redirect"""
    if refresh or redirect:
        request.session["flash"] = {"msg": msg, "kind": kind}
        return PlainTextResponse("", headers={"HX-Redirect": redirect} if redirect else {"HX-Refresh": "true"})
    return PlainTextResponse("", headers=toast(msg, kind))


def client_ip(request):
    return request.client.host if request.client else "?"


# ---------------------------------------------------------------- auth

@app.get("/healthz")
async def healthz():
    return {"ok": True}


@app.get("/setup", response_class=HTMLResponse)
async def setup_form(request: Request, token: str = ""):
    if auth.has_password():
        return RedirectResponse("/login", status_code=303)
    return templates.TemplateResponse(request, "setup.html", {"token": token, "error": None})


@app.post("/setup")
async def setup_save(request: Request, token: str = Form(""), password: str = Form(...), password2: str = Form(...)):
    if auth.has_password():
        return RedirectResponse("/login", status_code=303)
    error = None
    if token != auth.SETUP_TOKEN:
        error = "invalid setup token – see ./tentaclai.sh setup-url"
    elif len(password) < 10:
        error = "at least 10 characters"
    elif password != password2:
        error = "passwords do not match"
    if error:
        return templates.TemplateResponse(request, "setup.html", {"token": token, "error": error}, status_code=400)
    auth.set_password(password)
    request.session["auth"] = True
    return RedirectResponse("/", status_code=303)


@app.get("/login", response_class=HTMLResponse)
async def login_form(request: Request):
    if not auth.has_password():
        return RedirectResponse("/setup", status_code=303)
    return templates.TemplateResponse(request, "login.html", {"error": None})


@app.post("/login")
async def login(request: Request, password: str = Form(...)):
    ip = client_ip(request)
    wait = auth.throttled(ip)
    if wait:
        return templates.TemplateResponse(request, "login.html", {"error": f"too many attempts – please wait {wait} s"}, status_code=429)
    if not auth.check_password(password):
        auth.register_failure(ip)
        await asyncio.sleep(1)
        return templates.TemplateResponse(request, "login.html", {"error": "wrong password"}, status_code=401)
    auth.clear_failures(ip)
    request.session.clear()
    request.session["auth"] = True
    return RedirectResponse("/", status_code=303)


@app.post("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


# ---------------------------------------------------------------- dashboard

@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    return page(request, "dashboard.html")


@app.get("/partials/overview", response_class=HTMLResponse)
async def overview(request: Request):
    st = dockerops.status()
    ctx = {"st": st, "stats": None, "version": None, "logged": dockerops.logged_in_claude(),
           "sessions": [], "rc": None, "services": services_store.load_all(), "hosts": sshhosts.hosts()}
    if st["running"]:
        ctx.update(stats=dockerops.stats(), version=dockerops.claude_version(),
                   sessions=dockerops.tmux_sessions(), rc=dockerops.rc_info())
    if ctx["rc"] and ctx["rc"]["url"]:
        ctx["rc"]["qr"] = segno.make(ctx["rc"]["url"], error="m").svg_inline(scale=4, border=2, dark="#111", light="#fff", omitsize=True)
    return templates.TemplateResponse(request, "partials/overview.html", ctx)


@app.post("/container/{action}")
async def container_action(request: Request, action: str):
    if action == "create":
        try:
            job = jobs.start("Create & start tentaclai", [COMPOSE + ["up", "-d", "--no-deps", CLAUDE_SERVICE]])
        except RuntimeError as e:
            return PlainTextResponse("", headers=toast(str(e), "err"))
        return PlainTextResponse("", headers={"HX-Redirect": f"/jobs/{job.id}"})
    if action == "recreate":
        try:
            job = jobs.start("Recreate tentaclai (apply configuration)",
                             [COMPOSE + ["up", "-d", "--no-deps", "--force-recreate", CLAUDE_SERVICE]])
        except RuntimeError as e:
            return PlainTextResponse("", headers=toast(str(e), "err"))
        return PlainTextResponse("", headers={"HX-Redirect": f"/jobs/{job.id}"})
    if action not in ("start", "stop", "restart"):
        return PlainTextResponse("unknown action", status_code=400)
    ok, msg = await asyncio.to_thread(dockerops.lifecycle, action)
    label = {"start": "started", "stop": "stopped", "restart": "restarted"}[action]
    return respond(request, f"tentaclai {label}" if ok else msg, "ok" if ok else "err", refresh=True)


@app.post("/rc/toggle")
async def rc_toggle(request: Request):
    """enable/disable remote control (RC_ENABLED) and restart rc-loop so it applies immediately"""
    enable = (read_env().get("RC_ENABLED", "true") or "true").lower() != "true"
    update_env({"RC_ENABLED": "true" if enable else "false"})
    await asyncio.to_thread(dockerops.rc_restart)
    return respond(request, "Remote control enabled" if enable else "Remote control disabled", refresh=True)


@app.post("/rc/restart")
async def rc_restart():
    code, out = await asyncio.to_thread(dockerops.rc_restart)
    return PlainTextResponse("", headers=toast("Remote control restarted" if code == 0 else f"Error: {out[:120]}",
                                               "ok" if code == 0 else "err"))


# ---------------------------------------------------------------- terminal

@app.get("/terminal", response_class=HTMLResponse)
async def terminal_page(request: Request, p: str = "claude", alias: str = "", target: str = "", sudo: str = ""):
    hosts = docker_dirs()
    if p == "sshadd":
        title = f"Add SSH host: {alias}"
    elif p == "sshremove":
        title = f"Remove SSH host: {alias}"
    elif p == "onboard":
        title = f"Onboard service: {alias}"
    elif p == "host":
        title = f"Claude · {hosts.get(alias, alias)}"
    else:
        title = PROFILES.get(p, PROFILES["claude"])["title"]
    return page(request, "terminal.html", profiles=PROFILES, hosts=hosts, current=p, title=title,
                params={"alias": alias, "target": target, "sudo": sudo},
                reattach=p in ("claude", "rc", "inventory", "onboard", "host"))  # tmux-based: safe to reconnect automatically


def docker_dirs():
    """local docker services with a stacks dir -> {name: dir} (terminal tabs)"""
    out = {}
    for s in services_store.load_all():
        d = s["data"].get("docker") or {}
        if s["state"] == "ok" and d.get("mode", "socket") == "socket" and d.get("stacks_dir"):
            out[s["name"]] = d["stacks_dir"]
    return out


@app.websocket("/ws/terminal")
async def ws_terminal(ws: WebSocket, p: str = "claude", alias: str = "", target: str = "", sudo: str = ""):
    if not ws.session.get("auth") or not auth.same_origin(ws):
        await ws.close(code=4401)
        return
    await ws.accept()
    st = dockerops.status()
    if not st["running"]:
        await ws.send_text(json.dumps({"type": "error", "msg": "tentaclAI is not running – start it on the overview first."}))
        await ws.close()
        return
    try:
        if p == "sshadd":
            cmd = ssh_add_cmd(alias, target, sudo)
        elif p == "sshremove":
            cmd = ssh_remove_cmd(alias)
        elif p == "onboard":
            cmd = onboard_cmd(alias)
        elif p == "host":
            d = docker_dirs().get(alias)
            if not d:
                raise ValueError("no local docker service with this name")
            cmd = ["cc", d]
        else:
            cmd = PROFILES.get(p, PROFILES["claude"])["cmd"]
    except ValueError as e:
        await ws.send_text(json.dumps({"type": "error", "msg": str(e)}))
        await ws.close()
        return
    await bridge(ws, cmd)


# ---------------------------------------------------------------- services

@app.get("/services", response_class=HTMLResponse)
async def services_page(request: Request):
    systems = systems_store.load_all()
    return page(request, "services.html", services=services_store.load_all(), templates_list=services_store.list_templates(),
                systems=systems, system_names={x["name"] for x in systems})


# ---------------------------------------------------------------- topology (prototype)

@app.get("/topology", response_class=HTMLResponse)
async def topology_page(request: Request):
    return page(request, "topology.html")


@app.get("/topology.json")
async def topology_json():
    return JSONResponse(await asyncio.to_thread(topology.graph))


@app.get("/topology/ssh-status")
async def topology_ssh_status():
    aliases = [h["alias"] for h in sshhosts.hosts()]
    res = await asyncio.gather(*(ssh_state(a) for a in aliases))
    return JSONResponse({a: {"state": s, "title": t} for a, (s, t) in zip(aliases, res)})


LAYOUT_FILE = os.path.join(DATA_DIR, "topology-layout.json")


@app.get("/topology/layout")
async def topology_layout_get():
    try:
        with open(LAYOUT_FILE) as f:
            return JSONResponse(json.load(f))
    except (OSError, ValueError):
        return JSONResponse({})


@app.post("/topology/layout")
async def topology_layout_save(request: Request):
    """node positions after the user moved nodes: {id: {x, y}}"""
    try:
        data = await request.json()
        pos = {str(k)[:300]: {"x": float(v["x"]), "y": float(v["y"])} for k, v in data.items()}
    except (ValueError, TypeError, KeyError, AttributeError):
        return JSONResponse({"error": "invalid layout"}, status_code=400)
    if len(pos) > 5000:
        return JSONResponse({"error": "too many nodes"}, status_code=400)
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(LAYOUT_FILE + ".tmp", "w") as f:
        json.dump(pos, f)
    os.replace(LAYOUT_FILE + ".tmp", LAYOUT_FILE)
    return JSONResponse({"saved": len(pos)})


@app.delete("/topology/layout")
async def topology_layout_reset():
    try:
        os.remove(LAYOUT_FILE)
    except FileNotFoundError:
        pass
    return JSONResponse({"reset": True})


@app.get("/topology/docker/{name}")
async def topology_docker(name: str):
    if not services_store.NAME.match(name):
        return JSONResponse({"error": "invalid name"}, status_code=400)
    if not dockerops.status()["running"]:
        return JSONResponse({"error": "tentaclAI is not running"}, status_code=409)
    try:
        return JSONResponse({"stacks": await asyncio.to_thread(topology.docker_stacks, name)})
    except RuntimeError as e:
        return JSONResponse({"error": str(e)}, status_code=502)


# ---------------------------------------------------------------- systems (groups of services)

@app.get("/systems", response_class=HTMLResponse)
async def systems_page(request: Request):
    systems = systems_store.load_all()
    services = services_store.load_all()
    names = {x["name"] for x in systems}
    members = {}
    for sv in services:
        if sv["system"] in names:
            members.setdefault(sv["system"], []).append(sv)
    return page(request, "systems.html", systems=systems, members=members,
                standalone=[sv for sv in services if sv["system"] not in names])


@app.post("/systems/new")
async def systems_new(request: Request, name: str = Form(...)):
    name = name.strip().lower()
    try:
        systems_store.create(name)
    except ValueError as e:
        return PlainTextResponse("", headers=toast(str(e), "err"))
    return respond(request, f"System {name} created – add description, SSH host and services", redirect=f"/systems/{name}")


@app.get("/systems/{name}", response_class=HTMLResponse)
async def system_edit(request: Request, name: str):
    try:
        sysd = systems_store.get(name)
    except (FileNotFoundError, ValueError):
        return RedirectResponse("/services", status_code=303)
    sysd.setdefault("description", ""); sysd.setdefault("ssh_host", ""); sysd.setdefault("notes", [])
    return page(request, "system_edit.html", sysd=sysd, services=services_store.load_all(), hosts=sshhosts.hosts(), nav="services")


@app.post("/systems/{name}")
async def system_save(request: Request, name: str):
    form = await request.form()
    try:
        systems_store.save(name, form.get("description", ""), form.get("ssh_host", ""),
                           form.get("notes", "").splitlines(), form.getlist("members"))
    except (ValueError, OSError) as e:
        return PlainTextResponse("", headers=toast(str(e), "err"))
    await sync_services()
    return respond(request, f"System {name} saved", refresh=True)


@app.post("/systems/{name}/delete")
async def system_delete(request: Request, name: str):
    try:
        systems_store.delete(name)
    except (ValueError, OSError) as e:
        return PlainTextResponse("", headers=toast(str(e), "err"))
    await sync_services()
    return respond(request, f"System {name} deleted – its services are standalone now", redirect="/systems")


@app.post("/services/new")
async def services_new(request: Request, name: str = Form(...), template: str = Form("")):
    name = name.strip().lower()
    try:
        services_store.create(name, template.strip())
    except ValueError as e:
        return PlainTextResponse("", headers=toast(str(e), "err"))
    await sync_services()
    return respond(request, f"{name} created – check URL/tokens, then “Test connection” and “Onboard with Claude”",
                   redirect=f"/services/{name}")


@app.get("/services/{name}", response_class=HTMLResponse)
async def service_edit(request: Request, name: str):
    try:
        raw = services_store.read_raw(name)
    except (FileNotFoundError, ValueError):
        return RedirectResponse("/services", status_code=303)
    try:
        data = json.loads(raw)
        err = None
    except ValueError as e:
        data, err = {}, str(e)
    svc = services_store.describe(name, data, error=err)
    return page(request, "service_edit.html", svc=svc, raw=raw, systems=systems_store.load_all())


@app.post("/services/{name}/system")
async def service_set_system(request: Request, name: str, system: str = Form("")):
    system = system.strip()
    if system and system not in {x["name"] for x in systems_store.load_all()}:
        return PlainTextResponse("", headers=toast("unknown system", "err"))
    try:
        systems_store.set_service_system(name, system)
    except (ValueError, OSError) as e:
        return PlainTextResponse("", headers=toast(str(e), "err"))
    await sync_services()
    return respond(request, f"{name} → {system or 'standalone'}", refresh=True)


@app.post("/services/{name}/json")
async def service_save_json(request: Request, name: str, raw: str = Form(...)):
    try:
        services_store.save_raw(name, raw)
    except ValueError as e:
        return PlainTextResponse("", headers=toast(f"Not saved: {e}", "err"))
    await sync_services()
    return respond(request, "Saved & synced", refresh=True)


@app.post("/services/{name}/tokens")
async def service_save_tokens(request: Request, name: str):
    form = await request.form()
    env = read_env()
    changes = {}
    for key, value in form.items():
        if key.startswith("var_"):
            k, value = key[4:], value.strip()
            if is_secret(k):
                if value:
                    changes[k] = value
            elif env.get(k, "") != value:
                changes[k] = value
        elif key.startswith("clear_"):
            changes[key[6:]] = ""
    if not changes:
        return PlainTextResponse("", headers=toast("No changes", "info"))
    try:
        update_env(changes)
    except ValueError as e:
        return PlainTextResponse("", headers=toast(str(e), "err"))
    if RESTART_VARS & set(changes):
        mark_restart(env, changes)
    await sync_services()
    return respond(request, "Token saved", refresh=True)


@app.post("/services/{name}/toggle")
async def service_toggle(request: Request, name: str):
    svc = [s for s in services_store.load_all() if s["name"] == name]
    if not svc:
        return PlainTextResponse("", status_code=404)
    services_store.set_enabled(name, not svc[0]["enabled"])
    await sync_services()
    return respond(request, "Status changed", refresh=True)


@app.post("/services/{name}/delete")
async def service_delete(request: Request, name: str):
    try:
        services_store.delete(name)
    except (FileNotFoundError, ValueError) as e:
        return PlainTextResponse("", headers=toast(str(e), "err"))
    await sync_services()
    return respond(request, f"{name} deleted", redirect="/services")


@app.post("/services/{name}/test", response_class=HTMLResponse)
async def service_test(request: Request, name: str, path: str = Form("/")):
    if not path.startswith("/") or any(c in path for c in " \n\r'\""):
        return HTMLResponse('<span class="pill err">invalid path</span>')
    code, out = await asyncio.to_thread(dockerops.run, ["svc", name, "GET", path, "-o", "/dev/null", "-w", "%{http_code}", "--max-time", "10"])
    out = out.strip()
    if code == 0 and out.isdigit():
        kind = "ok" if out.startswith("2") else "warn" if out.startswith(("3", "4")) else "err"
        return HTMLResponse(f'<span class="pill {kind}">HTTP {out}</span>')
    return HTMLResponse(f'<span class="pill err">{(out or "error")[-160:]}</span>')


@app.post("/services/{name}/git-sync")
async def service_git_sync(request: Request, name: str):
    code, out = await asyncio.to_thread(dockerops.run, ["svc", "git", "sync", name])
    line = (out.strip().splitlines() or [""])[-1].strip()
    ok = code == 0 and "✓" in out
    return respond(request, line or "git sync done", "ok" if ok else "err", refresh=ok)


@app.post("/services/sync")
async def services_sync_route(request: Request):
    code, out = await sync_services()
    return respond(request, "svc sync done" if code == 0 else f"svc sync: {out[-120:]}",
                   "ok" if code == 0 else "err", refresh=True)


async def sync_services():
    """after any change to services/: regenerate compose.generated.yaml (docker mounts) and run svc sync"""
    try:
        generated.write(services_store.STACK_DIR)  # restart_needed() compares it with the container's mounts
    except OSError:
        pass
    return await asyncio.to_thread(dockerops.run, ["svc", "sync"])


@app.post("/services/{name}/svc-test", response_class=HTMLResponse)
async def service_svc_test(name: str):
    if not services_store.NAME.match(name):
        return HTMLResponse('<span class="pill err">invalid</span>')
    code, out = await asyncio.to_thread(dockerops.run, ["svc", "test", name])
    from html import escape
    lines = "".join(f'<div class="{"ok" if l.startswith("✓") else "err" if l.startswith("✗") else "muted"}">{escape(l)}</div>'
                    for l in out.strip().splitlines())
    return HTMLResponse(f'<div class="testout mono small">{lines or escape(out or "no output")}</div>')


# ---------------------------------------------------------------- help

@app.get("/help", response_class=HTMLResponse)
async def help_page(request: Request):
    return page(request, "help.html", templates_list=services_store.list_templates())


# ---------------------------------------------------------------- settings

@app.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    env = read_env()
    known = {k for _, items in SETTINGS_GROUPS for k, *_ in items}
    service_vars = {v["name"] for s in services_store.load_all() for v in s["vars"]}
    other = sorted(k for k in env if k not in known and k not in service_vars)
    return page(request, "settings.html", groups=SETTINGS_GROUPS, env=env, other=other, is_secret=is_secret)


READONLY = {k for _, items in SETTINGS_GROUPS for k, _, _, kind in items if kind == "readonly"}


@app.post("/settings")
async def settings_save(request: Request):
    form = await request.form()
    env = read_env()
    changes = {}
    for key, value in form.items():
        if not key.startswith("k_"):
            continue
        k = key[2:]
        if k in READONLY:
            continue
        value = value.strip()
        if is_secret(k):
            if form.get(f"clear_{k}"):
                changes[k] = ""
            elif value:
                changes[k] = value
        elif env.get(k, "") != value:
            changes[k] = value
    new_key, new_val = form.get("new_key", "").strip(), form.get("new_val", "").strip()
    if new_key:
        if not new_key.replace("_", "").isalnum() or not new_key[0].isalpha():
            return PlainTextResponse("", headers=toast("invalid variable name", "err"))
        changes[new_key.upper()] = new_val
    if not changes:
        return PlainTextResponse("", headers=toast("No changes", "info"))
    try:
        update_env(changes)
    except ValueError as e:
        return PlainTextResponse("", headers=toast(str(e), "err"))
    msg = f"{len(changes)} setting(s) saved"
    if RESTART_VARS & set(changes):
        mark_restart(env, changes)
    elif RC_VARS & set(changes):
        msg += " – restart remote control on the overview to apply"
    return respond(request, msg, refresh=True)


# ---------------------------------------------------------------- ssh

@app.get("/ssh", response_class=HTMLResponse)
async def ssh_page(request: Request):
    return page(request, "ssh.html", hosts=sshhosts.hosts(), pubkey=sshhosts.public_key())


# ---------------------------------------------------------------- SSH hosts: add / edit / remove in the UI

def _ssh_fields(form):
    return {k: (form.get(k) or "").strip() for k in ("alias", "hostname", "user", "port", "proxyjump", "sudo")}


def _ssh_dialog(request, step, **ctx):
    ctx.setdefault("f", {})
    ctx.setdefault("hosts", sshhosts.hosts())
    ctx.setdefault("error", None)
    return templates.TemplateResponse(request, "partials/ssh_dialog.html",
                                      {"request": request, "step": step, "site": site(), **ctx})


async def _ssh_test_results(alias):
    """(ok, text) lines for the result step; ok None = warning"""
    _SSH_STATUS.clear()
    sudo = sshhosts.sudo_of(alias)
    code, out = await asyncio.to_thread(dockerops.ssh_check, alias, sudo)
    lines = [l for l in out.strip().splitlines() if l.strip()]
    if code != 0:
        last = lines[-1] if lines else "no answer"
        return [(False, f"Login failed: {last}")], ("host key" in out.lower() or "identification has changed" in out.lower())
    res = [(True, f"Login works: {next((l for l in lines if '@' in l), alias)}")]
    if sudo:
        res.append((True, f"{sudo} works") if "SUDO_OK" in lines else (None, f"Login works, but {sudo} fails – the user needs passwordless sudo"))
    return res, False


@app.get("/ssh/new", response_class=HTMLResponse)
async def ssh_new(request: Request):
    return _ssh_dialog(request, "form")


@app.post("/ssh/new/form", response_class=HTMLResponse)
async def ssh_new_back(request: Request):
    return _ssh_dialog(request, "form", f=_ssh_fields(await request.form()))


@app.post("/ssh/new/scan", response_class=HTMLResponse)
async def ssh_new_scan(request: Request):
    f = _ssh_fields(await request.form())
    try:
        sshhosts.validate(f, aliases={h["alias"] for h in sshhosts.hosts()})
        scan = await asyncio.to_thread(sshops.scan, f)
    except (ValueError, RuntimeError) as e:
        return _ssh_dialog(request, "form", f=f, error=str(e))
    return _ssh_dialog(request, "hostkey", f=f, scan=scan, action="/ssh/new/trust", edit=False)


@app.post("/ssh/new/trust", response_class=HTMLResponse)
async def ssh_new_trust(request: Request):
    form = await request.form()
    f = _ssh_fields(form)
    try:
        await asyncio.to_thread(sshops.trust, form.get("token", ""))
    except RuntimeError as e:
        return _ssh_dialog(request, "form", f=f, error=str(e))
    return _ssh_dialog(request, "deploy", f=f, pubkey=sshhosts.public_key())


@app.post("/ssh/new/deploy", response_class=HTMLResponse)
async def ssh_new_deploy(request: Request):
    form = await request.form()
    f = _ssh_fields(form)
    results = []
    if form.get("mode") == "password":
        if not form.get("password"):
            return _ssh_dialog(request, "deploy", f=f, pubkey=sshhosts.public_key(), error="Enter the password, or use “The key is already there”.")
        try:
            state = await asyncio.to_thread(sshops.deploy, f, form.get("password"))
        except RuntimeError as e:
            return _ssh_dialog(request, "deploy", f=f, pubkey=sshhosts.public_key(), error=str(e))
        results.append((True, "Key added to authorized_keys" if state == "added" else "Key was already authorized"))
    try:
        if not sshhosts.get(f["alias"]):
            sshhosts.add(f)
    except ValueError as e:
        return _ssh_dialog(request, "form", f=f, error=str(e))
    results.append((True, f"Saved as ssh/config.d/{f['alias']}.conf"))
    test, _ = await _ssh_test_results(f["alias"])
    await sync_services()
    return _ssh_dialog(request, "result", title=f"{f['alias']} added", results=results + test)


@app.get("/ssh/{alias}/edit", response_class=HTMLResponse)
async def ssh_edit(request: Request, alias: str):
    h = sshhosts.get(alias)
    if not h:
        return _ssh_dialog(request, "result", title="Not found", results=[(False, f"host {alias} not found")])
    return _ssh_dialog(request, "edit", alias=alias, f=h, refs=sshops.references(alias))


@app.post("/ssh/{alias}/edit", response_class=HTMLResponse)
async def ssh_edit_save(request: Request, alias: str):
    old = sshhosts.get(alias)
    if not old:
        return _ssh_dialog(request, "result", title="Not found", results=[(False, f"host {alias} not found")])
    f = dict(old, **_ssh_fields(await request.form()))
    try:
        sshhosts.update(alias, f)
    except ValueError as e:
        return _ssh_dialog(request, "edit", alias=alias, f=f, refs=sshops.references(alias), error=str(e))
    results = [(True, "Saved")]
    if f["alias"] != alias:
        refs = sshops.references(alias)
        await asyncio.to_thread(sshops.rename_references, alias, f["alias"])
        results.append((True, f"Renamed {alias} → {f['alias']}" + (f", updated {len(refs)} reference(s)" if refs else "")))
    test, hostkey = await _ssh_test_results(f["alias"])
    await sync_services()
    return _ssh_dialog(request, "result", title=f"{f['alias']} saved", results=results + test,
                       hostkey_alias=f["alias"] if hostkey else None)


@app.post("/ssh/{alias}/hostkey/scan", response_class=HTMLResponse)
async def ssh_hostkey_scan(request: Request, alias: str):
    h = sshhosts.get(alias)
    if not h:
        return _ssh_dialog(request, "result", title="Not found", results=[(False, f"host {alias} not found")])
    try:
        scan = await asyncio.to_thread(sshops.scan, h)
    except RuntimeError as e:
        return _ssh_dialog(request, "result", title=f"Host key of {alias}", results=[(False, str(e))])
    return _ssh_dialog(request, "hostkey", f=h, scan=scan, action=f"/ssh/{alias}/hostkey/trust", edit=True)


@app.post("/ssh/{alias}/hostkey/trust", response_class=HTMLResponse)
async def ssh_hostkey_trust(request: Request, alias: str):
    form = await request.form()
    try:
        await asyncio.to_thread(sshops.trust, form.get("token", ""))
    except RuntimeError as e:
        return _ssh_dialog(request, "result", title=f"Host key of {alias}", results=[(False, str(e))])
    test, hostkey = await _ssh_test_results(alias)
    return _ssh_dialog(request, "result", title=f"Host key of {alias} confirmed", results=[(True, "Host key stored")] + test,
                       hostkey_alias=alias if hostkey else None)


@app.get("/ssh/{alias}/remove", response_class=HTMLResponse)
async def ssh_remove(request: Request, alias: str):
    h = sshhosts.get(alias)
    if not h:
        return _ssh_dialog(request, "result", title="Not found", results=[(False, f"host {alias} not found")])
    return _ssh_dialog(request, "remove", alias=alias, f=h, refs=sshops.references(alias))


@app.post("/ssh/{alias}/remove", response_class=HTMLResponse)
async def ssh_remove_do(request: Request, alias: str):
    form = await request.form()
    h = sshhosts.get(alias)
    if not h:
        return _ssh_dialog(request, "result", title="Not found", results=[(False, f"host {alias} not found")])
    results = []
    if form.get("unauthorize") and not form.get("force"):
        try:
            await asyncio.to_thread(sshops.unauthorize, alias)
            results.append((True, f"Key removed from {h['user']}@{h['hostname']}"))
        except RuntimeError as e:
            return _ssh_dialog(request, "remove", alias=alias, f=h, refs=sshops.references(alias),
                               error=f"Could not remove the key on the host: {e}")
    try:
        sshhosts.remove(alias)
    except ValueError as e:
        return _ssh_dialog(request, "remove", alias=alias, f=h, refs=sshops.references(alias), error=str(e))
    results.append((True, f"{alias} removed from ssh/config.d/{h['file']}"))
    refs = sshops.references(alias)
    if refs:
        results.append((None, "Still referenced by " + ", ".join(f"{k} {n}" for k, n in refs)))
    _SSH_STATUS.clear()
    await sync_services()
    return _ssh_dialog(request, "result", title=f"{alias} removed", results=results)


@app.post("/ssh/{alias}/sudo")
async def ssh_sudo(request: Request, alias: str):
    if alias not in {h["alias"] for h in sshhosts.hosts()}:
        return PlainTextResponse("", headers=toast("unknown host", "err"))
    form = await request.form()
    cmd = form.get("sudo", "").strip() if form.get("use_sudo") else ""
    try:
        sshhosts.set_sudo(alias, cmd)
        _SSH_STATUS.clear()
    except ValueError as e:
        return PlainTextResponse("", headers=toast(str(e), "err"))
    await sync_services()  # svc docker contexts + Claude's SSH host table
    return respond(request, f"{alias}: sudo {'= ' + cmd if cmd else 'off'}", refresh=True)


_SSH_STATUS = {}


async def ssh_state(alias):
    """('ok'|'warn'|'err'|'', title): ok = login (and sudo, if configured) works, warn = login ok but sudo fails, err = no login"""
    if alias not in {h["alias"] for h in sshhosts.hosts()}:
        return "", "unknown host"
    sudo = sshhosts.sudo_of(alias)
    hit = _SSH_STATUS.get((alias, sudo))
    if hit and time.time() - hit[0] < 60:  # the overview refreshes every 15 s – don't ssh that often
        code, out = hit[1]
    else:
        code, out = await asyncio.to_thread(dockerops.ssh_check, alias, sudo)
        _SSH_STATUS[(alias, sudo)] = (time.time(), (code, out))
    lines = [l for l in out.strip().splitlines() if l.strip()]
    if code != 0:
        return "err", f"not reachable: {lines[-1] if lines else ''}"
    if sudo and "SUDO_FAILED" in lines:
        return "warn", f"login ok, but {sudo} fails (needs NOPASSWD)"
    who = next((l for l in lines if "@" in l), "")
    return "ok", f"ok: {who}{' · sudo ok' if sudo else ''}"


@app.get("/ssh/{alias}/status", response_class=HTMLResponse)
async def ssh_status(alias: str):
    """status dot for the host tables"""
    state, title = await ssh_state(alias)
    return HTMLResponse(f'<span class="dot {state}" title="{html.escape(title)}"></span>')


@app.post("/ssh/{alias}/test", response_class=HTMLResponse)
async def ssh_test(alias: str):
    if alias not in {h["alias"] for h in sshhosts.hosts()}:
        return HTMLResponse('<span class="pill err">unknown</span>')
    sudo = sshhosts.sudo_of(alias)
    code, out = await asyncio.to_thread(dockerops.ssh_check, alias, sudo)
    lines = [l for l in out.strip().splitlines() if l.strip()]
    sudo_state = lines.pop() if sudo and lines and lines[-1] in ("SUDO_OK", "SUDO_FAILED") else ""
    line = html.escape(lines[-1] if lines else "")
    if code == 0:
        res = f'<span class="pill ok">✓ {line}</span>'
        if sudo_state == "SUDO_OK":
            res += f' <span class="pill ok">✓ {html.escape(sudo)}</span>'
        elif sudo_state:
            res += f' <span class="pill err" title="the SSH user needs passwordless sudo (NOPASSWD)">✗ {html.escape(sudo)}: needs NOPASSWD</span>'
        return HTMLResponse(res)
    return HTMLResponse(f'<span class="pill err" title="{line}">✗ {line[:120]}</span>')


# ---------------------------------------------------------------- updates & jobs

@app.get("/updates", response_class=HTMLResponse)
async def updates_page(request: Request):
    return page(request, "updates.html", sessions=dockerops.tmux_sessions())


@app.get("/partials/versions", response_class=HTMLResponse)
async def versions(request: Request):
    installed, latest = await asyncio.gather(asyncio.to_thread(dockerops.claude_version),
                                             asyncio.to_thread(dockerops.claude_latest))
    pin = read_env().get("CLAUDE_CODE_VERSION", "latest") or "latest"
    return templates.TemplateResponse(request, "partials/versions.html",
                                      {"installed": installed, "latest": latest, "pin": pin, "st": dockerops.status()})


@app.post("/updates/claude")
async def update_claude():
    steps = [
        COMPOSE + ["build", "--pull", "--build-arg", f"CACHE_BUST={int(time.time())}", CLAUDE_SERVICE],
        COMPOSE + ["up", "-d", "--no-deps", "--force-recreate", CLAUDE_SERVICE],
        ["docker", "image", "prune", "-f", "--filter", "label=com.docker.compose.project=tentaclai"],
    ]
    try:
        job = jobs.start("Update Claude Code (rebuild image)", steps)
    except RuntimeError as e:
        return PlainTextResponse("", headers=toast(str(e), "err"))
    return PlainTextResponse("", headers={"HX-Redirect": f"/jobs/{job.id}"})


@app.get("/jobs/{job_id}", response_class=HTMLResponse)
async def job_page(request: Request, job_id: str):
    job = jobs.JOBS.get(job_id)
    if not job:
        return RedirectResponse("/updates", status_code=303)
    return page(request, "job.html", job=job)


@app.get("/jobs/{job_id}/stream")
async def job_stream(job_id: str):
    job = jobs.JOBS.get(job_id)
    if not job:
        return PlainTextResponse("", status_code=404)

    async def gen():
        async for line in job.follow():
            yield f"data: {json.dumps(line)}\n\n"
        yield f"event: done\ndata: {json.dumps({'ok': job.ok})}\n\n"
    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ---------------------------------------------------------------- logs

@app.get("/logs", response_class=HTMLResponse)
async def logs_page(request: Request):
    return page(request, "logs.html", logs=dockerops.logs(300))


@app.get("/logs/stream")
async def logs_stream():
    c = dockerops.container()
    if c is None:
        return PlainTextResponse("", status_code=404)
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()

    def follow():
        try:
            for chunk in c.logs(stream=True, follow=True, since=int(time.time()), timestamps=True):
                loop.call_soon_threadsafe(queue.put_nowait, chunk.decode(errors="replace"))
        except Exception:  # noqa: BLE001
            pass
        loop.call_soon_threadsafe(queue.put_nowait, None)

    loop.run_in_executor(None, follow)

    async def gen():
        while True:
            try:
                item = await asyncio.wait_for(queue.get(), timeout=20)
            except asyncio.TimeoutError:
                yield ": keepalive\n\n"
                continue
            if item is None:
                return
            for line in item.splitlines():
                yield f"data: {json.dumps(line)}\n\n"
    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/api/status")
async def api_status():
    return JSONResponse(dockerops.status())
