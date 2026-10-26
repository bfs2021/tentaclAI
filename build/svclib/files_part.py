"""svc files: directories and files on SSH hosts."""
import os
import shutil
import subprocess
import sys
import shlex

import tclib  # shared with tentacontrol (lib/tclib.py)

from . import core, gate, safeguards


# ---------- directories / files on SSH hosts (files) ----------

def files_target(name, f):
    host = f.get("ssh_host") or sys.exit(f"svc: {name}: files.ssh_host missing (or a system with an SSH host)")
    root = (f.get("path") or "").rstrip("/") or sys.exit(f"svc: {name}: files.path missing")
    sudo = core.ssh_sudo(host) if f.get("sudo") else ""
    return host, root, sudo


def files_sub(root, sub):
    sub = (sub or "").strip()
    if sub.startswith("/") or ".." in sub.split("/"):
        sys.exit("svc: paths are relative to the service path and may not contain '..'")
    return root + ("/" + sub if sub and sub != "." else "")


def files_ssh(host, sudo, cmd):
    return subprocess.call(["ssh", host, (sudo + " " if sudo else "") + cmd])


def cmd_files(services, args):
    fs = core.part_services(services, "files")
    if not args:
        if not fs:
            print("No file services.")
            return 0
        print(f"{'Name':<16}{'Access':<8}{'Target':<44}Status")
        for n, s in fs.items():
            f = core.render(s["files"])
            print(f"{n:<16}{tclib.access(f.get('access')):<8}{(f.get('ssh_host') or '?') + ':' + f.get('path', ''):<44}{core.status(s)}")
        return 0
    if len(args) < 2:
        sys.exit("usage: svc files <name> ls|tree|cat|grep|pull|diff|push [path]")
    name, op, rest = args[0], args[1], args[2:]
    f = gate.get_part(services, name, "files")
    host, root, sudo = files_target(name, f)
    local = os.path.join("/workspace/files", name)
    if op == "ls":
        return files_ssh(host, sudo, f"ls -la --group-directories-first {shlex.quote(files_sub(root, rest[0] if rest else ''))}")
    if op == "tree":
        return files_ssh(host, sudo, f"find {shlex.quote(files_sub(root, rest[0] if rest else ''))} -maxdepth {int(f.get('tree_depth', 3))} "
                                     f"-not -path '*/.git/*' | sort | head -500")
    if op == "cat" and rest:
        return safeguards.scanned("file", name, "files", f"cat {rest[0]}",
                       ["ssh", host, (sudo + " " if sudo else "") + f"cat {shlex.quote(files_sub(root, rest[0]))}"])
    if op == "grep" and rest:
        return safeguards.scanned("file", name, "files", f"grep {rest[0]}",
                       ["ssh", host, (sudo + " " if sudo else "") + f"grep -rIn --color=never -m 20 -e {shlex.quote(rest[0])} "
                        f"{shlex.quote(files_sub(root, rest[1] if len(rest) > 1 else ''))} | head -300"])
    if op in ("pull", "diff", "push"):
        sub = rest[0] if rest else ""
        remote, dest = files_sub(root, sub), os.path.join(local, sub) if sub else local
        if op == "push" and gate.access_mode(f) == "read":
            gate.audit(name, "files", "read", f"push {sub or '.'} → {host}:{remote}", "blocked")
            sys.exit(f"svc: {name} is read-only – push is not allowed")
        is_dir = subprocess.run(["ssh", host, (sudo + " " if sudo else "") + f"test -d {shlex.quote(remote)}"]).returncode == 0
        src_r = remote + "/" if is_dir else remote
        base = ["rsync", "-a", "--exclude=.git/"] + ([f"--rsync-path={sudo} rsync"] if sudo else [])
        # root talks to the host (SSH key); the local side is copied as Claude through a private staging dir,
        # so root never reads or writes a path inside Claude's workspace (symlinks there can't redirect it)
        claude = core.as_user(core.CLAUDE_USER)
        stage = core.stage_dir()
        try:
            st = os.path.join(stage, "x") + ("/" if is_dir else "")
            if op == "pull":
                r = subprocess.call(base + [f"{host}:{src_r}", st])
                if r != 0:
                    return r
                core.give(stage, core.CLAUDE_USER)
                r = subprocess.call(["sh", "-c", 'mkdir -p "$1" && rsync -a "$2" "$3"', "sh", dest if is_dir else os.path.dirname(dest) or local,
                                     st, dest.rstrip("/") + "/" if is_dir else dest], **claude)
                if r == 0:
                    print(f"→ {dest}  (edit there; changes go back with: svc files {name} diff|push{' ' + sub if sub else ''})")
                return r
            core.give(stage, core.CLAUDE_USER)
            if subprocess.call(["test", "-e", dest], **claude) != 0:
                sys.exit(f"svc: {dest} not found – pull first")
            if subprocess.call(["rsync", "-a", "--exclude=.git/", dest.rstrip("/") + "/" if is_dir else dest, st], **claude) != 0:
                sys.exit(f"svc: could not read {dest}")
            out = base + ["--no-owner", "--no-group"]  # the staged copy belongs to Claude – keep the host's owners
            if op == "diff":
                return subprocess.call(out + ["-n", "-i", "--checksum", st, f"{host}:{src_r}"])
            return gate.guarded(name, "files", gate.access_mode(f), f"push {sub or '.'} → {host}:{remote}",
                           lambda: subprocess.call(out + ["-i", st, f"{host}:{src_r}"]))
        finally:
            shutil.rmtree(stage, ignore_errors=True)
    sys.exit("usage: svc files <name> ls|tree|cat <file>|grep <pattern> [path]|pull|diff|push [path]")


def files_test(name, f):
    host, root, sudo = files_target(name, f)
    r = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", host,
                        (sudo + " " if sudo else "") + f"sh -c 'if [ -d {shlex.quote(root)} ]; then echo dir $(ls -A {shlex.quote(root)} | wc -l); "
                        f"elif [ -e {shlex.quote(root)} ]; then echo file; else echo missing; fi'"],
                       capture_output=True, text=True, timeout=30)
    out = r.stdout.strip()
    if r.returncode != 0:
        return False, (r.stderr.strip().splitlines() or ["ssh failed"])[-1][:180]
    if out.startswith("dir"):
        return True, f"{host}:{root} – directory, {out.split()[1]} entries ({tclib.access(f.get('access'))}{', sudo' if sudo else ''})"
    if out == "file":
        return True, f"{host}:{root} – file ({tclib.access(f.get('access'))})"
    return False, f"{host}:{root} not found"
