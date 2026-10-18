#!/usr/bin/env python3
"""SSH hosts on the command line – same logic as tentacontrol → Connections → SSH hosts.

  tentacontrol-ssh list
  tentacontrol-ssh add <alias> <user@host[:port]> [--jump <alias>] [--sudo | --sudo-cmd "<cmd>"]
  tentacontrol-ssh sudo <alias> [<cmd>|off]
  tentacontrol-ssh hostkey <alias>          fetch and confirm the host key again (e.g. after a reinstall)
  tentacontrol-ssh remove <alias> [--keep-key]
  tentacontrol-ssh test [alias]

On the Docker host: ./ssh-host.sh <command> … (runs this inside the tentacontrol container).
Renaming and editing other fields: tentacontrol → Connections → SSH hosts → Edit.
"""
import getpass
import sys

sys.path.insert(0, "/app")
from app import dockerops, sshhosts, sshops  # noqa: E402


def die(msg):
    sys.exit(f"Error: {msg}")


def ask(q):
    try:
        return input(q).strip().lower() in ("y", "yes", "j", "ja")
    except EOFError:
        return False


def confirm_hostkey(fields):
    print(f"→ host key of {fields['hostname']}:{fields.get('port') or 22}")
    try:
        scan = sshops.scan(fields)
    except RuntimeError as e:
        die(str(e))
    for fp in scan["fingerprints"]:
        print(f"   {fp}")
    if not ask("Fingerprint correct? [y/N] "):
        die("aborted")
    sshops.trust(scan["token"])
    print("   host key stored")


def test(alias):
    code, out = dockerops.ssh_check(alias, sshhosts.sudo_of(alias))
    lines = [line for line in out.strip().splitlines() if line.strip()]
    if code != 0:
        print(f"{alias:<14} ✗ {lines[-1] if lines else 'no answer'}")
        return False
    who = next((line for line in lines if "@" in line), alias)
    sudo = "  sudo ok" if "SUDO_OK" in lines else ("  sudo FAILED (needs NOPASSWD)" if "SUDO_FAILED" in lines else "")
    print(f"{alias:<14} ✓ {who}{sudo}")
    return True


def sync():
    dockerops.run(["svc", "sync"])  # refresh Claude's SSH host table


def cmd_add(args):
    if len(args) < 2:
        die("usage: add <alias> <user@host[:port]> [--jump <alias>] [--sudo | --sudo-cmd \"<cmd>\"]")
    alias, target, rest = args[0], args[1], args[2:]
    if "@" not in target:
        die("target as user@host[:port]")
    user, hostport = target.split("@", 1)
    host, _, port = hostport.partition(":")
    fields = {"alias": alias, "hostname": host, "user": user, "port": port or "22", "proxyjump": "", "sudo": ""}
    while rest:
        opt = rest.pop(0)
        if opt == "--jump" and rest:
            fields["proxyjump"] = rest.pop(0)
        elif opt == "--sudo":
            fields["sudo"] = fields["sudo"] or "sudo -n"
        elif opt == "--sudo-cmd" and rest:
            fields["sudo"] = rest.pop(0)
        else:
            die(f"unknown option: {opt}")
    try:
        sshhosts.validate(fields, aliases={h["alias"] for h in sshhosts.hosts()})
    except ValueError as e:
        die(str(e))
    confirm_hostkey(fields)
    pw = getpass.getpass(f"→ password of {user}@{host} to deploy the key (empty = the key is already there / add it by hand): ")
    if pw:
        try:
            print("   key", "added" if sshops.deploy(fields, pw) == "added" else "was already there")
        except RuntimeError as e:
            die(str(e))
    else:
        print(f"   add this line to ~{user}/.ssh/authorized_keys on {host} if it is not there yet:\n   {sshhosts.public_key()}")
    sshhosts.add(fields)
    print(f"→ saved as ssh/config.d/{alias}.conf")
    test(alias)
    sync()


def cmd_remove(args):
    if not args:
        die("usage: remove <alias> [--keep-key]")
    alias, h = args[0], sshhosts.get(args[0])
    if not h:
        die(f"host {alias} not found")
    refs = sshops.references(alias)
    if refs:
        print("   still used by " + ", ".join(f"{k} {n}" for k, n in refs))
    if not ask(f"Remove {alias}{'' if '--keep-key' in args else ' and its key on ' + h['user'] + '@' + h['hostname']}? [y/N] "):
        die("aborted")
    if "--keep-key" not in args:
        try:
            sshops.unauthorize(alias)
            print("   key removed on the host")
        except RuntimeError as e:
            print(f"   could not remove the key on the host ({e}) – remove it by hand")
    sshhosts.remove(alias)
    print(f"→ {alias} removed")
    sync()


def main(argv):
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(__doc__.strip())
        return 0
    cmd, args = argv[0], argv[1:]
    if cmd in ("list", "ls"):
        for h in sshhosts.hosts():
            extra = (f"  via {h['proxyjump']}" if h["proxyjump"] else "") + (f"  sudo: {h['sudo']}" if h["sudo"] else "")
            port = f":{h['port']}" if h["port"] != "22" else ""
            print(f"{h['alias']:<14} {h['user']}@{h['hostname']}{port}{extra}   ({h['file']})")
    elif cmd == "add":
        cmd_add(args)
    elif cmd in ("remove", "rm"):
        cmd_remove(args)
    elif cmd == "sudo" and args:
        if len(args) == 1:
            print(f"{args[0]}: {sshhosts.sudo_of(args[0]) or 'no sudo (commands run as the SSH user)'}")
            return 0
        try:
            sshhosts.set_sudo(args[0], "" if args[1] == "off" else args[1])
        except ValueError as e:
            die(str(e))
        test(args[0])
        sync()
    elif cmd == "hostkey" and args:
        h = sshhosts.get(args[0]) or die(f"host {args[0]} not found")
        confirm_hostkey(h)
        test(args[0])
    elif cmd == "test":
        ok = all([test(a) for a in (args or [h["alias"] for h in sshhosts.hosts()])])
        return 0 if ok else 1
    else:
        print(__doc__.strip())
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
