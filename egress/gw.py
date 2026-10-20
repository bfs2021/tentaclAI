#!/usr/bin/env python3
"""tentaclAI egress gateway – the only way out of the sandbox network.

Forward proxy for CONNECT (any TCP port – HTTPS, ssh, databases; TLS is not opened) and plain HTTP requests.
Policy (from tentacontrol, refreshed every 15 s): built-in list → blocklist → own allowlist → tentacles → grants →
EGRESS_MODE permissive (allow, record as "unlisted") or enforce (hold the connection and ask in Activity).
Every destination is counted in $DATA_DIR/egress-seen.json (host:port → first, last, count, last decision).
"""
import asyncio
import json
import os
import socket
import sys
import time
import urllib.request

sys.path.insert(0, "/app")
import tclib  # noqa: E402

LISTEN = int(os.environ.get("EGRESS_PORT", "3128"))
CONTROL = os.environ.get("TC_CONTROL_INTERNAL", "http://tentacontrol:8080")
DATA = os.environ.get("DATA_DIR", "/data")
REFRESH = float(os.environ.get("EGRESS_REFRESH", "15"))  # seconds between policy fetches (the tests use 1)
SEEN_FILE = os.path.join(DATA, "egress-seen.json")
POLICY_CACHE = os.path.join(DATA, "policy.json")  # last policy from tentacontrol – decisions work right after a restart
DEFAULT = tclib.egress_patterns(open("/app/egress-default.txt").read())
IDLE = 3600  # seconds a tunnel may stay silent (ssh sessions, long polls)
HOLD = 60  # seconds an unknown destination waits for the user's decision (enforce)

seen, dirty, cleared = {}, False, 0.0
pol = {"mode": os.environ.get("EGRESS_MODE", "permissive"), "direct_ip": "block", "allow": [], "block": [], "tentacles": {},
       "grants": {}, "ok": False}


def control(path, payload=None, timeout=30):
    req = urllib.request.Request(CONTROL + path, data=json.dumps(payload).encode() if payload is not None else None,
                                 headers={"Content-Type": "application/json"}, method="POST" if payload is not None else "GET")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def refresh(p=None):
    """policy from tentacontrol (cached on disk); keeps the last known one when tentacontrol is unreachable"""
    if p is None:
        try:
            p = control("/internal/egress/policy", timeout=10)
        except Exception as e:  # noqa: BLE001
            if pol["ok"]:
                print(f"policy refresh failed: {e}", flush=True)
            return
        try:
            with open(POLICY_CACHE + ".tmp", "w") as f:
                json.dump(p, f)
            os.replace(POLICY_CACHE + ".tmp", POLICY_CACHE)
        except OSError:
            pass
    global seen, dirty, cleared
    if p.get("cleared", 0) > cleared:  # "Clear" on the Network page
        seen, dirty = {k: v for k, v in seen.items() if v.get("last", 0) > p["cleared"]}, True
        cleared = p["cleared"]
    pol.update(mode=p.get("mode", "permissive"), direct_ip=p.get("direct_ip", "block"), allow=p.get("allow", []), block=p.get("block", []), ok=True,
               tentacles={(h, pt): n for h, pt, n in p.get("tentacles", [])}, grants=dict(p.get("grants", {})))


async def refresher():
    while True:
        await asyncio.to_thread(refresh)
        await asyncio.sleep(REFRESH if pol["ok"] else 2)  # retry quickly until the first policy arrived


async def policy_ready(seconds=30):
    """no policy yet (first start, tentacontrol still starting): wait for it instead of deciding without rules"""
    end = time.time() + seconds
    while not pol["ok"] and time.time() < end:
        await asyncio.sleep(0.5)
    return pol["ok"]


def decide(host, port, ips):
    """(allow?, reason) – names are checked as requested, IP/CIDR rules also against the resolved addresses ips"""
    if any(tclib.egress_match(p, host, port) for p in DEFAULT):  # built-in: Claude Code itself – never blockable
        return True, "allowlist"
    if any(tclib.egress_match(p, host, port, ips) for p in pol["block"]):
        return False, "blocked"
    if any(tclib.egress_match(p, host, port, ips) for p in pol["allow"]):
        return True, "allowlist"
    if (host, port) in pol["tentacles"] or (host, None) in pol["tentacles"]:
        return True, "tentacle"
    if pol["grants"].get(f"{host}:{port}", 0) > time.time():
        return True, "grant"
    if pol["mode"] != "enforce":
        return True, "unlisted"
    if tclib.is_ip(host) and pol["direct_ip"] == "block":
        return False, "direct-ip"
    return None, "ask"


async def resolve(host, port):
    """addresses the gateway will connect to – checked against IP/CIDR rules, then used for the connection (no re-resolve)"""
    if tclib.is_ip(host):
        return [host]
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError:
        return []
    return sorted(dict.fromkeys(i[4][0] for i in infos), key=lambda a: ":" in a)  # IPv4 first


async def ask(host, port):
    """enforce: request an approval in tentacontrol and hold the connection up to HOLD seconds"""
    try:
        rid = (await asyncio.to_thread(control, "/internal/egress/ask", {"host": host, "port": port}))["id"]
        end = time.time() + HOLD
        while time.time() < end:
            st = await asyncio.to_thread(control, f"/internal/egress/wait/{rid}?wait=20", None, 30)
            if st.get("status") != "pending":
                await asyncio.to_thread(refresh)
                return st.get("status") == "approved"
    except Exception as e:  # noqa: BLE001
        print(f"approval for {host}:{port} failed: {e}", flush=True)
    return False


def report(host, port, reason):
    """tell tentacontrol about a refused destination (notifications) – best effort"""
    try:
        control("/internal/egress/event", {"host": host, "port": port, "reason": reason}, timeout=5)
    except Exception:  # noqa: BLE001
        pass


def load_seen():
    global seen
    try:
        seen = json.load(open(SEEN_FILE))
    except (OSError, ValueError):
        seen = {}


def record(host, port, decision):
    global dirty
    now = int(time.time())
    e = seen.setdefault(f"{host}:{port}", {"first": now, "count": 0})
    e.update(last=now, count=e["count"] + 1, decision=decision)
    dirty = True


async def flush():
    global dirty
    while True:
        await asyncio.sleep(10)
        if dirty:
            dirty = False
            with open(SEEN_FILE + ".tmp", "w") as f:
                json.dump(seen, f, indent=1, sort_keys=True)
            os.replace(SEEN_FILE + ".tmp", SEEN_FILE)


def split_hostport(s, default):
    s = s.strip()
    if s.startswith("["):  # [v6]:port
        h, _, rest = s[1:].partition("]")
        return h.lower(), int(rest[1:] or default)
    h, _, p = s.rpartition(":") if s.count(":") == 1 else (s, "", "")
    return (h or s).lower().rstrip("."), int(p or default)


async def pipe(r, w):
    try:
        while True:
            data = await asyncio.wait_for(r.read(65536), IDLE)
            if not data:
                break
            w.write(data)
            await w.drain()
    except (asyncio.TimeoutError, ConnectionError, OSError):
        pass
    finally:
        try:
            w.close()
        except OSError:
            pass


def refuse(cw, code, text):
    cw.write(f"HTTP/1.1 {code}\r\nConnection: close\r\nContent-Type: text/plain\r\n\r\n{text}\n".encode())
    cw.close()


async def handle(cr, cw):
    try:
        head = await asyncio.wait_for(cr.readuntil(b"\r\n\r\n"), 30)
        method, target, version = head.decode("latin-1").split("\r\n")[0].split(" ", 2)
    except (asyncio.TimeoutError, asyncio.IncompleteReadError, asyncio.LimitOverrunError, ConnectionError, ValueError):
        cw.close()
        return
    lines = head.decode("latin-1").split("\r\n")
    try:
        if method.upper() == "CONNECT":
            host, port = split_hostport(target, 443)
            rest = b""
        elif target.startswith("http://"):  # plain HTTP: absolute URI → origin form, one request per connection
            hostpart, _, path = target[7:].partition("/")
            host, port = split_hostport(hostpart, 80)
            hdrs = [l for l in lines[1:] if l and not l.lower().startswith(("proxy-", "connection:", "keep-alive:"))]
            rest = (f"{method} /{path} {version}\r\n" + "\r\n".join(hdrs) + "\r\nConnection: close\r\n\r\n").encode("latin-1")
        else:
            return refuse(cw, "400 Bad Request", "tentaclAI egress: proxy requests only")
    except ValueError:
        return refuse(cw, "400 Bad Request", "tentaclAI egress: bad host or port")
    ips = await resolve(host, port)
    if not await policy_ready():
        return refuse(cw, "503 Service Unavailable", "tentaclAI egress: no rules from tentacontrol yet – try again in a moment")
    ok, why = decide(host, port, ips)
    if ok is None:  # the cached policy may predate a service enabled or a token set just now – check fresh first
        await asyncio.to_thread(refresh)
        ok, why = decide(host, port, ips)
    if ok is None:
        ok = await ask(host, port)
        why = "approved" if ok else "denied"
    record(host, port, why)
    if not ok and why in ("blocked", "direct-ip", "denied"):
        asyncio.get_running_loop().run_in_executor(None, report, host, port, why)
    if not ok:
        return refuse(cw, "403 Forbidden", f"tentaclAI egress: {host}:{port} is {why} – allow it in tentacontrol → Network or approve it in Activity")
    if not ips:
        return refuse(cw, "502 Bad Gateway", f"{host}: name not found")
    try:
        ur, uw = await asyncio.wait_for(asyncio.open_connection(ips[0], port), 20)
    except (OSError, asyncio.TimeoutError) as e:
        return refuse(cw, "502 Bad Gateway", f"{host}:{port}: {e}")
    if method.upper() == "CONNECT":
        cw.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
        await cw.drain()
    else:
        uw.write(rest)
    await asyncio.gather(pipe(cr, uw), pipe(ur, cw))


async def main():
    os.makedirs(DATA, exist_ok=True)
    load_seen()
    try:
        refresh(json.load(open(POLICY_CACHE)))
    except (OSError, ValueError):
        pass
    server = await asyncio.start_server(handle, "0.0.0.0", LISTEN, limit=65536)
    print(f"tentaclAI egress gateway on :{LISTEN}", flush=True)
    asyncio.create_task(flush())
    asyncio.create_task(refresher())
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
