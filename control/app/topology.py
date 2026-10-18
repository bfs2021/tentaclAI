"""Topology graph (prototype): tentaclaude → systems → services / SSH hosts, from the configuration only.

Docker stacks of a docker service can be loaded on demand (live, read-only `docker ps`).
"""
import json

from . import dockerops, services_store, sshhosts, systems_store


def graph():
    systems = {s["name"]: s for s in systems_store.load_all()}
    services = services_store.load_all()
    hosts = {h["alias"]: h for h in sshhosts.hosts()}
    nodes, edges = [], []

    def node(id_, label, kind, parent=None, **data):
        d = {"id": id_, "label": label, "kind": kind, **data}
        if parent:
            d["parent"] = parent
        nodes.append({"data": d})

    def edge(src, dst, label="", kind="link"):
        edges.append({"data": {"id": f"{src}->{dst}", "source": src, "target": dst, "label": label, "kind": kind}})

    node("core", "tentaclaude", "core", info="Claude Code sandbox – reaches everything below only through these connections.")
    for name, s in systems.items():
        node(f"sys:{name}", name, "system", info=s.get("description", ""), notes=s.get("notes", []),
             href=f"/systems/{name}", ssh=s.get("ssh_host", ""),
             members=[x["name"] for x in services if x["system"] == name and x["state"] != "off"])

    # SSH hosts sit inside the system that uses them as its machine
    host_system = {s["ssh_host"]: n for n, s in systems.items() if s.get("ssh_host")}
    for alias, h in hosts.items():
        node(f"ssh:{alias}", alias, "ssh", parent=f"sys:{host_system[alias]}" if alias in host_system else None,
             info=f"{h['user']}@{h['hostname']}" + (f":{h['port']}" if h["port"] != "22" else ""), sudo=h["sudo"], href="/ssh",
             system=host_system.get(alias, ""))
        edge("core", f"ssh:{alias}", "ssh", "ssh")

    for s in services:
        if s["state"] == "off":  # disabled services are not reachable for Claude → not part of the map
            continue
        sid = f"svc:{s['name']}"
        parent = f"sys:{s['system']}" if s["system"] in systems else None
        d = s["docker"]
        node(sid, s["name"], "service", parent=parent, state=s["state"], kinds=s["kinds"], info=s["description"],
             href=f"/services/{s['name']}", status=s["label"], system=s["system"] if s["system"] in systems else "",
             access=next((p["access"] for p in (d, s["git"], s["db"], s["files"]) if p), ""),
             url=s["base_url"] or (s["git"] or {}).get("url", "") or (s["web"] or {}).get("url", "") or (s["db"] or {}).get("target", "")
             or (s["files"] or {}).get("target", ""), missing=s["missing"],
             docker=f"{'local socket' if d['mode'] == 'socket' else 'ssh ' + d['target']} · {d['stacks_dir'] or '–'}" if d else "")
        fh = (s["data"].get("files") or {}).get("ssh_host") or (systems.get(s["system"], {}).get("ssh_host") if s["files"] else "")
        if d and d["mode"] == "ssh" and d["target"] in hosts:
            # docker over ssh: the path goes through the SSH host
            edge(f"ssh:{d['target']}", sid, "docker", "docker")
        elif s["files"] and fh in hosts:
            edge(f"ssh:{fh}", sid, "files", "docker")
        else:
            edge("core", sid, " + ".join(s["kinds"]), "service")
    return {"elements": nodes + edges}


def docker_stacks(name):
    """compose projects of a docker service with container counts (read-only `docker ps -a`)"""
    code, out = dockerops.run(["svc", "docker", name, "ps", "-a", "--format",
                               '{{json .}}'])
    if code != 0:
        raise RuntimeError((out.strip().splitlines() or ["error"])[-1][:200])
    stacks = {}
    for line in out.splitlines():
        try:
            c = json.loads(line)
        except ValueError:
            continue
        labels = dict(kv.split("=", 1) for kv in (c.get("Labels") or "").split(",") if "=" in kv)
        proj = labels.get("com.docker.compose.project") or "(no compose project)"
        st = stacks.setdefault(proj, {"name": proj, "running": 0, "total": 0, "containers": []})
        st["total"] += 1
        st["running"] += c.get("State") == "running"
        st["containers"].append({"name": c.get("Names", ""), "state": c.get("State", ""), "image": c.get("Image", ""),
                                 "status": c.get("Status", "")})
    return sorted(stacks.values(), key=lambda x: x["name"])
