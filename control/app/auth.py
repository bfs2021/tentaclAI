"""Single admin password (bcrypt hash in /data/admin.hash), signed session cookie, login throttling."""
import os
import secrets
import time

import bcrypt
from fastapi import Request

from .config import DATA_DIR

HASH_FILE = os.path.join(DATA_DIR, "admin.hash")
SECRET_FILE = os.path.join(DATA_DIR, "session.secret")

# one-time setup token, only valid while no password exists (printed to the container log)
SETUP_TOKEN = secrets.token_urlsafe(24)

_failures: dict = {}


def session_secret() -> str:
    os.makedirs(DATA_DIR, exist_ok=True)
    if not os.path.exists(SECRET_FILE):
        with open(os.open(SECRET_FILE, os.O_WRONLY | os.O_CREAT, 0o600), "w") as f:
            f.write(secrets.token_urlsafe(48))
    with open(SECRET_FILE) as f:
        return f.read().strip()


def has_password() -> bool:
    return os.path.exists(HASH_FILE) and os.path.getsize(HASH_FILE) > 0


def set_password(pw: str) -> None:
    h = bcrypt.hashpw(pw.encode(), bcrypt.gensalt(rounds=12))
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(os.open(HASH_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "wb") as f:
        f.write(h)


def check_password(pw: str) -> bool:
    try:
        with open(HASH_FILE, "rb") as f:
            return bcrypt.checkpw(pw.encode(), f.read().strip())
    except (FileNotFoundError, ValueError):
        return False


def throttled(ip: str) -> int:
    """seconds the client still has to wait (exponential backoff after 3 failures)"""
    n, last = _failures.get(ip, (0, 0.0))
    if n < 3:
        return 0
    wait = min(2 ** (n - 3) * 5, 900)
    return max(0, int(last + wait - time.time()))


def register_failure(ip: str) -> None:
    n, _ = _failures.get(ip, (0, 0.0))
    _failures[ip] = (n + 1, time.time())


def clear_failures(ip: str) -> None:
    _failures.pop(ip, None)


def logged_in(request: Request) -> bool:
    return bool(request.session.get("auth")) and has_password()


def same_origin(request: Request) -> bool:
    """CSRF guard for state-changing requests and websockets."""
    origin = request.headers.get("origin")
    if not origin:
        return True  # same-origin form posts from old browsers / curl; cookie is SameSite=Strict anyway
    host = request.headers.get("x-forwarded-host") or request.headers.get("host", "")
    return origin.split("://", 1)[-1] == host
