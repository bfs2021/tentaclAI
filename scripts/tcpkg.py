#!/usr/bin/env python3
"""Build a module package (format 1) from an extension folder – unsigned; tentaclAI signs its own in tcmanager.

    scripts/tcpkg.py build <folder> [-o <dir or file>] [--version X]

<folder> is the package itself (…/<name>/ with __init__.py and extension.json). Name, title and version come from
extension.json (--version overrides it). Hidden files, __pycache__ and *.pyc are left out; any other file type than
tentacontrol accepts is an error. Writes <name>-<version>.tcpkg.zip. Format: docs/extensions.md → Packages.
Standard library only.
"""
import argparse
import hashlib
import json
import os
import re
import sys
import zipfile

# = control/app/packages.py (tests/test_tcpkg.py compares them)
FORMAT = 1
SUFFIXES = {".py", ".html", ".css", ".js", ".json", ".svg", ".png", ".woff2", ".md", ".txt"}
PLAIN_NAMES = {"LICENSE", "NOTICE"}
NAME_RE = re.compile(r"^[a-z][a-z0-9_]{1,40}$")
VERSION_RE = re.compile(r"^[0-9A-Za-z][0-9A-Za-z.+-]{0,39}$")


def collect(folder):
    """{"<name>/rel/path": absolute path} of every file that goes into the package"""
    name = os.path.basename(os.path.normpath(folder))
    out = {}
    for root, dirs, files in os.walk(folder):
        dirs[:] = sorted(d for d in dirs if not d.startswith(".") and d != "__pycache__")
        for fn in sorted(files):
            if fn.startswith(".") or fn.endswith(".pyc"):
                continue
            full = os.path.join(root, fn)
            rel = name + "/" + os.path.relpath(full, folder).replace(os.sep, "/")
            if os.path.islink(full):
                raise SystemExit(f"{rel}: symlinks can't go into a package")
            if os.path.splitext(fn)[1].lower() not in SUFFIXES and fn not in PLAIN_NAMES:
                raise SystemExit(f"{rel}: file type not allowed in a package (only {', '.join(sorted(SUFFIXES))})")
            out[rel] = full
    return out


def build(folder, out=None, version=None):
    folder = os.path.abspath(folder)
    name = os.path.basename(os.path.normpath(folder))
    if not NAME_RE.match(name):
        raise SystemExit(f"{name}: the folder name is the module name – lowercase letters, digits and _")
    try:
        with open(os.path.join(folder, "extension.json")) as f:
            ext = json.load(f)
    except (OSError, ValueError) as e:
        raise SystemExit(f"extension.json: {e}")
    if ext.get("name") != name:
        raise SystemExit(f"extension.json names {ext.get('name')!r}, the folder is {name!r}")
    if not os.path.isfile(os.path.join(folder, "__init__.py")):
        raise SystemExit("no __init__.py")
    version = version or str(ext.get("version") or "")
    if not VERSION_RE.match(version):
        raise SystemExit("version: set it in extension.json or with --version (letters, digits and . + -)")
    files = collect(folder)
    manifest = {"format": FORMAT, "name": name, "version": version, "requires_api": ext.get("requires_api"),
                "title": ext.get("title") or name, "files": {}}
    for rel, full in files.items():
        with open(full, "rb") as f:
            manifest["files"][rel] = hashlib.sha256(f.read()).hexdigest()
    target = out or "."
    if os.path.isdir(target):
        target = os.path.join(target, f"{name}-{version}.tcpkg.zip")
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("tcpkg.json", json.dumps(manifest, indent=1, sort_keys=True))
        for rel, full in files.items():
            z.write(full, rel)
    return target, manifest


def main(argv=None):
    ap = argparse.ArgumentParser(description="Build an unsigned tentaclAI module package.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="build <name>-<version>.tcpkg.zip from an extension folder")
    b.add_argument("folder")
    b.add_argument("-o", "--out", help="output folder or file (default: the current folder)")
    b.add_argument("--version", help="instead of the version in extension.json")
    a = ap.parse_args(argv)
    target, m = build(a.folder, a.out, a.version)
    print(f"{target}: {m['name']} {m['version']}, {len(m['files'])} files – unsigned")
    return 0


if __name__ == "__main__":
    sys.exit(main())
