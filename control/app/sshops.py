"""SSH host operations that run inside the sandbox: fetch/confirm host keys, deploy the key with a one-time
password, remove it again, and keep references (services, systems) in sync when an alias changes."""
import json
import secrets
import time

from . import dockerops, services_store, sshhosts, systems_store

_PENDING = {}  # token -> scanned host key, valid for 10 minutes
SSH_BASE = ["-o", "ControlMaster=no", "-o", "ControlPath=none", "-o", "ConnectTimeout=10"]


def _target_args(f):
    args = ["-p", str(f.get("port") or "22")]
    if f.get("hostkeyalias"):
        args += ["-o", f"HostKeyAlias={f['hostkeyalias']}"]
    if f.get("proxyjump"):
        args += ["-J", f["proxyjump"]]
    return args + [f"{f['user']}@{f['hostname']}"]


def scan(fields):
    """connect without authenticating and collect the host key; returns dict with token, fingerprints – or raises"""
    # the temporary known_hosts file only exists in the shell, so its option lives in the script
    script = ('t=$(mktemp); ssh -o UserKnownHostsFile="$t" "$@" true >/dev/null 2>"$t.err"; '
              'if [ -s "$t" ]; then echo "##KEYS"; cat "$t"; echo "##FP"; ssh-keygen -lf "$t"; '
              'else echo "##ERR"; grep -v "^Warning: Permanently" "$t.err" | tail -3; fi; rm -f "$t" "$t.err"')
    args = SSH_BASE + ["-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=accept-new", "-o", "GlobalKnownHostsFile=/dev/null",
                       "-o", "HashKnownHosts=no", "-o", "PreferredAuthentications=none"]
    code, out = dockerops.sandbox_exec(["bash", "-c", script, "scan"] + args + _target_args(fields), timeout=40)
    if "##KEYS" not in out:
        err = out.split("##ERR", 1)[-1].strip() or "no answer"
        raise RuntimeError(f"could not fetch the host key: {err[-300:]}")
    keys, fps = out.split("##KEYS", 1)[1].split("##FP", 1)
    lines = [l for l in keys.strip().splitlines() if l.strip()]
    fps = [l.strip() for l in fps.strip().splitlines() if l.strip()]
    token = secrets.token_urlsafe(16)
    for k in [k for k, v in _PENDING.items() if time.time() - v["ts"] > 600]:
        _PENDING.pop(k, None)
    _PENDING[token] = {"name": sshhosts.known_name(fields), "lines": lines, "ts": time.time()}
    return {"token": token, "fingerprints": fps, "name": sshhosts.known_name(fields)}


def trust(token):
    """store a confirmed host key in ssh/known_hosts (replacing old entries for that name)"""
    p = _PENDING.pop(token, None)
    if not p or time.time() - p["ts"] > 600:
        raise RuntimeError("host key check expired – please fetch the key again")
    code, out = dockerops.sandbox_exec(
        ["bash", "-c", 'ssh-keygen -R "$1" -f ~/.ssh/known_hosts >/dev/null 2>&1; cat >> ~/.ssh/known_hosts; rm -f ~/.ssh/known_hosts.old',
         "trust", p["name"]], stdin="\n".join(p["lines"]) + "\n", timeout=20)
    if code != 0:
        raise RuntimeError(f"could not store the host key: {out.strip()[-200:]}")


def deploy(fields, password):
    """append the public key on the target, logging in once with a password (passed via stdin, never stored)"""
    pub = sshhosts.public_key()
    if not pub:
        raise RuntimeError("public key ssh/keys/tentaclai_ed25519.pub missing")
    body = pub.split()[1]
    remote = ("umask 077; mkdir -p ~/.ssh; touch ~/.ssh/authorized_keys; "
              f"grep -qF '{body}' ~/.ssh/authorized_keys && echo TC_ALREADY || "
              f"{{ echo '{pub}' >> ~/.ssh/authorized_keys && echo TC_ADDED; }}")
    args = SSH_BASE + ["-n", "-o", "StrictHostKeyChecking=yes", "-o", "PubkeyAuthentication=no",
                       "-o", "PreferredAuthentications=password,keyboard-interactive", "-o", "NumberOfPasswordPrompts=1"]
    code, out = dockerops.sandbox_exec(["sshpass", "-f", "/dev/stdin", "ssh"] + args + _target_args(fields) + [remote],
                                       stdin=password + "\n", timeout=40)
    if "TC_ADDED" in out:
        return "added"
    if "TC_ALREADY" in out:
        return "already"
    reason = {5: "wrong password", 6: "host key not confirmed"}.get(code) or (out.strip().splitlines() or ["login failed"])[-1]
    raise RuntimeError(f"could not deploy the key: {reason}")


def unauthorize(alias):
    """remove tentaclAI's key from the target (logs in with that key one last time)"""
    pub = sshhosts.public_key() or ""
    body = pub.split()[1] if len(pub.split()) > 1 else ""
    if not body:
        raise RuntimeError("public key missing")
    remote = (f"f=~/.ssh/authorized_keys; [ -f $f ] || exit 0; grep -vF '{body}' $f > $f.tc; cat $f.tc > $f; rm -f $f.tc; echo TC_REMOVED")
    code, out = dockerops.sandbox_exec(["ssh", "-o", "BatchMode=yes"] + SSH_BASE + [alias, remote], timeout=30)
    if "TC_REMOVED" not in out:
        raise RuntimeError((out.strip().splitlines() or ["ssh failed"])[-1][:200])


def references(alias):
    """services, systems and other SSH hosts (ProxyJump) that use this SSH host"""
    refs = [("host", h["alias"]) for h in sshhosts.hosts() if h.get("proxyjump") == alias]
    for s in services_store.load_all():
        d = s["data"]
        if (d.get("docker") or {}).get("ssh_host") == alias or (d.get("files") or {}).get("ssh_host") == alias:
            refs.append(("service", s["name"]))
    for x in systems_store.load_all():
        if x.get("ssh_host") == alias:
            refs.append(("system", x["name"]))
    return refs


def rename_references(old, new):
    for kind, name in references(old):
        if kind == "host":
            h = sshhosts.get(name)
            sshhosts.update(name, dict(h, proxyjump=new))
        elif kind == "service":
            data = json.loads(services_store.read_raw(name))
            for part in ("docker", "files"):
                if (data.get(part) or {}).get("ssh_host") == old:
                    data[part]["ssh_host"] = new
            services_store.save_raw(name, json.dumps(data, ensure_ascii=False))
        else:
            x = systems_store.get(name)
            members = [s["name"] for s in services_store.load_all() if s["data"].get("system") == name]
            systems_store.save(name, x.get("description", ""), new, x.get("notes", []), members)
