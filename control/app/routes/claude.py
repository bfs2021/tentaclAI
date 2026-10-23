"""The Claude area: Instructions (site rules + what Claude sees), Memory, settings.json – Models, Skills and Usage
live in routes/settings.py and share the tabs. Scheduled jobs have their own area (routes/jobs.py)."""
import asyncio
import json
import time

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse

from .. import claude_files, knowledge, scheduled
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
    know = await asyncio.to_thread(knowledge.sizes)
    return page(request, "claude_instructions.html", st=st, local=local, sizes=sizes, local_path=claude_files.LOCAL, know=know,
                cleanup_template=next(i for i, t in enumerate(scheduled.TEMPLATES) if t["name"] == "Knowledge cleanup"))


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

