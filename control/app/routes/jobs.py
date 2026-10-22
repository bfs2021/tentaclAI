"""Jobs: Claude tasks on a schedule (scheduled.py) – list with results, editor, run now, pause, cancel.
The build/update jobs of tentacontrol itself are something else: routes/system.py, /builds/<id>."""
import asyncio
import time

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

import tclib

from .. import scheduled
from ..web import page, respond, templates

router = APIRouter()


def _job_page(request, jid=None, job=None, error=None, status=200):
    if job is None and jid:
        job = scheduled.get(jid)
    known = scheduled.tentacles()
    warnings = scheduled.check_mentions(job, known)[1] if job and job.get("prompt") else []
    resp = page(request, "job_edit.html", jid=jid, job=job or {}, error=error, profiles=scheduled.PROFILES,
                notify_rules=scheduled.NOTIFY, days=scheduled.DAYS, templates=scheduled.TEMPLATES,
                rw=[x for x in scheduled.rw_tentacles()
                    if not (job or {}).get("scope") or x.removeprefix("ssh ") in scheduled.mentions(job.get("prompt"))],
                efforts=tclib.EFFORTS, warnings=warnings,
                tentacle_list=[{"name": n, "kind": t["kind"], "parts": ", ".join(t["parts"]), "on": t["enabled"]}
                               for n, t in sorted(known.items())])
    resp.status_code = status
    return resp


# ---------------------------------------------------------------- svc config put job (svc token; svc asks for the approval)

async def _put_args(request):
    from .. import activity
    if not activity.token_ok(request.headers.get("x-tc-token", "")):
        return None, JSONResponse({"error": "forbidden"}, status_code=403)
    d = await request.json()
    return (str(d.get("name", "")), d.get("definition")), None


@router.post("/internal/jobs/check")
async def internal_jobs_check(request: Request):
    args, err = await _put_args(request)
    if err:
        return err
    errors, warnings, summary = scheduled.put_preview(*args)
    return JSONResponse({"errors": errors, "warnings": warnings, "summary": summary})


@router.post("/internal/jobs/put")
async def internal_jobs_put(request: Request):
    args, err = await _put_args(request)
    if err:
        return err
    try:
        jid = await asyncio.to_thread(scheduled.put, *args)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    return JSONResponse({"id": jid, "enabled": scheduled.get(jid).get("enabled", False)})


@router.get("/claude/jobs")
async def jobs_moved():
    return RedirectResponse("/jobs", status_code=301)  # links in older notifications


@router.get("/partials/job-count", response_class=HTMLResponse)
async def job_count():
    p = scheduled.pending_proposals()
    if p:  # a decision waits – pulsing like approvals
        return HTMLResponse(f'<span class="nav-badge" title="{p} proposal(s) to pick">{p}</span>')
    n = scheduled.unseen()
    return HTMLResponse(f'<span class="nav-badge warn" title="{n} finding(s) not looked at">{n}</span>' if n else '<span class="nav-badge" hidden></span>')


@router.get("/partials/jobs-state", response_class=HTMLResponse)
async def jobs_state(v: str = ""):
    """the Jobs page polls this: a different state (a run started, finished, failed) reloads the page"""
    if v and v != scheduled.state_token():
        return HTMLResponse("", headers={"HX-Refresh": "true"})
    return HTMLResponse("")


@router.get("/jobs", response_class=HTMLResponse)
async def jobs_page(request: Request):
    scheduled.mark_seen()
    return page(request, "jobs.html", rows=scheduled.overview(), templates=scheduled.TEMPLATES, token=scheduled.state_token(), now=time.time(),
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
    warnings = scheduled.check_mentions(scheduled.get(jid))[1]
    if warnings:
        return respond(request, "Job saved – note: " + "; ".join(warnings), "warn", redirect=f"/jobs/{jid}")
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


@router.post("/jobs/{jid}/clone", response_class=HTMLResponse)
async def job_clone(request: Request, jid: str):
    try:
        new = scheduled.clone(jid)
    except ValueError as e:
        return respond(request, str(e), "err")
    return respond(request, "Copy created (paused) – adjust it and resume", redirect=f"/jobs/{new}")


@router.post("/jobs/{jid}/delete", response_class=HTMLResponse)
async def job_delete(request: Request, jid: str):
    try:
        scheduled.delete(jid)
    except ValueError as e:
        return respond(request, str(e), "err")
    return respond(request, "Job deleted", redirect="/jobs")


@router.post("/jobs/runs/{rid}/actions", response_class=HTMLResponse)
async def run_actions(request: Request, rid: str):
    picks = (await request.form()).getlist("pick")
    try:
        n = await asyncio.to_thread(scheduled.run_actions, rid, picks)
    except ValueError as e:
        return respond(request, f"Not run: {e}", "err")
    return respond(request, f"{n} proposal{'s' if n != 1 else ''} running – confirm tentacles ask for approval as usual", refresh=True)


@router.post("/jobs/runs/{rid}/discard", response_class=HTMLResponse)
async def run_discard(request: Request, rid: str, all: int = 0):
    picks = None if all else (await request.form()).getlist("pick")
    try:
        n = await asyncio.to_thread(scheduled.discard_actions, rid, picks)
    except ValueError as e:
        return respond(request, f"Not discarded: {e}", "err")
    return respond(request, f"{n} proposal{'s' if n != 1 else ''} discarded", refresh=True)


@router.get("/jobs/runs/{rid}/log", response_class=HTMLResponse)
async def run_log(request: Request, rid: str):
    run = next((r for r in scheduled.history() if r.get("id") == rid), None)
    return templates.TemplateResponse(request, "partials/job_log.html", {
        "log": scheduled.read_log(rid), "running": bool(run and run.get("status") in ("queued", "running"))})


@router.post("/jobs/runs/{rid}/cancel", response_class=HTMLResponse)
async def run_cancel(request: Request, rid: str):
    try:
        await asyncio.to_thread(scheduled.cancel, rid)
    except ValueError as e:
        return respond(request, f"Not canceled: {e}", "err")
    return respond(request, "Run canceled", refresh=True)
