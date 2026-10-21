"""Jobs: Claude tasks on a schedule, results as notifications (not the build jobs of jobs.py).

A job is a prompt with a schedule and a profile. The runner starts `claude -p` in the sandbox as user claude, one run at
a time, with a JSON schema for a traffic-light answer (ok / warn / alert) – that decides whether a notification goes out.

Profiles – the job's limit on top of each tentacle's own access (the stricter one wins):
- read-only: Claude Code only allows reading tools and `svc` (dontAsk), and svc treats every tentacle as ro. svc
  recognises the job by the marker tc-job=<run id> in the command line of the run's wrapper process (an ancestor of
  every svc call) plus job-runs.json (root's file).
- standard: like a terminal session – ro/rw/confirm of the tentacles apply; confirm waits for an approval in Activity.

State: jobs.json (definitions), job-history.json (last runs), job-runs.json (running now – read by svc).
"""
import asyncio
import json
import os
import re
import secrets
import shlex
import time
import uuid

import tclib

from . import dockerops, jsonstore, models, notify
from .config import CLAUDE_CONTAINER, DATA_DIR

JOBS = os.path.join(DATA_DIR, "jobs.json")
HISTORY = os.path.join(DATA_DIR, "job-history.json")
ACTIVE = os.path.join(DATA_DIR, "job-runs.json")
KEEP_RUNS = 300
CATCH_UP = 6 * 3600          # a run missed while tentaclAI was down is made up for if it is at most this old
QUOTA_SKIP = 90              # skip a scheduled run when a plan limit is at least this full
PROFILES = ("read-only", "standard")
NOTIFY = {"findings": "only on findings (warn, alert) and failures", "always": "every run", "failure": "only on failures"}
DAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,40}$")
TIME_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")

SCHEMA = {"type": "object", "additionalProperties": False, "required": ["status", "title", "summary"], "properties": {
    "status": {"type": "string", "enum": ["ok", "warn", "alert"],
               "description": "ok = nothing to do, warn = needs attention soon, alert = act now"},
    "title": {"type": "string", "description": "one line, e.g. '2 certificates expire within 14 days'"},
    "summary": {"type": "string", "description": "at most three sentences"},
    "details": {"type": "string", "description": "optional details (Markdown)"}}}
SYSTEM = ("This is an unattended scheduled job of tentaclAI named {name!r}; nobody watches the run and nobody can answer "
          "questions – decide on your own, keep it short. Your final answer is the JSON object of the given schema: "
          "status ok (nothing to do), warn (needs attention soon) or alert (act now), a one-line title, a summary of at "
          "most three sentences, optional details. If an action waits for an approval that doesn't come, say so in "
          "the summary.{ro}")
SYSTEM_RO = (" This run is read-only: tentacles can only be read, files in the sandbox can't be edited – report what "
             "should be done instead of trying it.")
READ_ONLY_TOOLS = "Read,Grep,Glob,WebFetch,WebSearch,Bash(svc:*)"

TEMPLATES = [
    {"name": "Patch report", "schedule": {"kind": "weekly", "day": 0, "time": "07:00"},
     "prompt": "Check every docker host for containers whose image has a newer version and for hosts with pending OS "
               "updates (PatchMon if it is a tentacle, otherwise read-only commands). List what is outdated, most "
               "important first. warn if updates are pending, alert for security updates older than a week."},
    {"name": "Certificate expiry", "schedule": {"kind": "daily", "time": "07:00"},
     "prompt": "Check the TLS certificates of all web services and API endpoints of the tentacles. warn if one expires "
               "within 21 days, alert within 7 days or if one is already expired or invalid."},
    {"name": "Disk space", "schedule": {"kind": "daily", "time": "06:30"},
     "prompt": "Check the free disk space on every docker and SSH host (df -h). warn above 80 % used, alert above 90 % – "
               "name the filesystems and the biggest docker items if it is a docker host."},
    {"name": "Backup check", "schedule": {"kind": "daily", "time": "08:00"},
     "prompt": "Check that last night's backups ran: look for the backup jobs described in the site rules (systemd "
               "timers, logs, restic snapshots). warn if a backup is older than 26 hours, alert if one failed."},
    {"name": "Container health", "schedule": {"kind": "every", "hours": 6},
     "prompt": "List containers on every docker host that are unhealthy, restarting or exited although they should run. "
               "warn for single containers, alert if a whole stack is down."},
]


# ---------------------------------------------------------------- @tentacle mentions

MENTION_RE = re.compile(r"(?<![\w@.])@([a-z0-9][a-z0-9_-]{0,40})\b")


def tentacles():
    """name → {kind, parts {part: access}, enabled} for services and SSH hosts (what a task can mention)"""
    from .config import SERVICES_DIR, SSH_DIR, STACK_DIR
    out = {}
    for name, svc in tclib.load_services(SERVICES_DIR, tclib.load_systems(STACK_DIR)).items():
        parts = {p: str(svc[p].get("access", "confirm" if p == "smtp" else ("rw" if p == "api" else "ro")))
                 for p in tclib.PARTS if isinstance(svc.get(p), dict) and p != "mcp"}
        if "mcp" in svc:
            parts["mcp"] = "tools"
        out[name] = {"kind": "service", "parts": parts, "enabled": tclib.enabled(svc), "description": svc.get("description", "")}
    conf = os.path.join(SSH_DIR, "config.d")
    for h in tclib.ssh_hosts(conf):
        out.setdefault(h["alias"], {"kind": "ssh", "parts": {"ssh": tclib.ssh_access(conf, h["alias"])}, "enabled": True,
                                    "description": f"SSH host {h.get('hostname') or ''}".strip()})
    return out


def mentions(prompt):
    seen = []
    for m in MENTION_RE.findall(prompt or ""):
        if m not in seen:
            seen.append(m)
    return seen


def check_mentions(job, known=None):
    """(errors, warnings) for the tentacles a task names – errors stop the save"""
    known = tentacles() if known is None else known
    errors, warnings = [], []
    ro = job.get("profile", "read-only") != "standard"
    names = mentions(job.get("prompt"))
    for n in names:
        t = known.get(n)
        if t is None:
            errors.append(f"@{n} is no service or SSH host")
            continue
        if not t["enabled"]:
            warnings.append(f"@{n} is disabled – the job can't use it")
        writes = [p for p, a in t["parts"].items() if a in ("rw", "confirm")]
        if ro and "smtp" in t["parts"]:
            warnings.append(f"@{n} can't send mail in a read-only job")
        elif not ro and any(t["parts"][p] == "confirm" for p in writes):
            warnings.append(f"@{n} asks for approval ({', '.join(p for p in writes if t['parts'][p] == 'confirm')}) – "
                            "the run waits up to 10 min, at night nobody answers")
        if job.get("scope") and "mcp" in t["parts"]:
            warnings.append(f"@{n}: MCP tools are off in a job limited to its tentacles – it can use the other parts")
    if job.get("scope") and not names:
        errors.append("“only the tentacles named in the task” needs at least one @name in the task")
    return errors, warnings


def mention_context(job, known=None):
    """a line per mentioned tentacle for Claude – so it doesn't search the catalog first"""
    known = tentacles() if known is None else known
    lines = []
    for n in mentions(job.get("prompt")):
        t = known.get(n)
        if t:
            parts = ", ".join(f"{p} {a}" for p, a in t["parts"].items())
            lines.append(f"@{n} = {'SSH host' if t['kind'] == 'ssh' else 'service'} {n} ({parts})"
                         + (f" – {t['description']}" if t.get("description") else ""))
    return lines


# ---------------------------------------------------------------- schedules

def _field(spec, lo, hi):
    """one cron field → set of ints: *, n, a-b, lists, /step"""
    out = set()
    for part in spec.split(","):
        rng, _, step = part.partition("/")
        step = int(step) if step else 1
        if rng == "*":
            a, b = lo, hi
        elif "-" in rng:
            a, b = (int(x) for x in rng.split("-", 1))
        else:
            a = b = int(rng)
            if step > 1:
                b = hi
        if not (lo <= a <= b <= hi) or step < 1:
            raise ValueError(f"cron: {part} out of range {lo}-{hi}")
        out.update(range(a, b + 1, step))
    return out


def parse_cron(expr):
    """minute hour day-of-month month day-of-week (0 or 7 = Sunday)"""
    f = expr.split()
    if len(f) != 5:
        raise ValueError("cron: five fields – minute hour day month weekday")
    try:
        mi, h, dom, mon, dow = (_field(f[0], 0, 59), _field(f[1], 0, 23), _field(f[2], 1, 31), _field(f[3], 1, 12),
                                _field(f[4].replace("7", "0") if f[4] == "7" else f[4], 0, 7))
    except ValueError as e:
        raise ValueError(str(e) if str(e).startswith("cron") else f"cron: {e}")
    dow = {d % 7 for d in dow}
    return {"min": mi, "hour": h, "dom": dom, "mon": mon, "dow": dow, "dom_any": f[2] == "*", "dow_any": f[4] == "*"}


def _cron_day_ok(c, t):
    wd = (t.tm_wday + 1) % 7  # cron: 0 = Sunday
    if c["dom_any"] or c["dow_any"]:
        return t.tm_mday in c["dom"] and wd in c["dow"]
    return t.tm_mday in c["dom"] or wd in c["dow"]  # cron: either day field matches when both are set


def next_run(sched, after, last=None):
    """next start time (epoch, local time) strictly after `after`"""
    k = sched.get("kind")
    base = int(after // 60 * 60) + 60
    if k == "every":
        step = int(sched["hours"]) * 3600
        return max(base, int(last + step)) if last else base
    if k == "cron":
        c = parse_cron(sched["expr"])
        t = base
        for _ in range(400 * 24 * 60):
            lt = time.localtime(t)
            if lt.tm_mon not in c["mon"] or not _cron_day_ok(c, lt):
                t = int(time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday + 1, 0, 0, 0, 0, 0, -1)))
                continue
            if lt.tm_hour in c["hour"] and lt.tm_min in c["min"]:
                return t
            t += 60
        raise ValueError("cron: no run within a year")
    if k == "hourly":
        m = int(sched.get("minute", 0))
        lt = time.localtime(base)
        t = int(time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, lt.tm_hour, m, 0, 0, 0, -1)))
        return t if t >= base else t + 3600
    hh, mm = (int(x) for x in sched["time"].split(":"))
    lt = time.localtime(base)
    for d in range(0, 9):
        t = int(time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday + d, hh, mm, 0, 0, 0, -1)))
        if t >= base and (k == "daily" or time.localtime(t).tm_wday == int(sched["day"])):
            return t
    raise ValueError("schedule: no next run")


def describe(sched):
    k = sched.get("kind")
    if k == "hourly":
        return f"every hour at :{int(sched.get('minute', 0)):02d}"
    if k == "daily":
        return f"daily at {sched['time']}"
    if k == "weekly":
        return f"every {DAYS[int(sched['day'])]} at {sched['time']}"
    if k == "every":
        return f"every {sched['hours']} h"
    if k == "cron":
        return f"cron {sched['expr']}"
    return "?"


def clean_schedule(f):
    """form values → a validated schedule"""
    k = f.get("kind", "daily")
    if k == "hourly":
        m = int(f.get("minute") or 0)
        if not 0 <= m <= 59:
            raise ValueError("minute: 0–59")
        return {"kind": k, "minute": m}
    if k in ("daily", "weekly"):
        t = (f.get("time") or "").strip()
        if not TIME_RE.match(t):
            raise ValueError("time: HH:MM")
        s = {"kind": k, "time": t}
        if k == "weekly":
            d = int(f.get("day") or 0)
            if not 0 <= d <= 6:
                raise ValueError("weekday")
            s["day"] = d
        return s
    if k == "every":
        h = int(f.get("hours") or 0)
        if not 1 <= h <= 168:
            raise ValueError("every: 1–168 hours")
        return {"kind": k, "hours": h}
    if k == "cron":
        expr = " ".join((f.get("expr") or "").split())
        parse_cron(expr)
        return {"kind": k, "expr": expr}
    raise ValueError("unknown schedule")


# ---------------------------------------------------------------- definitions and history

def load():
    return jsonstore.load(JOBS) or {}


def history():
    h = jsonstore.load(HISTORY)
    return h if isinstance(h, list) else []


def _save_history(h):
    jsonstore.save(HISTORY, h[-KEEP_RUNS:], indent=None)


def get(jid):
    return load().get(jid)


RESERVED = {"new", "save", "runs"}  # paths of the Jobs pages


def slug(name):
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40] or "job"
    return f"{s}-job" if s in RESERVED else s


def save(form, jid=None):
    """create or update a job from form values; returns its id"""
    jobs = load()
    name = (form.get("name") or "").strip()
    prompt = (form.get("prompt") or "").replace("\r\n", "\n").strip()
    if not name or len(name) > 60:
        raise ValueError("name: 1–60 characters")
    if not prompt or len(prompt) > 20000:
        raise ValueError("prompt: 1–20000 characters")
    profile = form.get("profile") or "read-only"
    if profile not in PROFILES:
        raise ValueError("unknown profile")
    rule = form.get("notify") or "findings"
    if rule not in NOTIFY:
        raise ValueError("unknown notification rule")
    model = (form.get("model") or "").strip().lower()
    if not tclib.valid_model(model):
        raise ValueError("model: an alias (sonnet, opus, haiku) or a model id")
    effort = form.get("effort") or ""
    if effort not in ("",) + tclib.EFFORTS:
        raise ValueError("unknown effort")
    try:
        minutes = int(form.get("max_minutes") or 20)
    except ValueError:
        raise ValueError("max. runtime: minutes")
    if not 1 <= minutes <= 120:
        raise ValueError("max. runtime: 1–120 minutes")
    sched = clean_schedule(form)
    scope = bool(form.get("scope"))
    errors, _ = check_mentions({"prompt": prompt, "profile": profile, "scope": scope})
    if errors:
        raise ValueError("; ".join(errors))
    if jid is None:
        jid = slug(name)
        base, n = jid, 2
        while jid in jobs:
            jid, n = f"{base}-{n}", n + 1
    elif jid not in jobs:
        raise ValueError("unknown job")
    old = jobs.get(jid, {})
    job = dict(old, name=name, prompt=prompt, profile=profile, notify=rule, model=model, effort=effort, scope=scope,
               max_minutes=minutes, schedule=sched, enabled=old.get("enabled", True), created=old.get("created", time.time()))
    if old.get("schedule") != sched or "next" not in old:
        job["next"] = next_run(sched, time.time(), old.get("last"))
    jobs[jid] = job
    jsonstore.save(JOBS, jobs)
    return jid


def set_enabled(jid, on):
    jobs = load()
    if jid not in jobs:
        raise ValueError("unknown job")
    jobs[jid]["enabled"] = bool(on)
    if on:
        jobs[jid]["next"] = next_run(jobs[jid]["schedule"], time.time(), jobs[jid].get("last"))
    jsonstore.save(JOBS, jobs)


def clone(jid):
    """a paused copy (so it doesn't run twice by accident) – returns the new id"""
    jobs = load()
    if jid not in jobs:
        raise ValueError("unknown job")
    src = jobs[jid]
    name = f"{src['name'][:53]} (copy)"
    new = slug(name)
    base, n = new, 2
    while new in jobs:
        new, n = f"{base}-{n}", n + 1
    jobs[new] = dict({k: v for k, v in src.items() if k not in ("last", "next")}, name=name, enabled=False,
                     created=time.time(), next=next_run(src["schedule"], time.time()))
    jsonstore.save(JOBS, jobs)
    return new


def delete(jid):
    jobs = load()
    if jobs.pop(jid, None) is None:
        raise ValueError("unknown job")
    jsonstore.save(JOBS, jobs)


# ---------------------------------------------------------------- running

def claude_argv(job, run_id, session_id):
    """the claude command line for one run – prompt right after -p (the tool lists take several values)"""
    m = tclib.models(models.load())
    model = job.get("model") or m.get("main") or ""
    effort = job.get("effort") or m.get("effort") or ""
    ro = job.get("profile") != "standard"
    system = SYSTEM.format(name=job["name"], ro=SYSTEM_RO if ro else "")
    context = mention_context(job)
    if context:
        system += " Tentacles named in the task (@name): " + "; ".join(context) + "."
    if job.get("scope"):
        system += (" This run may only use those tentacles – svc refuses every other one, and MCP servers are off. "
                   "If the task needs another tentacle, say so in the summary.")
    argv = ["claude", "-p", job["prompt"], "--output-format", "json", "--session-id", session_id,
            "--json-schema", json.dumps(SCHEMA), "--append-system-prompt", system]
    if ro or job.get("scope"):
        argv += ["--strict-mcp-config"]  # no MCP servers: their tools could reach other services past svc
    if model:
        argv += ["--model", model]
    if effort:
        argv += ["--effort", effort]
    if ro:
        argv += ["--permission-mode", "dontAsk", "--allowedTools", READ_ONLY_TOOLS, "--disallowedTools", "Edit,Write,NotebookEdit"]
    else:
        argv += ["--permission-mode", "bypassPermissions"]
    return argv


def parse_output(out):
    """the JSON line claude -p prints last → (status, fields)"""
    for line in reversed((out or "").strip().splitlines()):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            d = json.loads(line)
        except ValueError:
            continue
        if d.get("type") != "result":
            continue
        so = d.get("structured_output") if isinstance(d.get("structured_output"), dict) else {}
        fields = {"result": so or {"title": "", "summary": str(d.get("result") or "")[:4000]},
                  "cost": d.get("total_cost_usd"), "turns": d.get("num_turns"),
                  "denials": len(d.get("permission_denials") or []), "session": d.get("session_id")}
        if d.get("is_error") or d.get("subtype") != "success":
            return "failed", dict(fields, error=str(d.get("result") or d.get("subtype") or "error")[:500])
        if so.get("status") not in ("ok", "warn", "alert"):
            return "done", dict(fields, result=dict(fields["result"], status="warn",
                                                    title=fields["result"].get("title") or "no structured answer"))
        return "done", fields
    tail = (out or "").strip()[-500:]
    return "failed", {"error": tail or "no output"}


def should_notify(job, run):
    rule = job.get("notify", "findings")
    if run["status"] in ("failed", "timeout"):
        return True
    if run["status"] != "done":
        return False
    return rule == "always" or (rule == "findings" and (run.get("result") or {}).get("status") in ("warn", "alert"))


def _register(run_id, job):
    act = jsonstore.load(ACTIVE) or {}
    act[run_id] = {"job": job["name"], "profile": job.get("profile", "read-only"), "started": time.time()}
    if job.get("scope"):
        act[run_id]["allow"] = mentions(job.get("prompt"))  # svc refuses every other tentacle
    jsonstore.save(ACTIVE, act, mode=0o600)


def _unregister(run_id):
    act = jsonstore.load(ACTIVE) or {}
    if act.pop(run_id, None) is not None:
        jsonstore.save(ACTIVE, act, mode=0o600)


def running_job_name(run_id):
    """job name of a run (for the origin of approvals and audit entries)"""
    act = jsonstore.load(ACTIVE) or {}
    if run_id in act:
        return act[run_id].get("job", "")
    return next((r.get("name", "") for r in reversed(history()) if r.get("id") == run_id), "")


def _exec(argv, run_id, minutes):
    """run in the sandbox as claude, wait, return (exit code, stdout+stderr)"""
    c = dockerops.container()
    if c is None or c.status != "running":
        return None, "tentaclAI is not running"
    # the sh stays as the job's top process: its command line carries the marker svc looks for (tc-job=<run>)
    wrapped = ["sh", "-c", 'r="${0#tc-job=}"; timeout -k 20 "$@" & echo $! > "/tmp/.tc-job-$r"; wait $!', f"tc-job={run_id}",
               str(minutes * 60)] + argv
    api = dockerops.client().api
    try:
        e = api.exec_create(CLAUDE_CONTAINER, wrapped, user=dockerops.CLAUDE_USER, workdir="/workspace",
                            environment={"LANG": "C.UTF-8"})
        out = api.exec_start(e["Id"]).decode(errors="replace")
        code = api.exec_inspect(e["Id"]).get("ExitCode")
    except Exception as ex:  # noqa: BLE001
        return None, str(ex)
    finally:
        dockerops.run(["rm", "-f", f"/tmp/.tc-job-{run_id}"], user=dockerops.CLAUDE_USER)
    return code, out


def cancel(run_id):
    """stop a running run (SIGTERM to its timeout wrapper, which passes it on to claude)"""
    if not re.fullmatch(r"[a-z0-9]{6,40}", run_id or ""):
        raise ValueError("unknown run")
    code, out = dockerops.run(["sh", "-c", 'f="/tmp/.tc-job-$0"; [ -f "$f" ] && kill -TERM "$(cat "$f")"', run_id],
                              user=dockerops.CLAUDE_USER)
    if code != 0:
        raise ValueError("not running")
    _canceled.add(run_id)


_canceled = set()
_queue = None
_current = {}  # run id → job id of the run in progress


def quota_full():
    from .usage import plan
    return next((f"{lim['window']} limit at {lim['percent']} %" for lim in plan().get("limits", [])
                 if lim.get("percent", 0) >= QUOTA_SKIP), "")


def run_now(jid, trigger="manual"):
    """queue a run; returns the run id"""
    job = get(jid)
    if job is None:
        raise ValueError("unknown job")
    if _queue is None:
        raise ValueError("the job runner is not started")
    if jid in _current.values() or any(r.get("job") == jid and r["status"] == "queued" for r in history()):
        raise ValueError("this job is already queued or running")
    run_id = secrets.token_hex(6)
    h = history()
    h.append({"id": run_id, "job": jid, "name": job["name"], "profile": job.get("profile", "read-only"),
              "trigger": trigger, "status": "queued", "queued": time.time()})
    _save_history(h)
    _queue.put_nowait((run_id, jid))
    return run_id


def _update_run(run_id, **fields):
    h = history()
    for r in h:
        if r["id"] == run_id:
            r.update(fields)
    _save_history(h)


def execute(run_id, jid):
    """one run, blocking (in a thread)"""
    job = get(jid)
    if job is None:
        _update_run(run_id, status="failed", error="job was deleted", ended=time.time())
        return
    started = time.time()
    _update_run(run_id, status="running", started=started)
    _current[run_id] = jid
    _register(run_id, job)
    try:
        code, out = _exec(claude_argv(job, run_id, str(uuid.uuid4())), run_id, int(job.get("max_minutes", 20)))
    finally:
        _unregister(run_id)
        _current.pop(run_id, None)
    if run_id in _canceled:
        _canceled.discard(run_id)
        status, fields = "canceled", {}
    elif code == 124:
        status, fields = "timeout", {"error": f"stopped after {job.get('max_minutes', 20)} min"}
    else:
        status, fields = parse_output(out) if code is not None else ("failed", {"error": out[:500]})
    run = dict(fields, status=status, ended=time.time(), seconds=round(time.time() - started))
    _update_run(run_id, **run)
    if should_notify(job, run):
        r = run.get("result") or {}
        icon = {"ok": "✓", "warn": "⚠", "alert": "‼"}.get(r.get("status"), "✗")
        title = f"{icon} {job['name']}: " + (r.get("title") or run.get("error") or status)[:120]
        notify.emit("job_failed" if status in ("failed", "timeout") else "job_result", title,
                    (r.get("summary") or run.get("error") or "")[:600], key=f"job:{run_id}",
                    link=notify.control_link(f"/jobs#run-{run_id}"))


async def worker():
    global _queue
    _queue = asyncio.Queue()
    for r in history():  # runs that were queued or running when tentacontrol stopped
        if r["status"] in ("queued", "running"):
            _update_run(r["id"], status="failed", error="tentacontrol restarted during the run", ended=time.time())
    jsonstore.save(ACTIVE, {}, mode=0o600)
    while True:
        run_id, jid = await _queue.get()
        try:
            await asyncio.to_thread(execute, run_id, jid)
        except Exception as e:  # noqa: BLE001 – one broken run must not stop the runner
            print(f"jobs: {e}", flush=True)
            _update_run(run_id, status="failed", error=str(e)[:300], ended=time.time())


def due(now=None):
    """jobs whose start time has come → [(id, 'schedule' | 'skipped: …')]; moves their next start on"""
    now = now or time.time()
    jobs, out, changed = load(), [], False
    for jid, job in jobs.items():
        nxt = job.get("next")
        if not job.get("enabled", True) or not nxt or nxt > now:
            continue
        if now - nxt > CATCH_UP:
            out.append((jid, "skipped: tentaclAI was not running at the scheduled time"))
        else:
            out.append((jid, "schedule"))
        job["last"] = now
        job["next"] = next_run(job["schedule"], now, now)
        changed = True
    if changed:
        jsonstore.save(JOBS, jobs)
    return out


def _skip(jid, why):
    job = get(jid) or {}
    h = history()
    h.append({"id": secrets.token_hex(6), "job": jid, "name": job.get("name", jid), "profile": job.get("profile", ""),
              "trigger": "schedule", "status": "skipped", "error": why, "queued": time.time(), "ended": time.time()})
    _save_history(h)


async def scheduler():
    while True:
        try:
            for jid, kind in due():
                if kind != "schedule":
                    _skip(jid, kind.split(": ", 1)[1])
                elif quota_full():
                    _skip(jid, f"Claude's {quota_full()}")
                else:
                    try:
                        run_now(jid, "schedule")
                    except ValueError as e:
                        _skip(jid, str(e))
        except Exception as e:  # noqa: BLE001
            print(f"jobs scheduler: {e}", flush=True)
        await asyncio.sleep(30)


SEEN = os.path.join(DATA_DIR, "jobs-seen.json")


def finding(r):
    return r.get("status") in ("failed", "timeout") or (r.get("status") == "done" and (r.get("result") or {}).get("status") in ("warn", "alert"))


def unseen():
    """runs with a finding or a failure that ended after the Jobs page was last opened (the sidebar badge)"""
    seen = (jsonstore.load(SEEN) or {}).get("ts", 0)
    return sum(1 for r in history() if finding(r) and (r.get("ended") or 0) > seen)


def state_token():
    """changes whenever a run is queued, starts, ends – or a job is added, edited, paused"""
    import hashlib
    h = history()[-50:]
    raw = json.dumps([(r.get("id"), r.get("status")) for r in h]) + str(os.path.getmtime(JOBS) if os.path.exists(JOBS) else 0)
    return hashlib.sha1(raw.encode()).hexdigest()[:12]


def mark_seen():
    jsonstore.save(SEEN, {"ts": time.time()})


def overview():
    """jobs for the page: definition + next start in words + last run"""
    h = history()
    rows = []
    for jid, job in sorted(load().items(), key=lambda kv: kv[1]["name"].lower()):
        runs = [r for r in h if r.get("job") == jid]
        rows.append(dict(job, id=jid, when=describe(job["schedule"]), last_run=runs[-1] if runs else None,
                         runs=list(reversed(runs[-10:])), busy=jid in _current.values() or any(r["status"] in ("queued", "running") for r in runs)))
    return rows


def rw_tentacles():
    """names of tentacles a standard job could write to without asking (shown as a warning in the editor)"""
    from .config import SERVICES_DIR, SSH_DIR, STACK_DIR
    out = []
    for name, svc in tclib.load_services(SERVICES_DIR, tclib.load_systems(STACK_DIR)).items():
        if tclib.enabled(svc) and any(isinstance(svc.get(p), dict) and svc[p].get("access") == "rw"
                                      for p in ("docker", "git", "db", "files", "smtp", "imap")):
            out.append(name)
    conf = os.path.join(SSH_DIR, "config.d")
    for h in tclib.ssh_hosts(conf):
        if tclib.ssh_access(conf, h["alias"]) == "rw":
            out.append(f"ssh {h['alias']}")
    return sorted(set(out))


def shell_preview(job):
    """the command line as text – for the editor's 'what runs' hint"""
    return shlex.join(claude_argv(job, "RUN", "SESSION")[:3]) + " …"
