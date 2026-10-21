"""tentacontrol's small JSON state files (data/control/*.json): read with a default, write atomically."""
import json
import os


def load(path, default=None):
    """the file's content, or default when it is missing or broken"""
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {} if default is None else default


def save(path, data, indent=1, mode=None):
    """write to a temporary file and rename it – readers never see half a file; mode e.g. 0o600 for secrets"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd = os.open(path + ".tmp", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode if mode is not None else 0o644)
    with os.fdopen(fd, "w") as f:
        json.dump(data, f, indent=indent)
    if mode is not None:
        os.chmod(path + ".tmp", mode)
    os.replace(path + ".tmp", path)
