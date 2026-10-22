"""Shared by tentacontrol's routes: templates, page rendering, htmx answers, the "Apply now" hint, svc sync."""
import asyncio
import json
import os
import time

from fastapi.responses import PlainTextResponse
from fastapi.templating import Jinja2Templates

from . import dockerops, license, services_store, updatecheck
from .config import DATA_DIR, RESTART_VARS, site
from .envfile import read_env

BASE = os.path.dirname(__file__)
templates = Jinja2Templates(directory=os.path.join(BASE, "templates"))
# installed tentaclAI version (VERSION file baked into the image)
try:
    TC_VERSION = open(os.path.join(os.path.dirname(BASE), "VERSION")).read().strip()
except OSError:
    TC_VERSION = "dev"
templates.env.globals["tc_version"] = TC_VERSION
# cache busting: static URLs carry the newest mtime of app.css/app.js, so browsers load a new version right after an update
templates.env.globals["asset_v"] = str(int(max(os.path.getmtime(os.path.join(BASE, "static", f)) for f in ("app.css", "app.js", "logo.svg"))))


RESTART_FLAG = os.path.join(DATA_DIR, "restart-needed")


def page(request, name, **ctx):
    st = dockerops.status()
    newer = dockerops.newer_image() if st.get("exists") else False
    ctx.update(request=request, nav=name.split(".")[0], cstatus=st, restart_needed=restart_needed(st) or newer, newer_image=newer, site=site(),
               update=updatecheck.status(TC_VERSION), reports_unlocked=license.has("reports"),
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


def restart_needed(st=None):
    st = st or dockerops.status()
    if not st.get("exists"):
        return False
    env = read_env()
    return any(env.get(k, "") != v for k, v in _baseline(st).items())


def mark_restart(old_env, changed):
    """remember the pre-change values, so reverting a setting clears the hint again"""
    st = dockerops.status()
    base = _baseline(st)
    for k in RESTART_VARS & set(changed):
        base.setdefault(k, old_env.get(k, ""))
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(RESTART_FLAG, "w") as f:
        json.dump({"ts": time.time(), "baseline": base}, f)


def not_applied():
    """why a change could not be applied in the sandbox right away (it is saved and applied on the next start)"""
    v = dockerops.sandbox_version()
    if v is None:
        return "Saved – the sandbox is not running, applied on its next start"
    if v != TC_VERSION:
        return f"Saved – the sandbox still runs {v} (tentacontrol {TC_VERSION}): recreate it with “Apply now” to use it"
    return "Saved, but applying it in the sandbox failed – applied on its next start (see Logs)"


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


async def sync_services():
    """after any change to services/: svc sync (MCP entries, git repos, catalog, Claude's instructions)"""
    return await asyncio.to_thread(dockerops.run, ["svc", "sync"])
