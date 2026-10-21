"""Single admin password (bcrypt hash in /data/admin.hash), optional second factor (TOTP), signed session cookie, login
throttling."""
import os
import secrets
import time

import bcrypt
from fastapi import Request
from itsdangerous import BadSignature, URLSafeTimedSerializer

from . import jsonstore, totp
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


# ---------------------------------------------------------------- second factor (TOTP), optional
# data/control/twofactor.json (0600): secret, last used step, recovery code hashes, trusted browsers.
TWOFACTOR_FILE = os.path.join(DATA_DIR, "twofactor.json")
PENDING_SECONDS = 300           # password → code: 5 minutes
TRUST_DAYS = 30                 # "trust this browser"
TRUST_COOKIE = "tc_trust"
FAIL_DELAY = 1.0                # seconds after a wrong password or code (slows guessing down)
RECOVERY_ROUNDS = 10            # bcrypt cost of the recovery code hashes


def _2fa():
    return jsonstore.load(TWOFACTOR_FILE, {"enabled": False})


def _2fa_save(s):
    jsonstore.save(TWOFACTOR_FILE, s, mode=0o600)


def twofactor_enabled() -> bool:
    return bool(_2fa().get("enabled"))


def twofactor_status():
    s = _2fa()
    return {"enabled": bool(s.get("enabled")), "recovery_left": len(s.get("recovery", [])),
            "trusted": sorted(({"id": k, **v} for k, v in s.get("trusted", {}).items()), key=lambda t: -t.get("last_used", 0))}


def pending_secret():
    """the secret of a running setup (shown as QR code until the first code confirms it)"""
    return _2fa().get("pending_secret", "")


def start_setup():
    """a new secret waiting for its first code (the running setup is replaced)"""
    s = _2fa()
    s["pending_secret"] = totp.new_secret()
    _2fa_save(s)
    return s["pending_secret"]


def _hash(code):
    return bcrypt.hashpw(code.strip().lower().encode(), bcrypt.gensalt(rounds=RECOVERY_ROUNDS)).decode()


def confirm_setup(code):
    """turns 2FA on when the code matches the pending secret – returns the recovery codes (shown once) or None"""
    s = _2fa()
    secret = s.get("pending_secret")
    counter = totp.verify(secret, code) if secret else None
    if counter is None:
        return None
    codes = totp.new_recovery_codes()
    s.update(enabled=True, secret=secret, last_counter=counter, recovery=[_hash(c) for c in codes], trusted={})
    s.pop("pending_secret", None)
    _2fa_save(s)
    return codes


def check_second_factor(code):
    """'totp', 'recovery' (that code is used up now) or None"""
    s = _2fa()
    if not s.get("enabled"):
        return None
    counter = totp.verify(s["secret"], code, s.get("last_counter", -1))
    if counter is not None:
        s["last_counter"] = counter
        _2fa_save(s)
        return "totp"
    given = code.strip().lower()
    for h in s.get("recovery", []):
        if bcrypt.checkpw(given.encode(), h.encode()):
            s["recovery"].remove(h)
            _2fa_save(s)
            return "recovery"
    return None


def new_recovery_codes():
    s = _2fa()
    codes = totp.new_recovery_codes()
    s["recovery"] = [_hash(c) for c in codes]
    _2fa_save(s)
    return codes


def disable_twofactor():
    """off – secret, recovery codes and trusted browsers are gone (also: ./tentaclai.sh reset-2fa)"""
    _2fa_save({"enabled": False})


def _signer():
    return URLSafeTimedSerializer(session_secret(), salt="tc-trust")


def trust_browser(label):
    """a signed cookie value; its id must also be in twofactor.json, so it can be revoked"""
    s = _2fa()
    tid = secrets.token_urlsafe(12)
    s.setdefault("trusted", {})[tid] = {"created": time.time(), "last_used": time.time(), "label": label[:80]}
    _2fa_save(s)
    return _signer().dumps(tid)


def is_trusted(token) -> bool:
    if not token:
        return False
    try:
        tid = _signer().loads(token, max_age=TRUST_DAYS * 86400)
    except BadSignature:  # includes expired
        return False
    s = _2fa()
    if tid not in s.get("trusted", {}):
        return False
    s["trusted"][tid]["last_used"] = time.time()
    _2fa_save(s)
    return True


def revoke_trust(tid=None):
    """one trusted browser, or all (tid None)"""
    s = _2fa()
    if tid is None:
        s["trusted"] = {}
    else:
        s.get("trusted", {}).pop(tid, None)
    _2fa_save(s)
