"""Read/update a docker-compose .env file while keeping comments, order and file mode."""
import os
import re
import tempfile

import tclib

from .config import ENV_FILE

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
    """changes: key -> new value ('' = empty, None = remove the line). Existing lines are updated in place, new keys appended."""
    from .central import check_write  # settings the central server enforces are refused
    for k in changes:
        check_write("settings", k)
    try:
        with open(path) as f:
            lines = f.read().splitlines()
        mode = os.stat(path).st_mode & 0o777
    except FileNotFoundError:
        lines, mode = [], 0o600
    pending = dict(changes)
    out = []
    for line in lines:
        m = tclib.ENV_LINE.match(line)
        if m and m.group(1) in pending:
            key = m.group(1)
            value = pending.pop(key)
            if value is not None:
                out.append(f"{key}={_quote(value)}")
        else:
            out.append(line)
    pending = {k: v for k, v in pending.items() if v is not None}
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
