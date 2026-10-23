"""Settings → Export / Import (backup.py) and the template export/import of a single service."""
import asyncio
import json
import time

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, PlainTextResponse, Response

from .. import backup, services_store
from ..config import RESTART_VARS
from ..envfile import read_env, update_env
from ..web import mark_restart, page, respond, sync_services, templates, toast

router = APIRouter()


def _attachment(data, filename):
    return Response(json.dumps(data, ensure_ascii=False, indent=2) + "\n", media_type="application/json",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"', "Cache-Control": "no-store"})


@router.get("/backup", response_class=HTMLResponse)
async def backup_page(request: Request):
    return page(request, "backup.html", sections=backup.sections(), left_out=backup.literal_secrets(),
                pending=backup.pending() is not None)


@router.get("/backup/export")
async def backup_export(request: Request):
    parts = request.query_params.getlist("part") or list(backup.sections())
    doc, _ = await asyncio.to_thread(backup.export, parts)
    host = "".join(c for c in (doc.get("host") or "tentaclai") if c.isalnum() or c in "-_.") or "tentaclai"
    return _attachment(doc, f"tentaclai-{host}-{time.strftime('%Y-%m-%d')}.json")


def _preview(request, doc, error=None):
    rows = backup.preview(doc) if doc else {}
    return templates.TemplateResponse(request, "partials/backup_preview.html", {
        "doc": doc, "rows": rows, "sections": dict(backup.sections(), **{s: s for s in rows if s not in backup.sections()}), "error": error,
        "counts": {s: {st: sum(1 for r in rs if r["status"] == st) for st in ("new", "changed", "same", "invalid")} for s, rs in rows.items()}})


@router.post("/backup/import/preview", response_class=HTMLResponse)
async def backup_preview(request: Request, file: UploadFile = File(...)):
    raw = await file.read(backup.MAX_SIZE + 1)
    try:
        doc = backup.parse(raw)
    except ValueError as e:
        return _preview(request, None, str(e))
    backup.keep(doc)
    return _preview(request, doc)


@router.get("/backup/import/preview", response_class=HTMLResponse)
async def backup_preview_again(request: Request):
    """the kept upload again (e.g. after a reload) – empty when there is none"""
    doc = backup.pending()
    return _preview(request, doc, None if doc else "No import waiting – upload the file again.")


@router.post("/backup/import/apply", response_class=HTMLResponse)
async def backup_apply(request: Request):
    form = await request.form()
    doc = backup.pending()
    if not doc:
        return PlainTextResponse("", headers=toast("The upload expired – upload the file again", "err"))
    picked = set(form.getlist("pick"))
    if not picked:
        return PlainTextResponse("", headers=toast("Nothing picked", "info"))
    old_env = read_env()
    results, missing = await asyncio.to_thread(backup.apply, doc, picked)
    backup.drop_pending()
    touched = {r["id"].split(":", 1)[0] for r in results if r["ok"]}
    if touched & {"ssh_hosts", "systems", "services", "instructions", "skills"}:
        await sync_services()
    changed_settings = {r["id"].split(":", 1)[1] for r in results if r["ok"] and r["id"].startswith("settings:")}
    if RESTART_VARS & changed_settings:
        mark_restart(old_env, changed_settings)
    is_secret = services_store.secret_check()
    return templates.TemplateResponse(request, "partials/backup_result.html", {
        "results": results, "missing": [{"name": v, "secret": is_secret(v)} for v in missing],
        "ssh": "ssh_hosts" in touched, "restart": bool(RESTART_VARS & changed_settings)})


@router.post("/backup/variables", response_class=HTMLResponse)
async def backup_variables(request: Request):
    """fill the variables an import left empty – only those, nothing else in .env"""
    form = await request.form()
    allowed = set(backup.missing({}))
    changes = {k[2:]: v.strip() for k, v in form.items() if k.startswith("k_") and k[2:] in allowed and v.strip()}
    if not changes:
        return PlainTextResponse("", headers=toast("No values entered", "info"))
    try:
        update_env(changes)
    except ValueError as e:
        return PlainTextResponse("", headers=toast(str(e), "err"))
    await sync_services()
    return respond(request, f"{len(changes)} variable(s) saved", redirect="/backup")


# ---------------------------------------------------------------- one service as a template

@router.get("/services/{name}/template")
async def service_template(request: Request, name: str, title: str = "", category: str = "", setup: str = ""):
    try:
        tpl, _ = services_store.to_template(name, title, category, setup)
    except (ValueError, OSError) as e:
        return PlainTextResponse(f"No template: {e}", status_code=400)
    return _attachment(tpl, f"{name}.json")


@router.post("/services/template-import")
async def template_import(request: Request, file: UploadFile = File(...), name: str = Form("")):
    raw = await file.read(256 * 1024 + 1)
    if len(raw) > 256 * 1024:
        return PlainTextResponse("", headers=toast("file too large", "err"))
    tid = name.strip() or (file.filename or "").rsplit("/", 1)[-1].removesuffix(".json")
    try:
        tid, existed = services_store.save_template(tid.lower(), raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as e:
        return PlainTextResponse("", headers=toast(f"Not imported: {e}", "err"))
    return respond(request, f"Template {tid} {'replaced' if existed else 'added'} – pick it under “Add service”", refresh=True)
