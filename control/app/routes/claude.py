"""The Claude area: Instructions (site rules + what Claude sees), Memory, settings.json – Models, Skills and Usage
live in routes/settings.py and share the tabs."""
import asyncio
import json
import time

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

import tclib

from .. import claude_files, scheduled
from ..web import page, respond, sync_services

router = APIRouter()


def _lines(text):
    return len(text.splitlines()) if text else 0


@router.get("/claude/instructions", response_class=HTMLResponse)
async def instructions_page(request: Request):
    st = await asyncio.to_thread(claude_files.state)
    local = claude_files.read_local()
    memory = (st or {}).get("memory") or []
    sizes = {"base": (st or {}).get("base_lines"), "local": _lines(local), "claude_md": _lines((st or {}).get("claude_md")),
             "host_docs": (st or {}).get("host_docs_lines"), "catalog": (st or {}).get("catalog_lines"),
             "memory": len([m for m in memory if m["name"] != "MEMORY.md"])}
    return page(request, "claude_instructions.html", st=st, local=local, sizes=sizes, local_path=claude_files.LOCAL)


@router.post("/claude/instructions", response_class=HTMLResponse)
async def instructions_save(request: Request, text: str = Form("")):
    try:
        toggled = await asyncio.to_thread(claude_files.save_local, text)
    except (ValueError, OSError) as e:
        return respond(request, f"Not saved: {e}", "err")
    if toggled:
        await sync_services()  # the generated CLAUDE.md imports the file only when it has content
    return respond(request, "Site rules saved – they apply to new Claude sessions (previous version kept as .bak-…)", refresh=True)


@router.get("/claude/memory", response_class=HTMLResponse)
async def memory_page(request: Request):
    st = await asyncio.to_thread(claude_files.state)
    return page(request, "claude_memory.html", st=st, memory=(st or {}).get("memory") or [],
                fmt=lambda ts: time.strftime("%Y-%m-%d %H:%M", time.localtime(ts)) if ts else "–")


@router.post("/claude/memory/forget", response_class=HTMLResponse)
async def memory_forget(request: Request, project: str = Form(""), name: str = Form("")):
    try:
        await asyncio.to_thread(claude_files.forget, project, name)
    except ValueError as e:
        return respond(request, f"Not deleted: {e}", "err")
    return respond(request, f"{name} deleted", refresh=True)


@router.get("/claude/settings", response_class=HTMLResponse)
async def settings_json_page(request: Request):
    st = await asyncio.to_thread(claude_files.state)
    current = claude_files.load_settings((st or {}).get("settings")) if st else None
    text = json.dumps(claude_files.editable(current), indent=2, ensure_ascii=False) if current is not None else ""
    return page(request, "claude_settings.html", st=st, text=text, broken=st is not None and current is None,
                managed_top=claude_files.MANAGED_TOP, managed_env=claude_files.MANAGED_ENV, mask=claude_files.MASK)


@router.post("/claude/settings", response_class=HTMLResponse)
async def settings_json_save(request: Request, text: str = Form("")):
    try:
        await asyncio.to_thread(claude_files.save_settings, text)
    except ValueError as e:
        return respond(request, f"Not saved: {e}", "err")
    return respond(request, "settings.json saved – applies to new Claude sessions (previous version kept as .bak-…)", refresh=True)


# ---------------------------------------------------------------- scheduled jobs

def _job_page(request, jid=None, job=None, error=None, status=200):
    if job is None and jid:
        job = scheduled.get(jid)
    resp = page(request, "claude_job_edit.html", jid=jid, job=job or {}, error=error, profiles=scheduled.PROFILES,
                notify_rules=scheduled.NOTIFY, days=scheduled.DAYS, templates=scheduled.TEMPLATES,
                rw=scheduled.rw_tentacles(), efforts=tclib.EFFORTS)
    resp.status_code = status
    return resp


@router.get("/claude/jobs", response_class=HTMLResponse)
async def jobs_page(request: Request):
    return page(request, "claude_jobs.html", rows=scheduled.overview(), templates=scheduled.TEMPLATES,
                fmt=lambda ts: time.strftime("%Y-%m-%d %H:%M", time.localtime(ts)) if ts else "–")


@router.get("/claude/jobs/new", response_class=HTMLResponse)
async def job_new(request: Request, template: int = -1):
    job = dict(scheduled.TEMPLATES[template]) if 0 <= template < len(scheduled.TEMPLATES) else {}
    return _job_page(request, job=dict({"profile": "read-only", "notify": "findings", "max_minutes": 20,
                                        "schedule": {"kind": "daily", "time": "07:00"}}, **job))


@router.get("/claude/jobs/{jid}", response_class=HTMLResponse)
async def job_edit(request: Request, jid: str):
    if scheduled.get(jid) is None:
        return RedirectResponse("/claude/jobs", status_code=303)
    return _job_page(request, jid=jid)


@router.post("/claude/jobs/save", response_class=HTMLResponse)
async def job_save(request: Request):
    form = dict(await request.form())
    jid = form.get("jid") or None
    try:
        jid = await asyncio.to_thread(scheduled.save, form, jid)
    except (ValueError, KeyError) as e:
        return respond(request, f"Not saved: {e}", "err")
    return respond(request, "Job saved", redirect="/claude/jobs")


@router.post("/claude/jobs/{jid}/run", response_class=HTMLResponse)
async def job_run(request: Request, jid: str):
    try:
        scheduled.run_now(jid)
    except ValueError as e:
        return respond(request, f"Not started: {e}", "err")
    return respond(request, "Job started – the result shows up here", refresh=True)


@router.post("/claude/jobs/{jid}/toggle", response_class=HTMLResponse)
async def job_toggle(request: Request, jid: str):
    job = scheduled.get(jid)
    if job is None:
        return respond(request, "unknown job", "err")
    scheduled.set_enabled(jid, not job.get("enabled", True))
    return respond(request, f"{job['name']} {'paused' if job.get('enabled', True) else 'on'}", refresh=True)


@router.post("/claude/jobs/{jid}/delete", response_class=HTMLResponse)
async def job_delete(request: Request, jid: str):
    try:
        scheduled.delete(jid)
    except ValueError as e:
        return respond(request, str(e), "err")
    return respond(request, "Job deleted", redirect="/claude/jobs")


@router.post("/claude/runs/{rid}/cancel", response_class=HTMLResponse)
async def run_cancel(request: Request, rid: str):
    try:
        await asyncio.to_thread(scheduled.cancel, rid)
    except ValueError as e:
        return respond(request, f"Not canceled: {e}", "err")
    return respond(request, "Run canceled", refresh=True)
