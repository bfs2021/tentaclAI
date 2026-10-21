"""tentacontrol – web admin for the tentaclai sandbox + service gateway for Claude Code."""
import asyncio
import contextlib
import os
import time

from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from . import activity, auth, connectors, dockerops, netguard, notify, skills, updatecheck, usage
from .envfile import drop_generated_compose, read_env
from .routes import network, settings, ssh, system, tentacles
from .web import BASE, TC_VERSION


@contextlib.asynccontextmanager
async def lifespan(_app):
    """startup: activity token, background loops (notifications, usage, update check), setup hint"""
    for start in (migrate, prepare_activity, start_notifications, start_update_check, announce_setup):
        await start()
    yield


app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
app.mount("/static", StaticFiles(directory=os.path.join(BASE, "static")), name="static")
app.mount("/vendor", StaticFiles(directory=os.environ.get("VENDOR_DIR", "/app/vendor")), name="vendor")  # VENDOR_DIR: tests

PUBLIC = ("/login", "/setup", "/static/", "/vendor/", "/healthz", "/internal/")  # /internal/: svc token, checked per route


@app.middleware("http")
async def guard(request: Request, call_next):
    path = request.url.path
    if not netguard.allowed(path, request.client.host if request.client else ""):
        return PlainTextResponse("not from here", status_code=403)  # sandbox: svc endpoints only; gateway: egress only
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


async def migrate():
    """one-time adjustments of an existing installation"""
    if drop_generated_compose():
        print("migration: compose.generated.yaml removed from COMPOSE_FILE (docker services run over SSH only)", flush=True)


async def prepare_activity():
    activity.ensure_token()


async def discover_claude():
    """claude.ai connectors (new ones start disabled) and skills – `claude mcp list` health-checks every MCP server,
    so this runs hourly, after a sign-in and on "Check", not every few minutes"""
    await asyncio.to_thread(connectors.discover)
    await asyncio.to_thread(skills.discover)


async def start_notifications():
    loop = asyncio.get_running_loop()
    loop.create_task(notify.worker())

    async def watch():
        """state changes worth a notification – reported once per change, not every minute"""
        state, down_since = {}, None
        await asyncio.sleep(60)
        while True:
            try:
                st = await asyncio.to_thread(dockerops.status)
                gw = await asyncio.to_thread(dockerops.container_running, "tentaclai-egress")
                for name, up in (("tentaclai", st.get("running")), ("tentaclai-egress", gw)):
                    if state.get(name, True) and not up:
                        notify.emit("container", f"{name} is not running", "Start it in tentacontrol → Overview.",
                                    key=f"container:{name}", link=notify.control_link("/"))
                    state[name] = up
                if st.get("running"):
                    logged = await asyncio.to_thread(dockerops.logged_in_claude)
                    if state.get("logged", True) and not logged:
                        notify.emit("logged_out", "Claude is signed out", "Sign in again: Overview → Sign in now.",
                                    key="logged_out", link=notify.control_link("/"))
                    if state.get("logged") is False and logged:
                        loop.create_task(discover_claude())  # a new sign-in can bring other connectors and skills
                    state["logged"] = logged
                    rc = await asyncio.to_thread(dockerops.rc_info)
                    enabled = (read_env().get("RC_ENABLED", "true") or "true").lower() == "true"
                    ok = (rc or {}).get("state") == "connected" or not enabled
                    down_since = None if ok else (down_since or time.time())
                    is_down = down_since is not None and time.time() - down_since > 180
                    if is_down and not state.get("rc_down"):
                        notify.emit("rc_down", "Remote Control is down", f"State: {(rc or {}).get('state', 'no session')}",
                                    key="rc_down", link=notify.control_link("/"))
                    state["rc_down"] = is_down
                up = updatecheck.status(TC_VERSION)
                if up.get("available") and state.get("update") != up.get("latest"):
                    notify.emit("update", f"tentaclAI {up['latest']} is available", up.get("url", ""), key=f"update:{up['latest']}")
                    state["update"] = up.get("latest")
            except Exception as e:  # noqa: BLE001
                print(f"notify watch: {e}", flush=True)
            await asyncio.sleep(60)
    loop.create_task(watch())

    async def usage_loop():
        """plan limits every 10 min (no model call), local token totals every 5 min, connectors and skills hourly;
        quota notification at 80 %"""
        warned, n = {}, 0
        await asyncio.sleep(20)
        while True:
            try:
                await asyncio.to_thread(usage.refresh_local)
                if n % 12 == 0 and dockerops.status().get("running"):
                    await discover_claude()  # hourly
                if n % 2 == 0 and dockerops.status().get("running") and await asyncio.to_thread(dockerops.logged_in_claude):
                    p = await asyncio.to_thread(usage.refresh_plan)
                    for lim in p.get("limits", []):
                        k = f"{lim['window']}:{lim['scope']}"
                        if lim["percent"] >= 80 and warned.get(k) != lim["resets"]:
                            name = "session" if lim["window"] == "session" else f"week ({lim['scope'] or 'all models'})"
                            notify.emit("quota", f"Claude {name}: {lim['percent']} % used", f"Resets {lim['resets']}.",
                                        key=f"quota:{k}:{lim['resets']}", link=notify.control_link("/usage"))
                            warned[k] = lim["resets"]
            except Exception as e:  # noqa: BLE001
                print(f"usage: {e}", flush=True)
            n += 1
            await asyncio.sleep(300)
    loop.create_task(usage_loop())


async def start_update_check():
    async def loop():
        while True:  # refresh() itself only asks GitHub when the cached answer is older than 6 h
            await asyncio.to_thread(updatecheck.refresh)
            await asyncio.sleep(1800)
    asyncio.get_running_loop().create_task(loop())


async def announce_setup():
    if not auth.has_password():
        print("=" * 72, flush=True)
        print("tentacontrol: no password set yet. One-time setup link:", flush=True)
        print(f"  https://<host>/setup?token={auth.SETUP_TOKEN}", flush=True)
        print("  (or: ./tentaclai.sh passwd)", flush=True)
        print("=" * 72, flush=True)


for _r in (system, tentacles, ssh, network, settings):  # one router per area of the UI
    app.include_router(_r.router)
