"""Tentacles: services (incl. claude.ai connectors and status dots), systems and the topology."""
import asyncio
import html
import json
import os
import time

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse

from .. import connectors, dockerops, jsonstore, services_store, sshhosts, systems_store, topology
from ..config import DATA_DIR, RESTART_VARS
from ..envfile import read_env, update_env
from ..web import mark_restart, not_applied, page, respond, sync_services, toast
import tclib  # noqa: E402  shared with the sandbox tools
from .ssh import ssh_state

router = APIRouter()


@router.get("/services", response_class=HTMLResponse)
async def services_page(request: Request):
    systems = systems_store.load_all()
    services = services_store.load_all()
    like = request.query_params.get("like", "")
    like = next((x for x in services if x["name"] == like and x["can_add_mailbox"]), None)
    return page(request, "services.html", services=services, templates_list=services_store.list_templates(), like=like,
                systems=systems, system_names={x["name"] for x in systems}, connectors=connectors.listing())


@router.post("/connectors/toggle", response_class=HTMLResponse)
async def connector_toggle(request: Request, name: str = Form(...)):
    cur = {c["name"]: c for c in connectors.listing()}.get(name)
    if not cur:
        return respond(request, "Unknown connector", "err")
    ok = await asyncio.to_thread(connectors.set_enabled, name, not cur.get("enabled"))
    state = "enabled" if not cur.get("enabled") else "disabled"
    return respond(request, f"{cur['short']} {state} – applies to new Claude sessions" if ok else not_applied(),
                   "ok" if ok else "warn", refresh=True)


@router.post("/connectors/discover", response_class=HTMLResponse)
async def connector_discover(request: Request):
    ok = await asyncio.to_thread(connectors.discover)
    return respond(request, "Connectors checked" if ok else "Could not ask Claude Code (signed in?)", "ok" if ok else "warn", refresh=True)


@router.get("/topology", response_class=HTMLResponse)
async def topology_page(request: Request):
    return page(request, "topology.html")


@router.get("/topology.json")
async def topology_json():
    return JSONResponse(await asyncio.to_thread(topology.graph))


@router.get("/topology/ssh-status")
async def topology_ssh_status():
    aliases = [h["alias"] for h in sshhosts.hosts()]
    res = await asyncio.gather(*(ssh_state(a) for a in aliases))
    return JSONResponse({a: {"state": s, "title": t} for a, (s, t) in zip(aliases, res)})


LAYOUT_FILE = os.path.join(DATA_DIR, "topology-layout.json")


@router.get("/topology/layout")
async def topology_layout_get():
    try:
        with open(LAYOUT_FILE) as f:
            return JSONResponse(json.load(f))
    except (OSError, ValueError):
        return JSONResponse({})


@router.post("/topology/layout")
async def topology_layout_save(request: Request):
    """node positions after the user moved nodes: {id: {x, y}}"""
    try:
        data = await request.json()
        pos = {str(k)[:300]: {"x": float(v["x"]), "y": float(v["y"])} for k, v in data.items()}
    except (ValueError, TypeError, KeyError, AttributeError):
        return JSONResponse({"error": "invalid layout"}, status_code=400)
    if len(pos) > 5000:
        return JSONResponse({"error": "too many nodes"}, status_code=400)
    jsonstore.save(LAYOUT_FILE, pos, indent=None)
    return JSONResponse({"saved": len(pos)})


@router.delete("/topology/layout")
async def topology_layout_reset():
    try:
        os.remove(LAYOUT_FILE)
    except FileNotFoundError:
        pass
    return JSONResponse({"reset": True})


@router.get("/topology/docker/{name}")
async def topology_docker(name: str):
    if not services_store.NAME.match(name):
        return JSONResponse({"error": "invalid name"}, status_code=400)
    if not dockerops.status()["running"]:
        return JSONResponse({"error": "tentaclAI is not running"}, status_code=409)
    try:
        return JSONResponse({"stacks": await asyncio.to_thread(topology.docker_stacks, name)})
    except RuntimeError as e:
        return JSONResponse({"error": str(e)}, status_code=502)


@router.get("/systems", response_class=HTMLResponse)
async def systems_page(request: Request):
    systems = systems_store.load_all()
    services = services_store.load_all()
    names = {x["name"] for x in systems}
    members = {}
    for sv in services:
        if sv["system"] in names:
            members.setdefault(sv["system"], []).append(sv)
    return page(request, "systems.html", systems=systems, members=members,
                standalone=[sv for sv in services if sv["system"] not in names])


@router.post("/systems/new")
async def systems_new(request: Request, name: str = Form(...)):
    name = name.strip().lower()
    try:
        systems_store.create(name)
    except ValueError as e:
        return PlainTextResponse("", headers=toast(str(e), "err"))
    return respond(request, f"System {name} created – add description, SSH host and services", redirect=f"/systems/{name}")


@router.get("/systems/{name}", response_class=HTMLResponse)
async def system_edit(request: Request, name: str):
    try:
        sysd = systems_store.get(name)
    except (FileNotFoundError, ValueError):
        return RedirectResponse("/services", status_code=303)
    sysd.setdefault("description", ""); sysd.setdefault("ssh_host", ""); sysd.setdefault("notes", [])
    return page(request, "system_edit.html", sysd=sysd, services=services_store.load_all(), hosts=sshhosts.hosts(), nav="services")


@router.post("/systems/{name}")
async def system_save(request: Request, name: str):
    form = await request.form()
    try:
        systems_store.save(name, form.get("description", ""), form.get("ssh_host", ""),
                           form.get("notes", "").splitlines(), form.getlist("members"))
    except (ValueError, OSError) as e:
        return PlainTextResponse("", headers=toast(str(e), "err"))
    await sync_services()
    return respond(request, f"System {name} saved", refresh=True)


@router.post("/systems/{name}/delete")
async def system_delete(request: Request, name: str):
    try:
        systems_store.delete(name)
    except (ValueError, OSError) as e:
        return PlainTextResponse("", headers=toast(str(e), "err"))
    await sync_services()
    return respond(request, f"System {name} deleted – its services are standalone now", redirect="/systems")


@router.post("/services/new")
async def services_new(request: Request, name: str = Form(...), template: str = Form(""), address: str = Form(""),
                       like: str = Form("")):
    name = name.strip().lower()
    try:
        if like:
            services_store.create_like(name, like.strip(), address)
        else:
            services_store.create(name, template.strip(), address)
    except (ValueError, FileNotFoundError) as e:
        return PlainTextResponse("", headers=toast(str(e), "err"))
    await sync_services()
    return respond(request, f"{name} created – check URL/tokens, then “Test connection” and “Onboard with Claude”",
                   redirect=f"/services/{name}")


@router.get("/services/{name}", response_class=HTMLResponse)
async def service_edit(request: Request, name: str):
    try:
        raw = services_store.read_raw(name)
    except (FileNotFoundError, ValueError):
        return RedirectResponse("/services", status_code=303)
    try:
        data = json.loads(raw)
        err = None
    except ValueError as e:
        data, err = {}, str(e)
    svc = services_store.describe(name, data, error=err)
    users = services_store.variable_users()
    for v in svc["vars"]:
        v["shared_with"] = [n for n in users.get(v["name"], []) if n != name]
    try:
        tpl, tpl_warnings = services_store.to_template(name)
    except (ValueError, OSError):
        tpl, tpl_warnings = None, []
    return page(request, "service_edit.html", svc=svc, raw=raw, systems=systems_store.load_all(),
                suggested_test_path=services_store.template_test_path(name), tpl=tpl, tpl_warnings=tpl_warnings,
                categories=sorted({t["category"] for t in services_store.list_templates()}))


@router.post("/services/{name}/tls")
async def service_set_tls(request: Request, name: str, insecure: str = Form("")):
    try:
        services_store.set_insecure_tls(name, insecure == "1")
    except (ValueError, OSError) as e:
        return PlainTextResponse("", headers=toast(str(e), "err"))
    await sync_services()
    return respond(request, f"{name}: TLS certificates {'NOT verified' if insecure == '1' else 'verified'}",
                   kind="warn" if insecure == "1" else "ok", refresh=True)


@router.post("/services/{name}/system")
async def service_set_system(request: Request, name: str, system: str = Form("")):
    system = system.strip()
    if system and system not in {x["name"] for x in systems_store.load_all()}:
        return PlainTextResponse("", headers=toast("unknown system", "err"))
    try:
        systems_store.set_service_system(name, system)
    except (ValueError, OSError) as e:
        return PlainTextResponse("", headers=toast(str(e), "err"))
    await sync_services()
    return respond(request, f"{name} → {system or 'standalone'}", refresh=True)


@router.post("/services/{name}/json")
async def service_save_json(request: Request, name: str, raw: str = Form(...)):
    try:
        services_store.save_raw(name, raw)
    except ValueError as e:
        return PlainTextResponse("", headers=toast(f"Not saved: {e}", "err"))
    await sync_services()
    return respond(request, "Saved & synced", refresh=True)


@router.post("/services/{name}/tokens")
async def service_save_tokens(request: Request, name: str):
    form = await request.form()
    env = read_env()
    is_secret = services_store.secret_check()
    changes = {}
    for key, value in form.items():
        if key.startswith("var_"):
            k, value = key[4:], value.strip()
            if is_secret(k):
                if value:
                    changes[k] = value
            elif env.get(k, "") != value:
                changes[k] = value
        elif key.startswith("clear_"):
            changes[key[6:]] = ""
    if not changes:
        return PlainTextResponse("", headers=toast("No changes", "info"))
    try:
        update_env(changes)
    except ValueError as e:
        return PlainTextResponse("", headers=toast(str(e), "err"))
    if RESTART_VARS & set(changes):
        mark_restart(env, changes)
    await sync_services()
    return respond(request, "Token saved", refresh=True)


@router.post("/services/{name}/toggle")
async def service_toggle(request: Request, name: str):
    svc = [s for s in services_store.load_all() if s["name"] == name]
    if not svc:
        return PlainTextResponse("", status_code=404)
    services_store.set_enabled(name, not svc[0]["enabled"])
    await sync_services()
    return respond(request, "Status changed", refresh=True)


@router.post("/services/{name}/delete")
async def service_delete(request: Request, name: str):
    try:
        services_store.delete(name)
    except (FileNotFoundError, ValueError) as e:
        return PlainTextResponse("", headers=toast(str(e), "err"))
    await sync_services()
    return respond(request, f"{name} deleted", redirect="/services")


@router.post("/services/{name}/rename")
async def service_rename(request: Request, name: str, new: str = Form("")):
    new = new.strip().lower()
    try:
        data = json.loads(services_store.read_raw(name))
    except (FileNotFoundError, ValueError) as e:
        return respond(request, f"Not renamed: {e}", "err")
    git = data.get("git") if isinstance(data.get("git"), dict) else None
    if git and not dockerops.status().get("running"):
        return respond(request, "Not renamed: start tentaclAI first – the git working copy and mirror move inside the sandbox", "err")
    try:
        services_store.rename(name, new)
    except (ValueError, OSError) as e:
        return respond(request, f"Not renamed: {e}", "err")
    moved = ""
    if git:  # svc's mirror belongs to root, the working copy to Claude – each moved by its owner
        mv = ["sh", "-c", '[ ! -e "$1" ] || [ -e "$2" ] || mv "$1" "$2"', "mv"]
        await asyncio.to_thread(dockerops.run, mv + [f"/root/.cache/tentaclai/git/{name}.git", f"/root/.cache/tentaclai/git/{new}.git"])
        if not git.get("path"):
            await asyncio.to_thread(dockerops.run, mv + [f"/workspace/repos/{name}", f"/workspace/repos/{new}"], user=dockerops.CLAUDE_USER)
            moved = f", repo now in /workspace/repos/{new}"
    await sync_services()
    return respond(request, f"Renamed {name} → {new}{moved} – Claude calls it svc {new} now", redirect=f"/services/{new}")


@router.post("/services/{name}/test", response_class=HTMLResponse)
async def service_test(request: Request, name: str, path: str = Form("/")):
    if not services_store.valid_test_path(path):
        return HTMLResponse('<span class="pill err">invalid path</span>')
    code, out = await asyncio.to_thread(dockerops.run, ["svc", name, "GET", path, "-o", "/dev/null", "-w", "%{http_code}", "--max-time", "10"])
    out = out.strip()
    if code == 0 and out.isdigit():
        kind = "ok" if out.startswith("2") else "warn" if out.startswith(("3", "4")) else "err"
        return HTMLResponse(f'<span class="pill {kind}">HTTP {out}</span>')
    return HTMLResponse(f'<span class="pill err">{html.escape((out or "error")[-160:])}</span>')


@router.post("/services/{name}/test-path")
async def service_test_path(request: Request, name: str, path: str = Form("")):
    try:
        services_store.set_test_path(name, path)
    except (ValueError, OSError) as e:
        return respond(request, f"Not saved: {e}", "err")
    return respond(request, f"{name}: svc test now checks GET {path}", refresh=True)


@router.post("/services/{name}/git-sync")
async def service_git_sync(request: Request, name: str):
    code, out = await asyncio.to_thread(dockerops.run, ["svc", "git", "sync", name])
    line = (out.strip().splitlines() or [""])[-1].strip()
    ok = code == 0 and "✓" in out
    return respond(request, line or "git sync done", "ok" if ok else "err", refresh=ok)


@router.post("/services/sync")
async def services_sync_route(request: Request):
    code, out = await sync_services()
    return respond(request, "svc sync done" if code == 0 else f"svc sync: {out[-120:]}",
                   "ok" if code == 0 else "err", refresh=True)


_SVC_STATUS = {}            # name -> (cache key, time, state, title)


_SVC_SEM = asyncio.Semaphore(4)  # at most 4 connection tests at the same time


def _svc_cache_key(name):
    """changes when the service file or .env changes → stale results are dropped"""
    try:
        return (os.path.getmtime(services_store.path_of(name)), os.path.getmtime(os.path.join(services_store.STACK_DIR, ".env")))
    except (OSError, ValueError):
        return None


async def service_state(name, force=False):
    """('ok'|'warn'|'err'|'', title) from svc test – cached for 10 minutes"""
    try:
        data = json.loads(services_store.read_raw(name))
    except (OSError, ValueError):
        return "err", "invalid service file"
    d = services_store.describe(name, data)
    if d["state"] == "off":
        return "", "disabled"
    if d["state"] == "warn":
        return "warn", "token missing: " + ", ".join(d["missing"])
    key = _svc_cache_key(name)
    hit = _SVC_STATUS.get(name)
    if hit and not force and hit[0] == key and time.time() - hit[1] < 600:
        return hit[2], hit[3]
    async with _SVC_SEM:
        code, out = await asyncio.to_thread(dockerops.run, ["svc", "test", name])
    lines = [l.strip() for l in out.strip().splitlines() if l.strip().startswith(("✓", "✗"))]
    good, bad = sum(l.startswith("✓") for l in lines), sum(l.startswith("✗") for l in lines)
    if not lines:
        state = "" if "not running" in out else "err"
        title = (out.strip().splitlines() or ["no answer"])[-1]
    else:
        state = "ok" if not bad else ("warn" if good else "err")
        title = " · ".join(lines)
    title = f"{title} (checked {time.strftime('%H:%M')})"
    _SVC_STATUS[name] = (key, time.time(), state, title)
    return state, title


@router.get("/services/{name}/status", response_class=HTMLResponse)
async def service_status_dot(name: str):
    if not services_store.NAME.match(name):
        return HTMLResponse('<span class="dot"></span>')
    state, title = await service_state(name)
    return HTMLResponse(f'<span class="dot {state}" title="{html.escape(title)}"></span>')


@router.post("/services/{name}/svc-test", response_class=HTMLResponse)
async def service_svc_test(name: str):
    if not services_store.NAME.match(name):
        return HTMLResponse('<span class="pill err">invalid</span>')
    _SVC_STATUS.pop(name, None)  # the next status dot reflects this test run
    code, out = await asyncio.to_thread(dockerops.run, ["svc", "test", name])
    from html import escape
    lines = "".join(f'<div class="{"ok" if l.startswith("✓") else "err" if l.startswith("✗") else "muted"}">{escape(l)}</div>'
                    for l in out.strip().splitlines())
    return HTMLResponse(f'<div class="testout mono small">{lines or escape(out or "no output")}</div>')
