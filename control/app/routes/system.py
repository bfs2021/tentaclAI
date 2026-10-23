"""Sign-in, overview, sandbox lifecycle, terminal, help, updates, jobs and logs."""
import asyncio
import json
import os
import time

import segno
from fastapi import APIRouter, Form, Request, WebSocket
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, StreamingResponse

from .. import activity, auditlog, auth, dockerops, egress, extensions, jobs, netguard, notify, services_store, sshhosts, updatecheck, usage
from ..config import CLAUDE_SERVICE, COMPOSE, LOG_CONTAINERS
from ..envfile import read_env, update_env
from ..terminal import PROFILES, bridge, onboard_cmd
from ..web import BASE, TC_VERSION, client_ip, page, respond, templates, toast
import tclib  # noqa: E402  shared with the sandbox tools

router = APIRouter()


@router.get("/healthz")
async def healthz():
    return {"ok": True}


@router.get("/favicon.ico")
@router.get("/apple-touch-icon.png")
@router.get("/apple-touch-icon-precomposed.png")
async def favicon(request: Request):
    """browsers ask for these on their own (Safari ignores SVG icons) – the logo as PNG, not the login redirect"""
    name = "favicon-32.png" if request.url.path == "/favicon.ico" else "apple-touch-icon.png"
    return FileResponse(os.path.join(BASE, "static", name), media_type="image/png",
                        headers={"Cache-Control": "public, max-age=86400"})


@router.get("/setup", response_class=HTMLResponse)
async def setup_form(request: Request, token: str = ""):
    if auth.has_account():
        return RedirectResponse("/login", status_code=303)
    return templates.TemplateResponse(request, "setup.html", {"token": token, "error": None, "f": {}})


@router.post("/setup")
async def setup_save(request: Request, token: str = Form(""), username: str = Form(""), email: str = Form(""),
                     name: str = Form(""), password: str = Form(...), password2: str = Form(...)):
    if auth.has_account():
        return RedirectResponse("/login", status_code=303)
    error = None
    if token != auth.SETUP_TOKEN:
        error = "invalid setup token – see ./tentaclai.sh setup-url"
    else:
        error = auth.profile_error(username, email, name) or auth.password_error(password, password2)
    if error:
        return templates.TemplateResponse(request, "setup.html", {"token": token, "error": error,
                                          "f": {"username": username, "email": email, "name": name}}, status_code=400)
    user = await asyncio.to_thread(auth.create_admin, username, email, name, password)
    auth.sign_in(request, user)
    auditlog.user(request, "auth", "tentacontrol", "set up the admin account")
    return RedirectResponse("/", status_code=303)


def _login_page(request, error=None, status=200, username=""):
    return templates.TemplateResponse(request, "login.html", {"error": error, "username": username,
                                      "ask_username": not auth.profile_missing()}, status_code=status)


@router.get("/login", response_class=HTMLResponse)
async def login_form(request: Request):
    if not auth.has_account():
        return RedirectResponse("/setup", status_code=303)
    return _login_page(request)


@router.post("/login")
async def login(request: Request, username: str = Form(""), password: str = Form(...)):
    ip = client_ip(request)
    given = "".join(c for c in username.strip().lower() if (c.isascii() and c.isalnum()) or c in "._-")[:32]  # what was typed, for the log
    wait = auth.throttled(ip)
    if wait:
        auditlog.user(request, "auth", "tentacontrol", "sign in", "blocked", detail="too many attempts", user=given)
        return _login_page(request, f"too many attempts – please wait {wait} s", 429, username)
    user = await asyncio.to_thread(auth.check_login, username, password)
    if not user:
        auditlog.user(request, "auth", "tentacontrol", "sign in", "failed", detail="wrong username or password", user=given)
        auth.register_failure(ip)
        await asyncio.sleep(auth.FAIL_DELAY)
        return _login_page(request, "wrong username or password", 401, username)
    auth.clear_failures(ip)
    request.session.clear()
    if auth.twofactor_enabled() and not auth.is_trusted(request.cookies.get(auth.TRUST_COOKIE)):
        request.session["pending_2fa"] = time.time()  # password done – the code comes next
        request.session["pending_uid"] = user["id"]
        return RedirectResponse("/login/2fa", status_code=303)
    auth.sign_in(request, user)
    auditlog.user(request, "auth", "tentacontrol", "sign in", detail="password" + (" + trusted browser" if auth.twofactor_enabled() else ""))
    return RedirectResponse("/", status_code=303)


@router.get("/account/complete", response_class=HTMLResponse)
async def account_complete_form(request: Request):
    if not auth.profile_missing():
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse(request, "account_complete.html", {"error": None, "f": {}})


@router.post("/account/complete")
async def account_complete(request: Request, username: str = Form(""), email: str = Form(""), name: str = Form("")):
    """an account from before usernames: username and email once, right after the sign-in"""
    u = auth.current_user(request)
    if not u or not auth.profile_missing():
        return RedirectResponse("/", status_code=303)
    error = auth.profile_error(username, email, name, uid=u["id"])
    if error:
        return templates.TemplateResponse(request, "account_complete.html", {"error": error,
                                          "f": {"username": username, "email": email, "name": name}}, status_code=400)
    auth.update_profile(u["id"], username, email, name)
    request.session["uid"] = u["id"]
    auditlog.user(request, "admin", "account", "account profile completed", part="account", mode="POST")
    return RedirectResponse("/", status_code=303)


def _pending_2fa(request):
    ts = request.session.get("pending_2fa", 0)
    return auth.twofactor_enabled() and 0 < time.time() - ts < auth.PENDING_SECONDS


def browser_label(ua):
    """'Firefox on Linux' – to recognise a trusted browser in Settings → Security"""
    browser = next((n for k, n in (("Edg/", "Edge"), ("Firefox/", "Firefox"), ("Chrome/", "Chrome"), ("Safari/", "Safari"))
                    if k in ua), "Browser")
    system = next((n for k, n in (("iPhone", "iPhone"), ("iPad", "iPad"), ("Android", "Android"), ("Windows", "Windows"),
                                  ("Mac OS X", "macOS"), ("Linux", "Linux")) if k in ua), "")
    return f"{browser} on {system}" if system else browser


@router.get("/login/2fa", response_class=HTMLResponse)
async def login_2fa_form(request: Request):
    if not _pending_2fa(request):
        return RedirectResponse("/login", status_code=303)
    return templates.TemplateResponse(request, "login_2fa.html", {"error": None, "days": auth.TRUST_DAYS})


@router.post("/login/2fa")
async def login_2fa(request: Request, code: str = Form(...), trust: str = Form("")):
    if not _pending_2fa(request):
        return RedirectResponse("/login", status_code=303)
    ip = client_ip(request)
    ctx = {"days": auth.TRUST_DAYS}
    wait = auth.throttled(ip)
    if wait:
        auditlog.user(request, "auth", "tentacontrol", "sign in: second factor", "blocked", detail="too many attempts")
        return templates.TemplateResponse(request, "login_2fa.html", {**ctx, "error": f"too many attempts – please wait {wait} s"}, status_code=429)
    kind = await asyncio.to_thread(auth.check_second_factor, code)
    if kind is None:
        auditlog.user(request, "auth", "tentacontrol", "sign in: second factor", "failed", detail="wrong code")
        auth.register_failure(ip)
        await asyncio.sleep(auth.FAIL_DELAY)
        return templates.TemplateResponse(request, "login_2fa.html", {**ctx, "error": "wrong code"}, status_code=401)
    auth.clear_failures(ip)
    user = next((u for u in auth.users() if u.get("id") == request.session.get("pending_uid")), None) or auth.admin()
    auth.sign_in(request, user)
    auditlog.user(request, "auth", "tentacontrol", "sign in", detail=f"password + {kind}" + (" · browser trusted" if trust else ""))
    if kind == "recovery":
        left = auth.twofactor_status()["recovery_left"]
        notify.emit("signin", "tentacontrol: sign-in with a recovery code", f"{left} recovery codes left.", key=f"signin:{time.time()}")
        request.session["flash"] = {"msg": f"Signed in with a recovery code – {left} left. Set up new ones under Settings → Security.",
                                    "kind": "warn"}
    response = RedirectResponse("/", status_code=303)
    if trust:
        token = auth.trust_browser(browser_label(request.headers.get("user-agent", "")))
        response.set_cookie(auth.TRUST_COOKIE, token, max_age=auth.TRUST_DAYS * 86400, httponly=True, samesite="strict",
                            secure=request.url.scheme == "https", path="/")
    return response


@router.post("/logout")
async def logout(request: Request):
    name = auth.username(request)
    request.session.clear()
    auditlog.user(request, "auth", "tentacontrol", "sign out", user=name)
    return RedirectResponse("/login", status_code=303)


@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    return page(request, "dashboard.html")


@router.get("/partials/overview", response_class=HTMLResponse)
async def overview(request: Request):
    st = dockerops.status()
    auth = dockerops.claude_auth()
    plan = usage.plan()
    ctx = {"st": st, "stats": None, "version": None, "logged": bool(auth.get("loggedIn")), "auth": auth,
           "limits": [l for l in plan.get("limits", []) if l["window"] == "session" or l["scope"] in ("", "all models")][:2],
           "sessions": [], "rc": None, "services": services_store.load_all(), "hosts": sshhosts.hosts()}
    if st["running"]:
        ctx.update(stats=dockerops.stats(), version=dockerops.claude_version(),
                   sessions=dockerops.tmux_sessions(), rc=dockerops.rc_info())
    rows = egress.seen_rows()
    ctx["net"] = {"mode": egress.mode(), "total": len(rows), "unknown": sum(1 for r in rows if r["kind"] == "unknown"),
                  "pending": sum(1 for a in activity.pending() if a["part"] == "network")}
    ctx["flags"] = auditlog.unseen_flags()
    if ctx["rc"] and ctx["rc"]["url"]:
        ctx["rc"]["qr"] = segno.make(ctx["rc"]["url"], error="m").svg_inline(scale=4, border=2, dark="#111", light="#fff", omitsize=True)
    return templates.TemplateResponse(request, "partials/overview.html", ctx)


@router.post("/container/{action}")
async def container_action(request: Request, action: str):
    if action == "create":
        try:
            job = jobs.start("Create & start tentaclai", [COMPOSE + ["up", "-d", "--no-deps", "egress"], COMPOSE + ["up", "-d", "--no-deps", CLAUDE_SERVICE]])
        except RuntimeError as e:
            return PlainTextResponse("", headers=toast(str(e), "err"))
        return PlainTextResponse("", headers={"HX-Redirect": f"/builds/{job.id}"})
    if action == "recreate":
        try:
            job = jobs.start("Recreate tentaclai (apply configuration)",
                             [COMPOSE + ["up", "-d", "--no-deps", "egress"],
                              COMPOSE + ["up", "-d", "--no-deps", "--force-recreate", CLAUDE_SERVICE]])
        except RuntimeError as e:
            return PlainTextResponse("", headers=toast(str(e), "err"))
        return PlainTextResponse("", headers={"HX-Redirect": f"/builds/{job.id}"})
    if action not in ("start", "stop", "restart"):
        return PlainTextResponse("unknown action", status_code=400)
    ok, msg = await asyncio.to_thread(dockerops.lifecycle, action)
    label = {"start": "started", "stop": "stopped", "restart": "restarted"}[action]
    return respond(request, f"tentaclai {label}" if ok else msg, "ok" if ok else "err", refresh=True)


@router.post("/rc/toggle")
async def rc_toggle(request: Request):
    """enable/disable remote control (RC_ENABLED) and restart rc-loop so it applies immediately"""
    enable = (read_env().get("RC_ENABLED", "true") or "true").lower() != "true"
    update_env({"RC_ENABLED": "true" if enable else "false"})
    await asyncio.to_thread(dockerops.rc_restart)
    return respond(request, "Remote control enabled" if enable else "Remote control disabled", refresh=True)


@router.post("/claude/logout")
async def claude_logout(request: Request):
    code, out = await asyncio.to_thread(dockerops.claude_logout)
    return respond(request, "Signed out of Claude – sign in again with “Sign in now”" if code == 0 else f"Sign-out failed: {out.strip()[:120]}",
                   "ok" if code == 0 else "err", refresh=True)


@router.post("/rc/restart")
async def rc_restart():
    code, out = await asyncio.to_thread(dockerops.rc_restart)
    return PlainTextResponse("", headers=toast("Remote control restarted" if code == 0 else f"Error: {out[:120]}",
                                               "ok" if code == 0 else "err"))


@router.get("/terminal", response_class=HTMLResponse)
async def terminal_page(request: Request, p: str = "claude", alias: str = ""):
    if p == "onboard":
        title = f"Onboard service: {alias}"
    else:
        title = PROFILES.get(p, PROFILES["claude"])["title"]
    logged = await asyncio.to_thread(dockerops.logged_in_claude)
    return page(request, "terminal.html", profiles=PROFILES, current=p, title=title, logged=logged,
                params={"alias": alias},
                reattach=p in ("claude", "rc", "inventory", "onboard"))  # tmux-based: safe to reconnect automatically


@router.websocket("/ws/terminal")
async def ws_terminal(ws: WebSocket, p: str = "claude", alias: str = ""):
    if not netguard.allowed("/ws/terminal", ws.client.host if ws.client else "") or not auth.logged_in(ws) or not auth.same_origin(ws):
        await ws.close(code=4401)
        return
    await ws.accept()
    st = dockerops.status()
    if not st["running"]:
        await ws.send_text(json.dumps({"type": "error", "msg": "tentaclAI is not running – start it on the overview first."}))
        await ws.close()
        return
    try:
        if p == "onboard":
            cmd = onboard_cmd(alias)
        else:
            cmd = PROFILES.get(p, PROFILES["claude"])["cmd"]
    except ValueError as e:
        await ws.send_text(json.dumps({"type": "error", "msg": str(e)}))
        await ws.close()
        return
    t = time.time()
    auditlog.user(ws, "admin", alias or p, f"terminal opened: {p}" + (f" {alias}" if alias else ""), part="terminal")
    try:
        await bridge(ws, cmd)
    finally:
        auditlog.user(ws, "admin", alias or p, f"terminal closed: {p}" + (f" {alias}" if alias else ""), part="terminal",
                      seconds=round(time.time() - t))


# what tentaclAI Community shows, locked, on Modules – with tentaclAI Enterprise its registered modules are listed instead
COMMUNITY_MODULES = [
    {"key": "reports", "title": "Compliance reports", "text": "Reports from the audit log – e.g. all writes to production in a quarter, approvals by approver – as PDF or CSV, as evidence for ISO 27001 or SOC 2."},
    {"key": "siem", "title": "SIEM export", "text": "Streams the audit log to your SIEM (syslog, Splunk, Elastic) as JSON lines."},
]


@router.get("/modules", response_class=HTMLResponse)
async def modules_page(request: Request):
    return page(request, "modules.html",
                modules=COMMUNITY_MODULES if extensions.edition() == "community" else extensions.modules())


@router.get("/help", response_class=HTMLResponse)
async def help_page(request: Request):
    return page(request, "help.html", templates_list=services_store.list_templates())


@router.get("/updates", response_class=HTMLResponse)
async def updates_page(request: Request):
    return page(request, "updates.html", sessions=dockerops.tmux_sessions())


@router.get("/partials/versions", response_class=HTMLResponse)
async def versions(request: Request):
    installed, latest = await asyncio.gather(asyncio.to_thread(dockerops.claude_version),
                                             asyncio.to_thread(dockerops.claude_latest))
    pin = read_env().get("CLAUDE_CODE_VERSION", "latest") or "latest"
    code, out = await asyncio.to_thread(dockerops.run, ["cat", "/etc/tentaclai/VERSION"])
    sandbox = out.strip() if code == 0 and out.strip() else None
    return templates.TemplateResponse(request, "partials/versions.html",
                                      {"installed": installed, "latest": latest, "pin": pin, "st": dockerops.status(),
                                       "tc_version": TC_VERSION, "sandbox_version": sandbox,
                                       "update": updatecheck.status(TC_VERSION)})


@router.post("/updates/check", response_class=HTMLResponse)
async def update_check_now(request: Request):
    await asyncio.to_thread(updatecheck.refresh, True)
    return await versions(request)


@router.post("/updates/claude")
async def update_claude():
    steps = [
        COMPOSE + ["build", "--pull", "--build-arg", f"CACHE_BUST={int(time.time())}", CLAUDE_SERVICE],
        COMPOSE + ["up", "-d", "--no-deps", "egress"],
        COMPOSE + ["up", "-d", "--no-deps", "--force-recreate", CLAUDE_SERVICE],
        ["docker", "image", "prune", "-f", "--filter", "label=com.docker.compose.project=tentaclai"],
    ]
    try:
        job = jobs.start("Update Claude Code (rebuild image)", steps)
    except RuntimeError as e:
        return PlainTextResponse("", headers=toast(str(e), "err"))
    return PlainTextResponse("", headers={"HX-Redirect": f"/builds/{job.id}"})


@router.get("/builds/{job_id}", response_class=HTMLResponse)
async def job_page(request: Request, job_id: str):
    job = jobs.JOBS.get(job_id)
    if not job:
        return RedirectResponse("/updates", status_code=303)
    return page(request, "job.html", job=job)


@router.get("/builds/{job_id}/stream")
async def job_stream(job_id: str):
    job = jobs.JOBS.get(job_id)
    if not job:
        return PlainTextResponse("", status_code=404)

    async def gen():
        async for line in job.follow():
            yield f"data: {json.dumps(line)}\n\n"
        yield f"event: done\ndata: {json.dumps({'ok': job.ok})}\n\n"
    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


AUDIT_FILTERS = ("category", "actor", "service", "decision")
AUDIT_KINDS = {"service": "svc command", "tool": "Claude tool", "network": "network", "admin": "change in tentacontrol",
               "auth": "sign-in", "job": "job run", "system": "system"}  # the What filter's labels


def _audit_query(request):
    """the filters of Logs → Audit from the query string (defaults: last 24 h, everything)"""
    p = request.query_params
    f = {k: p.get(k, "")[:120] for k in AUDIT_FILTERS}
    f.update(since=p.get("since", "24h"), start=p.get("start", "")[:10], end=p.get("end", "")[:10], q=p.get("q", "")[:200],
             origin=p.get("origin", "")[:80])
    who = p.get("who", "")[:80]  # the page's one Who choice: an actor, origin:<group> or job:<name>
    if who in tclib.AUDIT_ACTORS:
        f.update(actor=who, origin="")
    elif who.startswith("origin:"):
        f.update(actor="", origin=who[7:])
    elif who.startswith("job:"):
        f.update(actor="", origin=who)
    return f


def _audit_who(f):
    """the Who choice that shows the actor / origin filter"""
    if f["origin"]:
        return f["origin"] if f["origin"].startswith("job:") else "origin:" + f["origin"]
    return f["actor"]


def _audit_qs(f):
    from urllib.parse import urlencode
    return urlencode({k: v for k, v in f.items() if v and not (k == "since" and v == "24h")})


@router.get("/logs", response_class=HTMLResponse)
async def logs_page(request: Request):
    """Logs → Audit: everything Claude, jobs, the user and tentaclAI did – filter, search, export"""
    auditlog.mark_flags_seen()  # opening the audit log counts as having seen the flagged entries
    f = _audit_query(request)
    entries, more = await asyncio.to_thread(auditlog.entries, **f)
    services, jobs_known = await asyncio.to_thread(auditlog.known)
    filtered = any(f[k] for k in (*AUDIT_FILTERS, "q", "origin", "start", "end")) or f["since"] != "24h"
    return page(request, "logs.html", entries=entries, more=more, f=f, who=_audit_who(f), qs=_audit_qs(f), filtered=filtered,
                services_known=services, jobs_known=jobs_known, categories=tclib.AUDIT_CATEGORIES,
                actors=tclib.AUDIT_ACTORS, decisions=tclib.AUDIT_DECISIONS, kinds=AUDIT_KINDS, retention=tclib.audit_retention(read_env()))


@router.get("/logs/audit/more", response_class=HTMLResponse)
async def logs_audit_more(request: Request, after: str = ""):
    """the next page of entries ("Load more") – rows only"""
    f = _audit_query(request)
    entries, more = await asyncio.to_thread(auditlog.entries, after=after[:40], **f)
    return templates.TemplateResponse(request, "partials/audit_rows.html", {"entries": entries, "more": more, "qs": _audit_qs(f)})


@router.get("/logs/audit/export")
async def logs_audit_export(request: Request, fmt: str = "csv"):
    """every entry matching the filters, as CSV or JSON lines – the export itself is an audit entry too"""
    fmt = "jsonl" if fmt == "jsonl" else "csv"
    f = _audit_query(request)
    lines = await asyncio.to_thread(lambda: list(auditlog.export(fmt, **f)))
    auditlog.user(request, "admin", "audit log", f"export as {fmt}", part="logs", mode="GET",
                  detail=f"{max(0, len(lines) - (fmt == 'csv'))} entries · " + (_audit_qs(f)[:280] or "no filter"))
    name = f"tentaclai-audit-{time.strftime('%Y%m%d-%H%M')}.{fmt}"
    return StreamingResponse(iter(lines), media_type="text/csv; charset=utf-8" if fmt == "csv" else "application/x-ndjson",
                             headers={"Content-Disposition": f'attachment; filename="{name}"'})


def _log_container(key):
    return key if key in LOG_CONTAINERS else "tentaclai"


@router.get("/logs/containers", response_class=HTMLResponse)
async def logs_containers(request: Request, c: str = "tentaclai"):
    """Logs → Containers: the log of each of tentaclAI's three containers, live"""
    c = _log_container(c)
    name, what = LOG_CONTAINERS[c]
    states = {k: dockerops.container_running(n) for k, (n, _) in LOG_CONTAINERS.items()}
    return page(request, "logs_containers.html", c=c, cname=name, what=what, containers=LOG_CONTAINERS, states=states,
                logs=await asyncio.to_thread(dockerops.logs, 300, name))


@router.get("/logs/containers/download")
async def logs_containers_download(c: str = "tentaclai", tail: int = 5000):
    name = LOG_CONTAINERS[_log_container(c)][0]
    text = await asyncio.to_thread(dockerops.logs, max(1, min(tail, 100_000)), name)
    return PlainTextResponse(text, headers={"Content-Disposition": f'attachment; filename="{name}-{time.strftime("%Y%m%d-%H%M")}.log"'})


@router.get("/logs/stream")
async def logs_stream(c: str = "tentaclai"):
    cont = dockerops.container(LOG_CONTAINERS[_log_container(c)][0])
    if cont is None:
        return PlainTextResponse("", status_code=404)
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()

    def follow():
        try:
            for chunk in cont.logs(stream=True, follow=True, since=int(time.time()), timestamps=True):
                loop.call_soon_threadsafe(queue.put_nowait, dockerops.local_ts(chunk.decode(errors="replace")))
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


@router.get("/api/status")
async def api_status():
    return JSONResponse(dockerops.status())
