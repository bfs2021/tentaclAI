"""Everything that talks to the Docker daemon about the tentaclaude container."""
import datetime as dt
import json
import os
import re

import docker
from docker.errors import NotFound

from .config import CLAUDE_CONTAINER, HOME_DIR, SSH_DIR

_client = None


def client():
    global _client
    if _client is None:
        _client = docker.from_env()
    return _client


def container():
    try:
        return client().containers.get(CLAUDE_CONTAINER)
    except NotFound:
        return None


def run(cmd, workdir=None):
    """Run a command in the claude container, return (exit_code, text)."""
    c = container()
    if c is None or c.status != "running":
        return 1, "container is not running"
    try:
        r = c.exec_run(cmd, workdir=workdir, demux=False)
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


def image_name(c):
    """image reference of a container – c.image fails when the image was rebuilt/removed meanwhile"""
    return c.attrs.get("Config", {}).get("Image") or c.attrs.get("Image", "")[:19]


def status():
    c = container()
    if c is None:
        return {"exists": False, "running": False, "state": "not created"}
    c.reload()
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
    code, out = run(["claude", "--version"])
    return out.strip().split()[0] if code == 0 and out.strip() else None


def claude_latest():
    code, out = run(["timeout", "30", "npm", "view", "@anthropic-ai/claude-code", "version"])
    return out.strip() if code == 0 else None


def logged_in_claude():
    """`claude auth status` (JSON); falls back to the credentials file when the container is not running"""
    code, out = run(["claude", "auth", "status", "--json"])
    try:
        return bool(json.loads(out).get("loggedIn"))
    except ValueError:
        return os.path.exists(os.path.join(HOME_DIR, ".claude", ".credentials.json"))


def tmux_sessions():
    code, out = run(["tmux", "ls", "-F", "#{session_name}|#{session_attached}|#{session_created}"])
    if code != 0:
        return []
    res = []
    for line in out.strip().splitlines():
        parts = line.split("|")
        if len(parts) == 3:
            age = human_age(dt.datetime.fromtimestamp(int(parts[2]), dt.timezone.utc)) if parts[2].isdigit() else "–"
            res.append({"name": parts[0], "attached": parts[1] != "0", "age": age})
    return res


RC_URL = re.compile(r"https://claude\.ai/code\S*")


def rc_info():
    """state of the remote-control session parsed from its tmux pane (None = no session)"""
    code, out = run(["tmux", "capture-pane", "-p", "-J", "-t", "claude-rc", "-S", "-300"])
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
    elif urls and "Connected" in text:
        state = "connected"
    else:
        state = "starting"
    capacity = next((l.strip() for l in cur if l.strip().startswith("Capacity:")), "")
    return {"state": state, "url": urls[-1] if urls else "", "capacity": capacity,
            "tail": "\n".join(lines[-14:])}


def rc_restart():
    return run(["tmux", "respawn-pane", "-k", "-t", "claude-rc"])


def logs(tail=300):
    c = container()
    if c is None:
        return ""
    return c.logs(tail=tail, timestamps=True).decode(errors="replace")


def lifecycle(action):
    c = container()
    if c is None:
        return False, "container does not exist"
    getattr(c, action)()
    return True, "ok"


def ssh_check(alias, sudo=""):
    """test an SSH login as tentaclaude; uses a throwaway container from the same image when tentaclaude is stopped"""
    probe = "echo $(whoami)@$(hostname)"
    if sudo:
        probe += f"; {sudo} true 2>/dev/null && echo SUDO_OK || echo SUDO_FAILED"
    cmd = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", "-o", "ControlMaster=no", alias, probe]
    c = container()
    if c is not None and c.status == "running":
        return run(cmd)
    image = image_name(c) if c is not None else "local/tentaclaude:latest"
    try:
        out = client().containers.run(image, cmd[1:], entrypoint="ssh", remove=True, stdout=True, stderr=True,
                                      volumes={SSH_DIR: {"bind": "/root/.ssh", "mode": "rw"}},
                                      extra_hosts={"host.docker.internal": "host-gateway"})
        return 0, out.decode(errors="replace")
    except docker.errors.ContainerError as e:
        return e.exit_status, (e.stderr or b"").decode(errors="replace")
    except docker.errors.ImageNotFound:
        return 1, "image local/tentaclaude not built yet (Updates → build)"
    except Exception as e:  # noqa: BLE001
        return 1, str(e)
