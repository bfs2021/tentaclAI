"""Tentacles → SSH hosts: add (host key, key deployment), edit, rename, remove, status."""
import asyncio
import html
import json
import re
import time

import tclib

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from .. import dockerops, services_store, sshhosts, sshops
from ..envfile import read_env
from ..web import page, respond, sync_services, templates

router = APIRouter()


@router.get("/ssh", response_class=HTMLResponse)
async def ssh_page(request: Request):
    return page(request, "ssh.html", hosts=sshhosts.hosts(), pubkey=sshhosts.public_key())


def _ssh_fields(form):
    f = {k: (form.get(k) or "").strip() for k in ("alias", "hostname", "user", "port", "proxyjump", "sudo", "access", "docker")}
    f["access"] = f["access"] or "confirm"
    return f


def _ssh_dialog(request, step, **ctx):
    ctx.setdefault("f", {})
    ctx.setdefault("hosts", sshhosts.hosts())
    ctx.setdefault("error", None)
    ctx.setdefault("this_host", read_env().get("TC_HOSTNAME") or "")
    return templates.TemplateResponse(request, "partials/ssh_dialog.html",
                                      {"request": request, "step": step, **ctx})


async def _ssh_test_results(alias):
    """(ok, text) lines for the result step; ok None = warning"""
    _SSH_STATUS.clear()
    sudo = sshhosts.sudo_of(alias)
    code, out = await asyncio.to_thread(dockerops.ssh_check, alias, sudo)
    lines = [l for l in out.strip().splitlines() if l.strip()]
    if code != 0:
        last = lines[-1] if lines else "no answer"
        return [(False, f"Login failed: {last}")], ("host key" in out.lower() or "identification has changed" in out.lower())
    res = [(True, f"Login works: {next((l for l in lines if '@' in l), alias)}")]
    if sudo:
        res.append((True, f"{sudo} works") if "SUDO_OK" in lines else (None, f"Login works, but {sudo} fails – the user needs passwordless sudo"))
    return res, False


async def _docker_host(alias):
    """the "Docker host" box of Add host: is docker there (with the host's sudo prefix), where do its stacks live –
    then a docker service for the alias (access confirm) → result lines"""
    sudo = sshhosts.sudo_of(alias)
    pre = f"{sudo} " if sudo else ""
    code, out = await asyncio.to_thread(dockerops.ssh_run, alias, pre + "docker version --format '{{.Server.Version}}'")
    version = out.strip().splitlines()[-1] if out.strip() else ""
    if code != 0 or not re.fullmatch(r"[0-9][\w.+-]*", version):
        hint = "add a sudo prefix (Edit host)" if not sudo else f"check that {sudo} docker works"
        return [(None, f"No Docker service created: docker doesn't answer for this user ({version or 'no output'}) – {hint}, "
                       "then add one from the template “Docker host (SSH)”")]
    code, out = await asyncio.to_thread(dockerops.ssh_run, alias, pre + "docker compose ls --all --format json")
    stacks, n, total = tclib.stacks_dir_guess(out if code == 0 else "")
    name = tclib.service_name_for(alias)
    res = [(True, f"Docker {version} answers")]
    if any(s["name"] == name for s in services_store.load_all()):
        return res + [(None, f"A service named {name} exists already – not changed")]
    try:
        services_store.create(name, "docker-ssh")
        data = json.loads(services_store.read_raw(name))
        data["description"] = f"Docker on {alias}"
        data["docker"].update(ssh_host=alias, stacks_dir=stacks or data["docker"].get("stacks_dir", ""), access="confirm")
        data["notes"] = [x for x in data.get("notes", []) if not x.startswith(("Setup:", "Default is read"))]
        services_store.save_raw(name, json.dumps(data, ensure_ascii=False))
    except (ValueError, OSError, KeyError) as e:
        return res + [(False, f"Docker service not created: {e}")]
    where = (f"stacks in {stacks} ({n} of {total} compose projects)" if stacks else
             f"no common stacks directory found ({total} compose projects) – set stacks_dir on the service")
    return res + [(True if stacks else None, where),
                  (True, f"Service {name} created – access confirm: Claude reads freely, every change waits for your approval. "
                         f"Try it: svc docker {name} ps")]


@router.get("/ssh/new", response_class=HTMLResponse)
async def ssh_new(request: Request):
    return _ssh_dialog(request, "form")


@router.post("/ssh/new/form", response_class=HTMLResponse)
async def ssh_new_back(request: Request):
    return _ssh_dialog(request, "form", f=_ssh_fields(await request.form()))


@router.post("/ssh/new/scan", response_class=HTMLResponse)
async def ssh_new_scan(request: Request):
    f = _ssh_fields(await request.form())
    try:
        sshhosts.validate(f, aliases={h["alias"] for h in sshhosts.hosts()})
        scan = await asyncio.to_thread(sshops.scan, f)
    except (ValueError, RuntimeError) as e:
        return _ssh_dialog(request, "form", f=f, error=str(e))
    return _ssh_dialog(request, "hostkey", f=f, scan=scan, action="/ssh/new/trust", edit=False)


@router.post("/ssh/new/trust", response_class=HTMLResponse)
async def ssh_new_trust(request: Request):
    form = await request.form()
    f = _ssh_fields(form)
    try:
        await asyncio.to_thread(sshops.trust, form.get("token", ""))
    except RuntimeError as e:
        return _ssh_dialog(request, "form", f=f, error=str(e))
    return _ssh_dialog(request, "deploy", f=f, pubkey=sshhosts.public_key())


@router.post("/ssh/new/deploy", response_class=HTMLResponse)
async def ssh_new_deploy(request: Request):
    form = await request.form()
    f = _ssh_fields(form)
    results = []
    if form.get("mode") == "password":
        if not form.get("password"):
            return _ssh_dialog(request, "deploy", f=f, pubkey=sshhosts.public_key(), error="Enter the password, or use “The key is already there”.")
        try:
            state = await asyncio.to_thread(sshops.deploy, f, form.get("password"))
        except RuntimeError as e:
            return _ssh_dialog(request, "deploy", f=f, pubkey=sshhosts.public_key(), error=str(e))
        results.append((True, "Key added to authorized_keys" if state == "added" else "Key was already authorized"))
    try:
        if not sshhosts.get(f["alias"]):
            sshhosts.add(f)
    except ValueError as e:
        return _ssh_dialog(request, "form", f=f, error=str(e))
    results.append((True, f"Saved as ssh/config.d/{f['alias']}.conf"))
    test, _ = await _ssh_test_results(f["alias"])
    if form.get("docker") and test and test[0][0] is True:
        test += await _docker_host(f["alias"])
    await sync_services()
    return _ssh_dialog(request, "result", title=f"{f['alias']} added", results=results + test)


@router.get("/ssh/{alias}/edit", response_class=HTMLResponse)
async def ssh_edit(request: Request, alias: str):
    h = sshhosts.get(alias)
    if not h:
        return _ssh_dialog(request, "result", title="Not found", results=[(False, f"host {alias} not found")])
    return _ssh_dialog(request, "edit", alias=alias, f=h, refs=sshops.references(alias))


@router.post("/ssh/{alias}/edit", response_class=HTMLResponse)
async def ssh_edit_save(request: Request, alias: str):
    old = sshhosts.get(alias)
    if not old:
        return _ssh_dialog(request, "result", title="Not found", results=[(False, f"host {alias} not found")])
    f = dict(old, **_ssh_fields(await request.form()))
    try:
        sshhosts.update(alias, f)
    except ValueError as e:
        return _ssh_dialog(request, "edit", alias=alias, f=f, refs=sshops.references(alias), error=str(e))
    results = [(True, "Saved")]
    if f["alias"] != alias:
        refs = sshops.references(alias)
        await asyncio.to_thread(sshops.rename_references, alias, f["alias"])
        results.append((True, f"Renamed {alias} → {f['alias']}" + (f", updated {len(refs)} reference(s)" if refs else "")))
    test, hostkey = await _ssh_test_results(f["alias"])
    await sync_services()
    return _ssh_dialog(request, "result", title=f"{f['alias']} saved", results=results + test,
                       hostkey_alias=f["alias"] if hostkey else None)


@router.post("/ssh/{alias}/hostkey/scan", response_class=HTMLResponse)
async def ssh_hostkey_scan(request: Request, alias: str):
    h = sshhosts.get(alias)
    if not h:
        return _ssh_dialog(request, "result", title="Not found", results=[(False, f"host {alias} not found")])
    try:
        scan = await asyncio.to_thread(sshops.scan, h)
    except RuntimeError as e:
        return _ssh_dialog(request, "result", title=f"Host key of {alias}", results=[(False, str(e))])
    return _ssh_dialog(request, "hostkey", f=h, scan=scan, action=f"/ssh/{alias}/hostkey/trust", edit=True)


@router.post("/ssh/{alias}/hostkey/trust", response_class=HTMLResponse)
async def ssh_hostkey_trust(request: Request, alias: str):
    form = await request.form()
    try:
        await asyncio.to_thread(sshops.trust, form.get("token", ""))
    except RuntimeError as e:
        return _ssh_dialog(request, "result", title=f"Host key of {alias}", results=[(False, str(e))])
    test, hostkey = await _ssh_test_results(alias)
    return _ssh_dialog(request, "result", title=f"Host key of {alias} confirmed", results=[(True, "Host key stored")] + test,
                       hostkey_alias=alias if hostkey else None)


@router.get("/ssh/{alias}/remove", response_class=HTMLResponse)
async def ssh_remove(request: Request, alias: str):
    h = sshhosts.get(alias)
    if not h:
        return _ssh_dialog(request, "result", title="Not found", results=[(False, f"host {alias} not found")])
    return _ssh_dialog(request, "remove", alias=alias, f=h, refs=sshops.references(alias))


@router.post("/ssh/{alias}/remove", response_class=HTMLResponse)
async def ssh_remove_do(request: Request, alias: str):
    form = await request.form()
    h = sshhosts.get(alias)
    if not h:
        return _ssh_dialog(request, "result", title="Not found", results=[(False, f"host {alias} not found")])
    results = []
    if form.get("unauthorize") and not form.get("force"):
        try:
            await asyncio.to_thread(sshops.unauthorize, alias)
            results.append((True, f"Key removed from {h['user']}@{h['hostname']}"))
        except RuntimeError as e:
            return _ssh_dialog(request, "remove", alias=alias, f=h, refs=sshops.references(alias),
                               error=f"Could not remove the key on the host: {e}")
    try:
        sshhosts.remove(alias)
    except ValueError as e:
        return _ssh_dialog(request, "remove", alias=alias, f=h, refs=sshops.references(alias), error=str(e))
    results.append((True, f"{alias} removed from ssh/config.d/{h['file']}"))
    refs = sshops.references(alias)
    if refs:
        results.append((None, "Still referenced by " + ", ".join(f"{k} {n}" for k, n in refs)))
    _SSH_STATUS.clear()
    await sync_services()
    return _ssh_dialog(request, "result", title=f"{alias} removed", results=results)


@router.post("/ssh/{alias}/sudo")
async def ssh_sudo(request: Request, alias: str):
    if alias not in {h["alias"] for h in sshhosts.hosts()}:
        return respond(request, "unknown host", "err")
    form = await request.form()
    cmd = form.get("sudo", "").strip() if form.get("use_sudo") else ""
    try:
        sshhosts.set_sudo(alias, cmd)
        _SSH_STATUS.clear()
    except ValueError as e:
        return respond(request, str(e), "err")
    await sync_services()  # svc docker contexts + Claude's SSH host table
    return respond(request, f"{alias}: sudo {'= ' + cmd if cmd else 'off'}", refresh=True)


_SSH_STATUS = {}


async def ssh_state(alias):
    """('ok'|'warn'|'err'|'', title): ok = login (and sudo, if configured) works, warn = login ok but sudo fails, err = no login"""
    if alias not in {h["alias"] for h in sshhosts.hosts()}:
        return "", "unknown host"
    sudo = sshhosts.sudo_of(alias)
    hit = _SSH_STATUS.get((alias, sudo))
    if hit and time.time() - hit[0] < 60:  # the overview refreshes every 15 s – don't ssh that often
        code, out = hit[1]
    else:
        code, out = await asyncio.to_thread(dockerops.ssh_check, alias, sudo)
        _SSH_STATUS[(alias, sudo)] = (time.time(), (code, out))
    lines = [l for l in out.strip().splitlines() if l.strip()]
    if code != 0:
        return "err", f"not reachable: {lines[-1] if lines else ''}"
    if sudo and "SUDO_FAILED" in lines:
        return "warn", f"login ok, but {sudo} fails (needs NOPASSWD)"
    who = next((l for l in lines if "@" in l), "")
    return "ok", f"ok: {who}{' · sudo ok' if sudo else ''}"


@router.get("/ssh/{alias}/status", response_class=HTMLResponse)
async def ssh_status(alias: str):
    """status dot for the host tables"""
    state, title = await ssh_state(alias)
    return HTMLResponse(f'<span class="dot {state}" title="{html.escape(title)}"></span>')


@router.post("/ssh/{alias}/test", response_class=HTMLResponse)
async def ssh_test(alias: str):
    if alias not in {h["alias"] for h in sshhosts.hosts()}:
        return HTMLResponse('<span class="pill err">unknown</span>')
    sudo = sshhosts.sudo_of(alias)
    code, out = await asyncio.to_thread(dockerops.ssh_check, alias, sudo)
    lines = [l for l in out.strip().splitlines() if l.strip()]
    sudo_state = lines.pop() if sudo and lines and lines[-1] in ("SUDO_OK", "SUDO_FAILED") else ""
    line = html.escape(lines[-1] if lines else "")
    if code == 0:
        res = f'<span class="pill ok">✓ {line}</span>'
        if sudo_state == "SUDO_OK":
            res += f' <span class="pill ok">✓ {html.escape(sudo)}</span>'
        elif sudo_state:
            res += f' <span class="pill err" title="the SSH user needs passwordless sudo (NOPASSWD)">✗ {html.escape(sudo)}: needs NOPASSWD</span>'
        return HTMLResponse(res)
    return HTMLResponse(f'<span class="pill err" title="{line}">✗ {html.escape((lines[-1] if lines else "")[:120])}</span>')
