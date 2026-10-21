"""Sign-in, overview, sandbox lifecycle, terminal, help, updates, jobs and logs."""
import asyncio
import json
import time

import segno
from fastapi import APIRouter, Form, Request, WebSocket
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, StreamingResponse

from .. import activity, auth, dockerops, egress, jobs, netguard, notify, services_store, sshhosts, updatecheck, usage
from ..config import CLAUDE_SERVICE, COMPOSE
from ..envfile import read_env, update_env
from ..terminal import PROFILES, bridge, onboard_cmd
from ..web import TC_VERSION, client_ip, page, respond, templates, toast
import tclib  # noqa: E402  shared with the sandbox tools

router = APIRouter()


@router.get("/healthz")
async def healthz():
    return {"ok": True}


@router.get("/setup", response_class=HTMLResponse)
async def setup_form(request: Request, token: str = ""):
    if auth.has_password():
        return RedirectResponse("/login", status_code=303)
    return templates.TemplateResponse(request, "setup.html", {"token": token, "error": None})


@router.post("/setup")
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


@router.get("/login", response_class=HTMLResponse)
async def login_form(request: Request):
    if not auth.has_password():
        return RedirectResponse("/setup", status_code=303)
    return templates.TemplateResponse(request, "login.html", {"error": None})


@router.post("/login")
async def login(request: Request, password: str = Form(...)):
    ip = client_ip(request)
    wait = auth.throttled(ip)
    if wait:
        return templates.TemplateResponse(request, "login.html", {"error": f"too many attempts – please wait {wait} s"}, status_code=429)
    if not auth.check_password(password):
        auth.register_failure(ip)
        await asyncio.sleep(auth.FAIL_DELAY)
        return templates.TemplateResponse(request, "login.html", {"error": "wrong password"}, status_code=401)
    auth.clear_failures(ip)
    request.session.clear()
    if auth.twofactor_enabled() and not auth.is_trusted(request.cookies.get(auth.TRUST_COOKIE)):
        request.session["pending_2fa"] = time.time()  # password done – the code comes next
        return RedirectResponse("/login/2fa", status_code=303)
    request.session["auth"] = True
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
        return templates.TemplateResponse(request, "login_2fa.html", {**ctx, "error": f"too many attempts – please wait {wait} s"}, status_code=429)
    kind = await asyncio.to_thread(auth.check_second_factor, code)
    if kind is None:
        auth.register_failure(ip)
        await asyncio.sleep(auth.FAIL_DELAY)
        return templates.TemplateResponse(request, "login_2fa.html", {**ctx, "error": "wrong code"}, status_code=401)
    auth.clear_failures(ip)
    request.session.clear()
    request.session["auth"] = True
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
    request.session.clear()
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
        return PlainTextResponse("", headers={"HX-Redirect": f"/jobs/{job.id}"})
    if action == "recreate":
        try:
            job = jobs.start("Recreate tentaclai (apply configuration)",
                             [COMPOSE + ["up", "-d", "--no-deps", "egress"],
                              COMPOSE + ["up", "-d", "--no-deps", "--force-recreate", CLAUDE_SERVICE]])
        except RuntimeError as e:
            return PlainTextResponse("", headers=toast(str(e), "err"))
        return PlainTextResponse("", headers={"HX-Redirect": f"/jobs/{job.id}"})
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
    if not netguard.allowed("/ws/terminal", ws.client.host if ws.client else "") or not ws.session.get("auth") or not auth.same_origin(ws):
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
    await bridge(ws, cmd)


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
    return PlainTextResponse("", headers={"HX-Redirect": f"/jobs/{job.id}"})


@router.get("/jobs/{job_id}", response_class=HTMLResponse)
async def job_page(request: Request, job_id: str):
    job = jobs.JOBS.get(job_id)
    if not job:
        return RedirectResponse("/updates", status_code=303)
    return page(request, "job.html", job=job)


@router.get("/jobs/{job_id}/stream")
async def job_stream(job_id: str):
    job = jobs.JOBS.get(job_id)
    if not job:
        return PlainTextResponse("", status_code=404)

    async def gen():
        async for line in job.follow():
            yield f"data: {json.dumps(line)}\n\n"
        yield f"event: done\ndata: {json.dumps({'ok': job.ok})}\n\n"
    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.get("/logs", response_class=HTMLResponse)
async def logs_page(request: Request):
    return page(request, "logs.html", logs=dockerops.logs(300))


@router.get("/logs/stream")
async def logs_stream():
    c = dockerops.container()
    if c is None:
        return PlainTextResponse("", status_code=404)
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()

    def follow():
        try:
            for chunk in c.logs(stream=True, follow=True, since=int(time.time()), timestamps=True):
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
