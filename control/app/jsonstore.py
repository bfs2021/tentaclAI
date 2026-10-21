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


def save(path, data, indent=1):
    """write to a temporary file and rename it – readers never see half a file"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path + ".tmp", "w") as f:
        json.dump(data, f, indent=indent)
    os.replace(path + ".tmp", path)
