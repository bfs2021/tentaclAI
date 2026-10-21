"""Activity (approvals, grants, audit), svc's and the gateway's internal endpoints, and the Network page."""
import asyncio
import time

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse

from .. import activity, egress, notify
from ..envfile import update_env
from ..web import page, respond, templates, toast
import tclib  # noqa: E402  shared with the sandbox tools

router = APIRouter()


def _svc_auth(request):
    return activity.token_ok(request.headers.get("x-tc-token", ""))


@router.post("/internal/approvals")
async def internal_approval_create(request: Request):
    if not _svc_auth(request):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    data = await request.json()
    rid = activity.create(data.get("service", "?"), data.get("part", "?"), data.get("action", "?"), data.get("origin"))
    a = activity.APPROVALS[rid]
    return JSONResponse({"id": rid, "status": a["status"], "by": a["by"]})


@router.post("/internal/event")
async def internal_event(request: Request):
    """svc reports something worth a notification (e.g. a blocked write)"""
    if not _svc_auth(request):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    d = await request.json()
    if d.get("event") in ("blocked",):
        notify.emit(d["event"], str(d.get("title", ""))[:200], str(d.get("body", ""))[:500])
    return JSONResponse({"ok": True})


@router.post("/internal/egress/event")
async def internal_egress_event(request: Request):
    """the gateway reports a refused destination"""
    if _from_proxy(request):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    d = await request.json()
    target = f"{str(d.get('host', ''))[:200]}:{int(d.get('port', 0) or 0)}"
    notify.emit("blocked", f"Network: {target} {str(d.get('reason', 'refused'))[:30]}", "The sandbox tried to connect.",
                key=f"blocked:{target}", link=notify.control_link("/network"))
    return JSONResponse({"ok": True})


@router.get("/internal/approvals")
async def internal_approval_list(request: Request):
    """pending requests and active grants – for `svc pending` (the status line in Claude's terminal)"""
    if not _svc_auth(request):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    return JSONResponse({"pending": [{k: a[k] for k in ("id", "service", "part", "action", "created")} for a in activity.pending()],
                         "grants": [{k: g[k] for k in ("service", "part", "until")} for g in activity.grants()]})


@router.get("/internal/approvals/{rid}")
async def internal_approval_wait(request: Request, rid: str, wait: int = 25):
    if not _svc_auth(request):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    return JSONResponse(await activity.wait(rid, wait))


@router.get("/activity", response_class=HTMLResponse)
async def activity_page(request: Request, service: str = "", decision: str = ""):
    return page(request, "activity.html", entries=activity.audit_entries(service=service, decision=decision),
                service=service, decision=decision,
                services_known=sorted({e.get("service", "") for e in activity.audit_entries(limit=2000)}))


@router.get("/partials/approvals", response_class=HTMLResponse)
async def approvals_partial(request: Request):
    return templates.TemplateResponse(request, "partials/approvals.html",
                                      {"pending": activity.pending(), "recent": activity.recent_decisions(), "now": time.time(),
                                       "grants": activity.grants(), "no_grant": activity.NO_GRANT_PARTS})


@router.get("/partials/approval-banner", response_class=HTMLResponse)
async def approval_banner(request: Request):
    return templates.TemplateResponse(request, "partials/approval_banner.html",
                                      {"pending": activity.pending(), "no_grant": activity.NO_GRANT_PARTS})


@router.post("/grants/revoke", response_class=HTMLResponse)
async def grant_revoke(request: Request, service: str = Form(...), part: str = Form(...)):
    activity.revoke(service, part)
    resp = await approvals_partial(request)
    resp.headers.update(toast(f"Grant for {service} ({part}) revoked"))
    return resp


@router.get("/partials/approval-count", response_class=HTMLResponse)
async def approval_count():
    n = len(activity.pending())
    # never an empty answer: app.js keeps the old content then, and the badge would stay after the last decision
    return HTMLResponse(f'<span class="nav-badge">{n}</span>' if n else '<span class="nav-badge" hidden></span>')


@router.post("/approvals/{rid}/{verdict}", response_class=HTMLResponse)
async def approval_decide(request: Request, rid: str, verdict: str):
    if verdict not in ("approve", "approve15", "deny", "net10", "net60", "always", "block"):
        return PlainTextResponse("", status_code=400)
    who = "tentacontrol (" + (request.client.host if request.client else "?") + ")"
    a = activity.APPROVALS.get(rid) or {}
    if verdict in ("always", "block") and a.get("part") == "network" and a.get("status") == "pending":
        egress.add("allow" if verdict == "always" else "block", a["service"])  # host:port
    ok = activity.decide(rid, verdict not in ("deny", "block"), who=who, grant=verdict in ("approve15", "net10", "net60"),
                         seconds={"net10": 600, "net60": 3600}.get(verdict))
    resp = await (approval_banner(request) if request.query_params.get("view") == "banner" else approvals_partial(request))
    if not ok:
        resp.headers.update(toast("Request no longer pending", "warn"))
    return resp


def _from_proxy(request):
    """the egress endpoints are for the gateway in the sandbox network – not for requests through the reverse proxy"""
    return any(h in request.headers for h in ("x-forwarded-for", "x-real-ip", "forwarded"))


@router.get("/internal/egress/policy")
async def internal_egress_policy(request: Request):
    """for the egress gateway (inside the sandbox network) – host lists only, no secrets"""
    if _from_proxy(request):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    return JSONResponse(await asyncio.to_thread(egress.policy))


@router.post("/internal/egress/ask")
async def internal_egress_ask(request: Request):
    """the gateway asks about an unknown destination (enforce) – only creates a request, the user decides"""
    if _from_proxy(request):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    data = await request.json()
    try:
        host, port = egress.normalize(str(data.get("host", ""))), int(data.get("port", 0))
    except (ValueError, TypeError):
        return JSONResponse({"error": "bad target"}, status_code=400)
    rid = activity.create(f"{host}:{port}", "network", f"connect to {host}:{port}")
    return JSONResponse({"id": rid})


@router.get("/internal/egress/wait/{rid}")
async def internal_egress_wait(rid: str, wait: int = 20):
    a = activity.APPROVALS.get(rid)
    if not a or a["part"] != "network":
        return JSONResponse({"status": "error"})
    return JSONResponse(await activity.wait(rid, wait))


@router.post("/network/rule", response_class=HTMLResponse)
async def network_rule(request: Request, kind: str = Form(...), entry: str = Form(...), op: str = Form("add")):
    if kind not in ("allow", "block"):
        return PlainTextResponse("", status_code=400)
    try:
        if op == "remove":
            egress.remove(kind, entry)
            msg = f"{entry} removed from the {kind}list"
        else:
            e = egress.add(kind, entry)
            msg = f"{e} added to the {kind}list"
            still = egress.still_blocked(e) if kind == "allow" else ""
            if still:
                return respond(request, f"{e} allowed, but the blocklist entry {still} still blocks it", "warn", refresh=True)
    except ValueError as e:
        return respond(request, f"Invalid entry: {e}", "err")
    return respond(request, msg, refresh=True)


@router.post("/network/clear", response_class=HTMLResponse)
async def network_clear(request: Request):
    egress.clear_seen()
    return respond(request, "List cleared – it fills again with new connections", refresh=True)


@router.post("/network/direct-ip", response_class=HTMLResponse)
async def network_direct_ip(request: Request):
    new = "ask" if egress.direct_ip() == "block" else "block"
    update_env({"EGRESS_DIRECT_IP": new})
    return respond(request, f"IP targets: {new} (in enforce mode, applied within 15 s)", refresh=True)


@router.post("/network/mode", response_class=HTMLResponse)
async def network_mode(request: Request):
    new = "enforce" if egress.mode() == "permissive" else "permissive"
    update_env({"EGRESS_MODE": new})
    return respond(request, f"Network mode: {new} (the gateway applies it within 15 s)", refresh=True)


@router.get("/network", response_class=HTMLResponse)
async def network_page(request: Request, tab: str = "seen"):
    """egress gateway: seen destinations, allow and block lists, mode"""
    rows = await asyncio.to_thread(egress.seen_rows)
    fmt = lambda ts: time.strftime("%Y-%m-%d %H:%M", time.localtime(ts)) if ts else "–"  # noqa: E731
    for r in rows:
        r["first_s"], r["last_s"] = fmt(r.get("first")), fmt(r.get("last"))
    tent = await asyncio.to_thread(egress.tentacles)
    return page(request, "network.html", tab=tab if tab in ("seen", "allow", "block") else "seen", rows=rows, mode=egress.mode(),
                direct_ip=egress.direct_ip(),
                rules=egress.rules(), default=egress.default_list(),
                tentacles=sorted((f"{h}:{p}" if p else h, n) for (h, p), n in tent.items()),
                counts={k: sum(1 for r in rows if r["kind"] == k) for k in ("tentacle", "default", "allow", "unknown", "blocked")})
