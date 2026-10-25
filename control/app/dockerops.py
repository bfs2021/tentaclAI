"""Everything that talks to the Docker daemon about the tentaclai container."""
import datetime as dt
import json
import os
import re
import socket

import docker
from docker.errors import NotFound

from .config import CLAUDE_CONTAINER, HOME_DIR, SSH_DIR

_client = None


def client():
    global _client
    if _client is None:
        _client = docker.from_env()
    return _client


def container(name=None):
    """the sandbox container – or another one by name (Logs → Containers)"""
    try:
        return client().containers.get(name or CLAUDE_CONTAINER)
    except NotFound:
        return None


CLAUDE_USER = "claude"  # Claude Code, tmux sessions and remote control run as this user; svc/ssh as root


def run(cmd, workdir=None, user="root"):
    """Run a command in the claude container, return (exit_code, text)."""
    c = container()
    if c is None or c.status != "running":
        return 1, "container is not running"
    try:
        r = c.exec_run(cmd, workdir=workdir, demux=False, user=user)
        return r.exit_code, (r.output or b"").decode(errors="replace")
    except Exception as e:  # noqa: BLE001
        return 1, str(e)


def _parse_ts(s):
    """Docker timestamps: 2026-10-16T20:48:12.123456789Z or ...+02:00 (nanoseconds, any offset)"""
    m = re.match(r"(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(\.\d+)?(Z|[+-]\d\d:\d\d)?", s or "")
    if not m or s.startswith("0001"):
        return None
    frac = (m.group(2) or ".0")[:7]
    tz = m.group(3) or "Z"
    return dt.datetime.fromisoformat(m.group(1) + frac + ("+00:00" if tz == "Z" else tz)).astimezone(dt.timezone.utc)


def human_age(ts):
    if not ts:
        return "–"
    delta = dt.datetime.now(dt.timezone.utc) - ts
    s = int(delta.total_seconds())
    if s < 120:
        return f"{s} s"
    if s < 7200:
        return f"{s // 60} min"
    if s < 172800:
        return f"{s // 3600} h"
    return f"{s // 86400} days"


def container_running(name):
    try:
        return client().containers.get(name).status == "running"
    except Exception:  # noqa: BLE001 – missing container counts as not running
        return False


def newer_image():
    """True when the image tag the sandbox runs from was rebuilt after the container was created"""
    c = container()
    if c is None:
        return False
    try:
        return client().images.get(image_name(c)).id != c.attrs.get("Image")
    except Exception:  # noqa: BLE001 – tag gone, docker hiccup
        return False


def image_name(c):
    """image reference of a container – c.image fails when the image was rebuilt/removed meanwhile"""
    return c.attrs.get("Config", {}).get("Image") or c.attrs.get("Image", "")[:19]


def status():
    c = container()
    if c is None:
        return {"exists": False, "running": False, "state": "not created"}
    st = c.attrs.get("State", {})
    started = _parse_ts(st.get("StartedAt", ""))
    created = _parse_ts(c.attrs.get("Created", ""))
    img_created = None
    try:
        img_created = _parse_ts(c.image.attrs.get("Created", ""))
    except Exception:  # noqa: BLE001
        pass
    return {
        "exists": True,
        "running": c.status == "running",
        "state": {"running": "running", "exited": "stopped", "restarting": "restarting",
                  "created": "created", "paused": "paused"}.get(c.status, c.status),
        "uptime": human_age(started) if c.status == "running" else "–",
        "created_ts": created.timestamp() if created else 0,
        "image": image_name(c),
        "image_age": human_age(img_created),
        "health": st.get("Health", {}).get("Status"),
        "mounts": [(m.get("Source", ""), m.get("Destination", "")) for m in c.attrs.get("Mounts", [])],
    }


def stats():
    c = container()
    if c is None or c.status != "running":
        return None
    try:
        s = c.stats(stream=False)
        cpu_delta = s["cpu_stats"]["cpu_usage"]["total_usage"] - s["precpu_stats"]["cpu_usage"]["total_usage"]
        sys_delta = s["cpu_stats"].get("system_cpu_usage", 0) - s["precpu_stats"].get("system_cpu_usage", 0)
        ncpu = s["cpu_stats"].get("online_cpus", 1)
        cpu = (cpu_delta / sys_delta) * ncpu * 100 if sys_delta > 0 else 0.0
        mem = s["memory_stats"].get("usage", 0) - s["memory_stats"].get("stats", {}).get("inactive_file", 0)
        lim = s["memory_stats"].get("limit", 1)
        return {"cpu": round(cpu, 1), "mem_mb": round(mem / 1048576), "mem_limit_mb": round(lim / 1048576),
                "mem_pct": round(mem / lim * 100, 1)}
    except Exception:  # noqa: BLE001
        return None


def claude_version():
    code, out = run(["claude", "--version"], user=CLAUDE_USER)
    return out.strip().split()[0] if code == 0 and out.strip() else None


def claude_latest():
    code, out = run(["timeout", "30", "npm", "view", "@anthropic-ai/claude-code", "version"], user=CLAUDE_USER)
    return out.strip() if code == 0 else None


def claude_auth():
    """`claude auth status` (JSON: loggedIn, email, orgName, subscriptionType …); falls back to the credentials file
    when the container is not running"""
    code, out = run(["claude", "auth", "status", "--json"], user=CLAUDE_USER)
    try:
        return json.loads(out)
    except ValueError:
        return {"loggedIn": os.path.exists(os.path.join(HOME_DIR, ".claude", ".credentials.json"))}


def logged_in_claude():
    return bool(claude_auth().get("loggedIn"))


def claude_logout():
    """sign the sandbox out of Claude (e.g. to connect another account) and restart remote control"""
    code, out = run(["claude", "auth", "logout"], user=CLAUDE_USER)
    rc_restart()
    return code, out


def tmux_sessions():
    code, out = run(["tmux", "ls", "-F", "#{session_name}|#{session_attached}|#{session_created}"], user=CLAUDE_USER)
    if code != 0:
        return []
    res = []
    for line in out.strip().splitlines():
        parts = line.split("|")
        if len(parts) == 3:
            age = human_age(dt.datetime.fromtimestamp(int(parts[2]), dt.timezone.utc)) if parts[2].isdigit() else "–"
            res.append({"name": parts[0], "label": session_label(parts[0]), "attached": parts[1] != "0", "age": age})
    return res


SESSION_LABELS = {"claude-workspace": "Claude terminal", "claude-rc": "Remote Control", "claude-inventory": "System inventory"}


def session_label(name):
    """readable name for a tmux session started by cc, rc-loop or a terminal profile"""
    if name in SESSION_LABELS:
        return SESSION_LABELS[name]
    if name.startswith("claude-onboard-"):
        return "Onboarding " + name[len("claude-onboard-"):]
    if name.startswith("claude-"):
        return "Claude · " + name[len("claude-"):]
    return name


RC_URL = re.compile(r"https://claude\.ai/code\S*")


def rc_info():
    """state of the remote-control session parsed from its tmux pane (None = no session)"""
    code, out = run(["tmux", "capture-pane", "-p", "-J", "-t", "claude-rc", "-S", "-300"], user=CLAUDE_USER)
    if code != 0:
        return None
    lines = [l.rstrip() for l in out.splitlines() if l.strip()]
    # only look at the output of the current run (rc-loop prints "▶ claude remote-control …" before each start)
    starts = [i for i, l in enumerate(lines) if l.startswith("▶ claude remote-control")]
    cur = lines[starts[-1]:] if starts else lines
    text = "\n".join(cur)
    urls = RC_URL.findall(text)
    last = cur[-1] if cur else ""
    if "Remote control is disabled" in last:
        state = "disabled"
    elif "remote-control ended" in last:
        state = "restarting"
    elif last.rstrip().endswith("(y/n)"):
        state = "confirm"
    elif re.search(r"\b(re)?connected\b", text, re.I):  # after a reconnect the URL may no longer be on screen
        state = "connected"
    else:
        state = "starting"
    capacity = next((l.strip() for l in cur if l.strip().startswith("Capacity:")), "")
    return {"state": state, "url": urls[-1] if urls else "", "capacity": capacity,
            "tail": "\n".join(lines[-14:])}


def rc_restart():
    return run(["tmux", "respawn-pane", "-k", "-t", "claude-rc"], user=CLAUDE_USER)


LOG_TS = re.compile(r"^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(?:\.\d+)?Z", re.M)


def local_ts(text):
    """Docker log timestamps are UTC – show them in the configured time zone (TZ)"""
    def conv(m):
        t = dt.datetime.strptime(m.group(1), "%Y-%m-%dT%H:%M:%S").replace(tzinfo=dt.timezone.utc)
        return t.astimezone().strftime("%Y-%m-%d %H:%M:%S")
    return LOG_TS.sub(conv, text)


def logs(tail=300, name=None):
    c = container(name)
    if c is None:
        return ""
    return local_ts(c.logs(tail=tail, timestamps=True).decode(errors="replace"))


def lifecycle(action):
    c = container()
    if c is None:
        return False, "container does not exist"
    getattr(c, action)()
    return True, "ok"


def ssh_check(alias, sudo=""):
    """test an SSH login as tentaclai; uses a throwaway container from the same image when tentaclai is stopped"""
    probe = "echo $(whoami)@$(hostname)"
    if sudo:
        probe += f"; {sudo} true 2>/dev/null && echo SUDO_OK || echo SUDO_FAILED"
    # fresh connection (no shared ControlMaster socket), so a changed host key or revoked key shows up at once
    cmd = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", "-o", "ControlMaster=no", "-o", "ControlPath=none", alias, probe]
    c = container()
    if c is not None and c.status == "running":
        return run(cmd)
    image = image_name(c) if c is not None else "local/tentaclai:latest"
    try:
        out = client().containers.run(image, cmd[1:], entrypoint="ssh", remove=True, stdout=True, stderr=True,
                                      volumes={SSH_DIR: {"bind": "/root/.ssh", "mode": "rw"}},
                                      extra_hosts={"host.docker.internal": "host-gateway"})
        return 0, out.decode(errors="replace")
    except docker.errors.ContainerError as e:
        return e.exit_status, (e.stderr or b"").decode(errors="replace")
    except docker.errors.ImageNotFound:
        return 1, "image local/tentaclai not built yet (Updates → build)"
    except Exception as e:  # noqa: BLE001
        return 1, str(e)


def sandbox_exec(cmd, stdin=None, timeout=60):
    """run a command in the running sandbox; optional stdin (e.g. a one-time password – never put into argv/env).
    Returns (exit_code, output)."""
    c = container()
    if c is None or c.status != "running":
        return 1, "tentaclAI is not running – start it on the overview first"
    api = client().api
    ex = api.exec_create(c.id, cmd, stdin=stdin is not None, tty=False)
    sock = api.exec_start(ex["Id"], socket=True)
    raw = getattr(sock, "_sock", sock)
    raw.settimeout(timeout)
    out = bytearray()
    try:
        if stdin is not None:
            raw.sendall(stdin if isinstance(stdin, bytes) else stdin.encode())
            raw.shutdown(socket.SHUT_WR)
        buf = b""
        while True:  # multiplexed stream: 8-byte header (stream, 0, 0, 0, size) + payload
            chunk = raw.recv(65536)
            if not chunk:
                break
            buf += chunk
            while len(buf) >= 8:
                size = int.from_bytes(buf[4:8], "big")
                if len(buf) < 8 + size:
                    break
                out += buf[8:8 + size]
                buf = buf[8 + size:]
    except OSError as e:
        out += f"\n{e}".encode()
    finally:
        try:
            raw.close()
        except OSError:
            pass
    code = api.exec_inspect(ex["Id"]).get("ExitCode")
    return (code if code is not None else 1), out.decode(errors="replace")


_version_cache = {}  # container id + start time -> VERSION of the image it runs


def sandbox_version():
    """tentaclAI version of the running sandbox (its image's /etc/tentaclai/VERSION), None if not running"""
    c = container()
    if c is None or c.status != "running":
        return None
    key = f"{c.id}:{c.attrs.get('State', {}).get('StartedAt', '')}"
    if key not in _version_cache:
        code, out = run(["cat", "/etc/tentaclai/VERSION"])
        _version_cache[key] = out.strip() if code == 0 else "unknown"
    return _version_cache[key]
