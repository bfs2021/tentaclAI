"""MCP: the servers handed to Claude Code, the stdio bridge and the HTTP proxy with the credentials injected."""
import json
import os
import subprocess
import sys

from . import core, gate


# ---------- MCP ----------
# MCP servers whose config holds secrets (headers, env) are registered in Claude's config as `svc mcp-proxy <name>`:
# stdio servers run as tcrun with their env, http servers are bridged here (stdio ↔ Streamable HTTP) with the headers
# added by svc – the tokens never land in Claude's ~/.claude.json.


def claude_mcp(*args):
    return subprocess.run(["claude", "mcp", *args], capture_output=True, text=True, **core.as_user(core.CLAUDE_USER, cwd="/"))


def mcp_entry(name, mcp):
    """what Claude's config gets: the server itself when it needs no secret, else the svc proxy"""
    if not core.variables(mcp):
        return core.render(mcp)
    return {"type": "stdio", "command": "svc", "args": ["mcp-proxy", name]}


def cmd_mcp_proxy(services, name):
    svc = services.get(name) or sys.exit(f"svc: unknown service '{name}'")
    if "mcp" not in svc or not core.enabled(svc) or core.missing_vars(svc["mcp"]):
        sys.exit(f"svc: {name}: MCP not available (svc test {name})")
    if core.job_read_only():
        sys.exit(f"svc: {name}: MCP servers are off in read-only jobs (their tools can write)")
    gate.require_allowed(name)
    m = core.render(svc["mcp"])
    kind = m.get("type", "stdio" if m.get("command") else "http")
    if kind == "stdio":
        kw = core.runner({k: str(v) for k, v in (m.get("env") or {}).items()})
        kw.pop("cwd", None)
        pw = core._account(core.RUN_USER) if core.privileged() else None
        os.chdir(pw.pw_dir if pw else core.ORIG_CWD)
        if pw:
            os.setgroups([])
            os.setgid(pw.pw_gid)
            os.setuid(pw.pw_uid)
        os.execvpe(m["command"], [m["command"], *[str(a) for a in m.get("args", [])]], kw["env"])
    if kind != "http":
        sys.exit(f"svc: {name}: MCP type {kind} can't be proxied – use type http (Streamable HTTP)")
    return mcp_bridge(m)


def mcp_bridge(m):
    import ssl
    import threading
    import urllib.error
    import urllib.request
    ctx = ssl._create_unverified_context() if m.get("insecure_tls") else ssl.create_default_context()
    headers = {str(k): str(v) for k, v in (m.get("headers") or {}).items()}
    state = {"session": None, "version": None}
    lock = threading.Lock()

    def emit(obj):
        with lock:
            sys.stdout.write(json.dumps(obj, separators=(",", ":")) + "\n")
            sys.stdout.flush()

    def post(line, rid):
        h = dict(headers, **{"Content-Type": "application/json", "Accept": "application/json, text/event-stream"})
        if state["session"]:
            h["Mcp-Session-Id"] = state["session"]
        if state["version"]:
            h["MCP-Protocol-Version"] = state["version"]
        req = urllib.request.Request(m["url"], data=line.encode(), headers=h, method="POST")
        try:
            with urllib.request.urlopen(req, context=ctx, timeout=300) as r:
                sid = r.headers.get("Mcp-Session-Id")
                if sid:
                    state["session"] = sid
                ctype = r.headers.get("Content-Type", "")
                if r.status == 202:
                    return
                if "text/event-stream" in ctype:
                    data = []
                    for raw in r:
                        l = raw.decode("utf-8", "replace").rstrip("\r\n")
                        if l.startswith("data:"):
                            data.append(l[5:].lstrip())
                        elif not l and data:
                            msg = json.loads("\n".join(data))
                            data = []
                            note_version(msg)
                            emit(msg)
                    if data:
                        msg = json.loads("\n".join(data))
                        note_version(msg)
                        emit(msg)
                    return
                body = r.read().decode("utf-8", "replace").strip()
                if body:
                    msg = json.loads(body)
                    for x in (msg if isinstance(msg, list) else [msg]):
                        note_version(x)
                        emit(x)
        except urllib.error.HTTPError as e:
            if e.code == 404 and state["session"]:
                state["session"] = None  # session expired – the client has to initialize again
            if rid is not None:
                emit({"jsonrpc": "2.0", "id": rid, "error": {"code": -32000, "message": f"HTTP {e.code} from MCP server"}})
        except Exception as e:  # noqa: BLE001 – network errors, bad JSON
            if rid is not None:
                emit({"jsonrpc": "2.0", "id": rid, "error": {"code": -32000, "message": f"MCP proxy: {str(e)[:200]}"}})

    def note_version(msg):
        if isinstance(msg, dict) and isinstance(msg.get("result"), dict) and msg["result"].get("protocolVersion"):
            state["version"] = msg["result"]["protocolVersion"]

    for line in sys.stdin:
        line = line.strip()
        try:
            msg = json.loads(line) if line else None
        except ValueError:
            continue
        if not isinstance(msg, dict):
            continue
        rid = msg.get("id")
        if rid is None or msg.get("method") == "initialize" or not state["session"] and not state["version"]:
            post(line, rid)  # initialize and notifications in order (initialize sets the session id)
        else:
            threading.Thread(target=post, args=(line, rid), daemon=True).start()  # requests may run long – in parallel
    return 0
