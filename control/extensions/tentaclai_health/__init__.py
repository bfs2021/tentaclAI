"""Service-Health – a built-in module of tentaclAI: tests every connected service on a schedule (svc test), keeps
7 days of history, shows availability per service and system and notifies when a service goes down or comes back.

Uses the public Extension API only (docs/extensions.md) – a working example next to examples/extensions/tcx_hello.
"""
import asyncio
import os

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from app import extapi

from . import checks

NAME = checks.NAME
REQUIRES_API = 1
HERE = os.path.dirname(__file__)
BASE = f"/x/{NAME}"

router = APIRouter(prefix=BASE)


@router.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return extapi.page(request, f"{NAME}/index.html", **checks.overview(), intervals=checks.INTERVALS)


@router.get("/count", response_class=HTMLResponse)
async def count():
    n = checks.down_count()
    return HTMLResponse(f'<span class="nav-badge" style="background:var(--err);color:#fff;animation:none" '
                        f'title="{n} service(s) down">{n}</span>' if n else '<span class="nav-badge" hidden></span>')


@router.post("/run", response_class=HTMLResponse)
async def run_now(request: Request):
    if not checks.monitored()[0]:
        return extapi.respond(request, "No service to check", "err")
    done = await asyncio.to_thread(checks.run_round, True)
    if not done:
        return extapi.respond(request, "A check is running already – or the sandbox is not running", "err")
    return extapi.respond(request, "Checked", refresh=True)


@router.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    checked, other = checks.monitored()
    services = sorted(checked + [s for s in other if s["why"] == "not monitored"], key=lambda s: s["name"])
    return extapi.page(request, f"{NAME}/settings.html", nav="settings", s=checks.settings(), services=services,
                       other=[s for s in other if s["why"] != "not monitored"], intervals=checks.INTERVALS,
                       fails=checks.FAILS)


@router.post("/settings", response_class=HTMLResponse)
async def settings_save(request: Request):
    form = await request.form()
    offered = {s["name"] for s in extapi.services() if s["state"] == "ok"}  # the checkboxes of the form
    skip = (set(checks.settings()["skip"]) - offered) | (offered - set(form.getlist("watch")))
    try:
        checks.save_settings(int(form.get("interval", 5)), int(form.get("fails", 2)), sorted(skip))
    except ValueError:
        return extapi.respond(request, "Invalid value", "err")
    return extapi.respond(request, "Saved", refresh=True)


def tick():
    """every minute: a round when the interval has passed"""
    checks.run_round()


def restore(data):
    data = data if isinstance(data, dict) else {}
    s = checks.DEFAULTS | {k: data[k] for k in ("interval", "fails") if k in data}
    try:
        checks.save_settings(int(s["interval"]), int(s["fails"]), [str(x) for x in data.get("skip") or []])
    except (TypeError, ValueError):
        checks.save_settings(checks.DEFAULTS["interval"], checks.DEFAULTS["fails"], [])


def register(api):
    api.add_router(router)
    api.add_templates(os.path.join(HERE, "templates"))
    api.add_static(os.path.join(HERE, "static"))
    api.add_icon("pulse", '<path d="M3 12h4l2-6 4 12 2-6h6"/>')
    api.add_module("health", "Service-Health", "Tests every service on a schedule, shows availability and notifies on outages.",
                   nav={"href": BASE + "/", "label": "Health", "icon": "pulse"}, count_url=BASE + "/count")
    api.add_tab("settings", BASE + "/settings", "Health", icon="pulse")
    api.add_help_section("about", "Service-Health", f"{NAME}/help.html")
    api.add_task(tick, every=60, delay=60)
    api.add_event("health_down", "Service down", "high", "a monitored service failed its test several times in a row (Service-Health)")
    api.add_event("health_up", "Service up again", "normal", "a service reported down passes its test again (Service-Health)")
    api.add_backup_part("settings", "Service-Health settings", export=checks.settings, apply=restore)
