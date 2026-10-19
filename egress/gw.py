#!/usr/bin/env python3
"""tentaclAI egress gateway – the only way out of the sandbox network.

Forward proxy for CONNECT (any TCP port – HTTPS, ssh, databases; TLS is not opened) and plain HTTP requests.
EGRESS_MODE=permissive: everything is allowed and recorded (learning); enforce: see policy() – next step.
Every destination is counted in $DATA_DIR/egress-seen.json (host:port → first, last, count, last decision).
"""
import asyncio
import json
import os
import time

LISTEN = int(os.environ.get("EGRESS_PORT", "3128"))
MODE = os.environ.get("EGRESS_MODE", "permissive")
DATA = os.environ.get("DATA_DIR", "/data")
SEEN_FILE = os.path.join(DATA, "egress-seen.json")
IDLE = 3600  # seconds a tunnel may stay silent (ssh sessions, long polls)

seen = {}
dirty = False


def load_seen():
    global seen
    try:
        seen = json.load(open(SEEN_FILE))
    except (OSError, ValueError):
        seen = {}


def record(host, port, decision):
    global dirty
    k = f"{host}:{port}"
    now = int(time.time())
    e = seen.setdefault(k, {"first": now, "count": 0})
    e.update(last=now, count=e["count"] + 1, decision=decision)
    dirty = True


async def flush():
    global dirty
    while True:
        await asyncio.sleep(10)
        if dirty:
            dirty = False
            tmp = SEEN_FILE + ".tmp"
            with open(tmp, "w") as f:
                json.dump(seen, f, indent=1, sort_keys=True)
            os.replace(tmp, SEEN_FILE)


def policy(host, port):
    """'allow' | 'deny' – permissive allows everything (and records it)"""
    return "allow"


def split_hostport(s, default):
    s = s.strip()
    if s.startswith("["):  # [v6]:port
        h, _, rest = s[1:].partition("]")
        return h, int(rest[1:] or default)
    h, _, p = s.rpartition(":") if s.count(":") == 1 else (s, "", "")
    return (h or s).lower(), int(p or default)


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


async def handle(cr, cw):
    try:
        head = await asyncio.wait_for(cr.readuntil(b"\r\n\r\n"), 30)
    except (asyncio.TimeoutError, asyncio.IncompleteReadError, asyncio.LimitOverrunError, ConnectionError):
        cw.close()
        return
    lines = head.decode("latin-1").split("\r\n")
    try:
        method, target, version = lines[0].split(" ", 2)
    except ValueError:
        cw.close()
        return
    if method.upper() == "CONNECT":
        host, port = split_hostport(target, 443)
        rest = b""
    else:  # plain HTTP: absolute URI → origin form, one request per connection
        if not target.startswith("http://"):
            cw.write(b"HTTP/1.1 400 Bad Request\r\nConnection: close\r\n\r\n")
            cw.close()
            return
        hostpart, _, path = target[7:].partition("/")
        host, port = split_hostport(hostpart, 80)
        hdrs = [l for l in lines[1:] if l and not l.lower().startswith(("proxy-", "connection:", "keep-alive:"))]
        rest = (f"{method} /{path} {version}\r\n" + "\r\n".join(hdrs) + "\r\nConnection: close\r\n\r\n").encode("latin-1")
    decision = policy(host, port)
    record(host, port, decision)
    if decision != "allow":
        cw.write(b"HTTP/1.1 403 Forbidden\r\nConnection: close\r\nContent-Type: text/plain\r\n\r\n"
                 + f"tentaclAI egress: {host}:{port} is not allowed – approve it in tentacontrol\n".encode())
        cw.close()
        return
    try:
        ur, uw = await asyncio.wait_for(asyncio.open_connection(host, port), 20)
    except (OSError, asyncio.TimeoutError) as e:
        cw.write(f"HTTP/1.1 502 Bad Gateway\r\nConnection: close\r\n\r\n{host}:{port}: {e}\n".encode())
        cw.close()
        return
    if method.upper() == "CONNECT":
        cw.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
        await cw.drain()
    else:
        uw.write(rest)
    await asyncio.gather(pipe(cr, uw), pipe(ur, cw))


async def main():
    os.makedirs(DATA, exist_ok=True)
    load_seen()
    server = await asyncio.start_server(handle, "0.0.0.0", LISTEN, limit=65536)
    print(f"tentaclAI egress gateway on :{LISTEN}, mode {MODE}", flush=True)
    asyncio.create_task(flush())
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
