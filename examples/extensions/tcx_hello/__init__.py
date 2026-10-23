"""Hello – the example extension of tentaclAI (docs/extensions.md).

Shows every hook of Extension API v1: a page with a sidebar entry and a badge, a Settings tab, an icon, a static file,
a background task, a notification event, an audit hook, a Help section and a part of Export / Import.
Try it: copy this folder to <stack>/local/extensions/tcx_hello, enable it on the Modules page, restart tentacontrol.
"""
import os

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, PlainTextResponse

from app import extapi  # the only part of tentacontrol an extension imports

NAME = "tcx_hello"
REQUIRES_API = 1
HERE = os.path.dirname(__file__)
BASE = f"/x/{NAME}"  # every route of a built-in or third-party extension lives below /x/<name>/

router = APIRouter(prefix=BASE)
_seen = {"entries": 0}  # audit entries since tentacontrol started (on_audit)


def settings():
    return extapi.load_json(NAME, "settings.json", {"greeting": "Hello"})


def state():
    return extapi.load_json(NAME, "state.json", {"ticks": 0})


@router.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return extapi.page(request, f"{NAME}/index.html", greeting=settings()["greeting"], ticks=state()["ticks"],
                       seen=_seen["entries"], edition=extapi.edition())


@router.get("/count", response_class=PlainTextResponse)
async def count():
    """the badge next to the sidebar entry"""
    return str(state()["ticks"])


@router.post("/ping", response_class=HTMLResponse)
async def ping(request: Request):
    extapi.notify("hello", f"{settings()['greeting']} from tentacontrol", "Sent by the example module.",
                  link=extapi.control_link(BASE + "/"))
    return extapi.respond(request, "Notification sent (if the event Hello is routed to a channel)")


@router.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    return extapi.page(request, f"{NAME}/settings.html", nav="settings", greeting=settings()["greeting"])


@router.post("/settings", response_class=HTMLResponse)
async def settings_save(request: Request, greeting: str = Form("")):
    # tentacontrol logs the change in the audit log itself (every POST of a signed-in user)
    extapi.save_json(NAME, "settings.json", {"greeting": greeting.strip()[:60] or "Hello"})
    return extapi.respond(request, "Saved", refresh=True)


def tick():
    """background task: counts up once a minute"""
    s = state()
    s["ticks"] += 1
    extapi.save_json(NAME, "state.json", s)


def on_entry(entry):
    _seen["entries"] += 1


def restore(data):
    extapi.save_json(NAME, "settings.json", {"greeting": str((data or {}).get("greeting") or "Hello")[:60]})


def register(api):
    api.add_router(router)
    api.add_templates(os.path.join(HERE, "templates"))  # used as "tcx_hello/<file>"
    api.add_static(os.path.join(HERE, "static"))        # served at /x/tcx_hello/static/
    api.add_icon("wave", '<path d="M3 12c2-4 4-4 6 0s4 4 6 0 4-4 6 0"/>')
    api.add_module("hello", "Hello", "The example module of the Extension API.",
                   nav={"href": BASE + "/", "label": "Hello", "icon": "wave"}, count_url=BASE + "/count")
    api.add_tab("settings", BASE + "/settings", "Hello", icon="wave")
    api.add_help_section("about", "Hello (example module)", f"{NAME}/help.html")
    api.add_task(tick, every=60)
    api.add_event("hello", "Hello", "low", "the example module says hello (button on its page)")
    api.on_audit(on_entry)
    api.add_backup_part("settings", "Hello settings", export=settings, apply=restore)
