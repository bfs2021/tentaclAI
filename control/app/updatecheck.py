"""Is a newer tentaclAI release out? Asks the GitHub API (no token) every few hours, caches the answer in DATA_DIR.

Settings in .env: UPDATE_CHECK=false switches it off, UPDATE_REPO=<owner>/<repo> for forks.
"""
import json
import os
import re
import time
import urllib.request

from . import jsonstore
from .config import DATA_DIR
from .envfile import read_env

CACHE = os.path.join(DATA_DIR, "update-check.json")
INTERVAL = 6 * 3600
DEFAULT_REPO = "mwaldegg/tentaclai"


def settings():
    env = read_env()
    enabled = (env.get("UPDATE_CHECK") or "true").strip().lower() not in ("false", "0", "no", "off")
    repo = (env.get("UPDATE_REPO") or DEFAULT_REPO).strip()
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
        repo = DEFAULT_REPO
    return enabled, repo


def parse(version):
    """'v0.2.0' / '0.2.0' -> (0, 2, 0); None if it is no plain release version"""
    m = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)", (version or "").strip())
    return tuple(int(x) for x in m.groups()) if m else None


def upgrade_section(body):
    """the '### Upgrade …' part of the release notes (our changelog format)"""
    m = re.search(r"^###\s*Upgrade[^\n]*\n(.*?)(?=^#{2,3}\s|\Z)", body or "", re.S | re.M)
    return m.group(1).strip() if m else ""


def _read_cache():
    try:
        with open(CACHE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def refresh(force=False):
    """fetch the latest release if the cache is older than INTERVAL (or forced); returns the cached data"""
    enabled, repo = settings()
    data = _read_cache()
    if not enabled or (not force and data.get("repo") == repo and time.time() - data.get("checked", 0) < INTERVAL):
        return data
    req = urllib.request.Request(f"https://api.github.com/repos/{repo}/releases/latest",
                                 headers={"Accept": "application/vnd.github+json", "User-Agent": "tentacontrol"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            rel = json.load(r)
        data = {"repo": repo, "checked": time.time(), "tag": rel.get("tag_name", ""), "url": rel.get("html_url", ""),
                "published": rel.get("published_at", ""), "upgrade": upgrade_section(rel.get("body", "")), "error": ""}
    except Exception as e:  # noqa: BLE001 – offline, rate limit, no release yet …
        data = dict(data, repo=repo, checked=time.time(), error=str(e)[:200])
    jsonstore.save(CACHE, data, indent=None)
    return data


def status(installed):
    """what the UI shows – from the cache only, never blocks"""
    enabled, repo = settings()
    data = _read_cache() if enabled else {}
    latest, current = parse(data.get("tag")), parse(installed)
    return {"enabled": enabled, "repo": repo, "latest": data.get("tag", ""), "url": data.get("url", ""),
            "upgrade": data.get("upgrade", ""), "checked": data.get("checked", 0), "error": data.get("error", ""),
            "available": bool(enabled and latest and current and latest > current)}
