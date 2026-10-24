"""Enterprise (sidebar): enroll at / leave a central server, its profile, locks and what it reports (central.py)."""
import asyncio

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from .. import auditlog, central
from ..web import page, respond, sync_services

router = APIRouter()
SYNCED = {"ssh_hosts", "systems", "services", "instructions", "skills"}  # parts svc copies into the sandbox


@router.get("/central", response_class=HTMLResponse)
async def central_page(request: Request):
    return page(request, "central.html", nav="enterprise", c=central.view())


async def _sync():
    report = await asyncio.to_thread(central.sync)
    if report and set(report["touched"]) & SYNCED:
        await sync_services()
    return report


@router.post("/central/enroll", response_class=HTMLResponse)
async def central_enroll(request: Request):
    form = await request.form()
    try:
        st = await asyncio.to_thread(central.enroll, form.get("code", ""), form.get("name", ""), form.getlist("areas"),
                                     form.get("ca", ""))
    except central.CentralError as e:
        auditlog.user(request, "admin", "central", "central: enrolling failed", "failed", detail=str(e)[:300])
        return respond(request, str(e), "err")
    try:
        await _sync()
    except central.CentralError:
        pass  # enrolled – the background sync tries again
    return respond(request, f"Enrolled at {st['central_name']}", refresh=True)


@router.post("/central/sync", response_class=HTMLResponse)
async def central_sync(request: Request):
    try:
        await _sync()
    except central.CentralError as e:
        return respond(request, str(e), "err")
    return respond(request, "Synchronised", refresh=True)


@router.post("/central/areas", response_class=HTMLResponse)
async def central_areas(request: Request):
    form = await request.form()
    try:
        await asyncio.to_thread(central.set_areas, form.getlist("areas"))
        await _sync()
    except central.CentralError as e:
        return respond(request, str(e), "err", refresh=True)
    return respond(request, "Saved – the server's profile is applied to these parts", refresh=True)


@router.post("/central/banner/dismiss", response_class=HTMLResponse)
async def central_banner_dismiss(request: Request):
    """× on the server's banner: gone until the next sign-in (the session remembers it)"""
    b = central.banner()
    if b:
        request.session["banner_dismissed"] = b["id"]
    return HTMLResponse("<!-- closed -->")  # not empty: an empty answer isn't swapped in (app.js)


@router.post("/central/leave", response_class=HTMLResponse)
async def central_leave(request: Request):
    try:
        await asyncio.to_thread(central.leave)
    except central.CentralError as e:
        return respond(request, str(e), "err")
    return respond(request, "Left central management – the values it set stay, nothing is locked any more", refresh=True)
