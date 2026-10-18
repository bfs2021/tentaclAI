"""Read/update a docker-compose .env file while keeping comments, order and file mode."""
import os
import re
import tempfile

import tclib

from .config import ENV_FILE

LINE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$")


def read_env(path: str = ENV_FILE) -> dict:
    return tclib.read_env(path)


def _quote(v: str) -> str:
    # single quotes stop compose from interpolating $ in values (e.g. passwords)
    if v == "" or re.fullmatch(r"[A-Za-z0-9_./:@+,=-]*", v):
        return v
    if "'" in v or "\n" in v:
        raise ValueError("values must not contain ' or line breaks")
    return "'" + v + "'"


def update_env(changes: dict, path: str = ENV_FILE) -> None:
    """changes: key -> new value ('' = empty). Existing lines are updated in place, new keys appended."""
    try:
        with open(path) as f:
            lines = f.read().splitlines()
        mode = os.stat(path).st_mode & 0o777
    except FileNotFoundError:
        lines, mode = [], 0o600
    pending = dict(changes)
    out = []
    for line in lines:
        m = LINE.match(line)
        if m and m.group(1) in pending:
            key = m.group(1)
            out.append(f"{key}={_quote(pending.pop(key))}")
        else:
            out.append(line)
    if pending:
        out.append("")
        out.append("# added by tentacontrol")
        for key, value in pending.items():
            out.append(f"{key}={_quote(value)}")
    d = os.path.dirname(path)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".env.")
    with os.fdopen(fd, "w") as f:
        f.write("\n".join(out) + "\n")
    os.chmod(tmp, mode or 0o600)
    os.replace(tmp, path)


def env_mtime(path: str = ENV_FILE) -> float:
    try:
        return os.stat(path).st_mtime
    except FileNotFoundError:
        return 0.0
