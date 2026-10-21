"""Jobs: Claude tasks on a schedule (scheduled.py) – list with results, editor, run now, pause, cancel.
The build/update jobs of tentacontrol itself are something else: routes/system.py, /builds/<id>."""
import asyncio
import time

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

import tclib

from .. import scheduled
from ..web import page, respond

router = APIRouter()


def _job_page(request, jid=None, job=None, error=None, status=200):
    if job is None and jid:
        job = scheduled.get(jid)
    resp = page(request, "job_edit.html", jid=jid, job=job or {}, error=error, profiles=scheduled.PROFILES,
                notify_rules=scheduled.NOTIFY, days=scheduled.DAYS, templates=scheduled.TEMPLATES,
                rw=scheduled.rw_tentacles(), efforts=tclib.EFFORTS)
    resp.status_code = status
    return resp


@router.get("/claude/jobs")
async def jobs_moved():
    return RedirectResponse("/jobs", status_code=301)  # links in older notifications


@router.get("/partials/job-count", response_class=HTMLResponse)
async def job_count():
    n = scheduled.unseen()
    return HTMLResponse(f'<span class="nav-badge warn">{n}</span>' if n else '<span class="nav-badge" hidden></span>')


@router.get("/jobs", response_class=HTMLResponse)
async def jobs_page(request: Request):
    scheduled.mark_seen()
    return page(request, "jobs.html", rows=scheduled.overview(), templates=scheduled.TEMPLATES,
                fmt=lambda ts: time.strftime("%Y-%m-%d %H:%M", time.localtime(ts)) if ts else "–")


@router.get("/jobs/new", response_class=HTMLResponse)
async def job_new(request: Request, template: int = -1):
    job = dict(scheduled.TEMPLATES[template]) if 0 <= template < len(scheduled.TEMPLATES) else {}
    return _job_page(request, job=dict({"profile": "read-only", "notify": "findings", "max_minutes": 20,
                                        "schedule": {"kind": "daily", "time": "07:00"}}, **job))


@router.get("/jobs/{jid}", response_class=HTMLResponse)
async def job_edit(request: Request, jid: str):
    if scheduled.get(jid) is None:
        return RedirectResponse("/jobs", status_code=303)
    return _job_page(request, jid=jid)


@router.post("/jobs/save", response_class=HTMLResponse)
async def job_save(request: Request):
    form = dict(await request.form())
    jid = form.get("jid") or None
    try:
        jid = await asyncio.to_thread(scheduled.save, form, jid)
    except (ValueError, KeyError) as e:
        return respond(request, f"Not saved: {e}", "err")
    return respond(request, "Job saved", redirect="/jobs")


@router.post("/jobs/{jid}/run", response_class=HTMLResponse)
async def job_run(request: Request, jid: str):
    try:
        scheduled.run_now(jid)
    except ValueError as e:
        return respond(request, f"Not started: {e}", "err")
    return respond(request, "Job started – the result shows up here", refresh=True)


@router.post("/jobs/{jid}/toggle", response_class=HTMLResponse)
async def job_toggle(request: Request, jid: str):
    job = scheduled.get(jid)
    if job is None:
        return respond(request, "unknown job", "err")
    scheduled.set_enabled(jid, not job.get("enabled", True))
    return respond(request, f"{job['name']} {'paused' if job.get('enabled', True) else 'on'}", refresh=True)


@router.post("/jobs/{jid}/delete", response_class=HTMLResponse)
async def job_delete(request: Request, jid: str):
    try:
        scheduled.delete(jid)
    except ValueError as e:
        return respond(request, str(e), "err")
    return respond(request, "Job deleted", redirect="/jobs")


@router.post("/jobs/runs/{rid}/cancel", response_class=HTMLResponse)
async def run_cancel(request: Request, rid: str):
    try:
        await asyncio.to_thread(scheduled.cancel, rid)
    except ValueError as e:
        return respond(request, f"Not canceled: {e}", "err")
    return respond(request, "Run canceled", refresh=True)
