"""Enterprise license: an offline key, signed with tentaclAI's private Ed25519 key – tentacontrol only holds the public one.

Key format:  TCL1.<payload>.<signature>   (both base64url, no padding; the signature covers the payload bytes)
Payload (JSON): id, licensee, email, edition, features, seats, issued, expires (YYYY-MM-DD, or null = no end).
No license server, no call home: a key is checked here only. scripts/license-key.py creates the key pair and keys.
"""
import base64
import json
import os
import time
from datetime import date

from . import jsonstore
from .config import DATA_DIR

LICENSE_FILE = os.path.join(DATA_DIR, "license.json")
PREFIX = "TCL1"
# the public half of the signing key – the private half never leaves the issuer (scripts/license-key.py init)
PUBLIC_KEY = "WxhdXTzPskN_rfrGIz-hHuJzDs6jGC6vEfjuTyt8mjc"
FEATURES = {"reports": "Compliance reports", "siem": "SIEM export of the audit log"}


def _b64d(s):
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def parse(key, public_key=None):
    """the payload of a well-formed key with a valid signature – ValueError with a reason otherwise (expiry is not checked here)"""
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    key = "".join(str(key or "").split())  # pasted keys may wrap
    parts = key.split(".")
    if len(parts) != 3 or parts[0] != PREFIX:
        raise ValueError("not a tentaclAI license key (it starts with TCL1.)")
    pub = public_key if public_key is not None else PUBLIC_KEY
    if not pub:
        raise ValueError("this build has no license public key")
    try:
        payload, sig = _b64d(parts[1]), _b64d(parts[2])
        Ed25519PublicKey.from_public_bytes(_b64d(pub)).verify(sig, payload)
    except (ValueError, InvalidSignature):
        raise ValueError("the key is damaged or not issued for tentaclAI") from None
    try:
        lic = json.loads(payload)
    except ValueError:
        raise ValueError("the key is damaged or not issued for tentaclAI") from None
    if not isinstance(lic, dict) or not lic.get("id") or not lic.get("licensee"):
        raise ValueError("the key is damaged or not issued for tentaclAI")
    return lic


def check(key, public_key=None, today=None):
    """{state: valid|expired|invalid, license, days_left, error} for a key"""
    try:
        lic = parse(key, public_key)
    except ValueError as e:
        return {"state": "invalid", "license": None, "error": str(e)}
    out = {"state": "valid", "license": lic, "days_left": None, "error": ""}
    if lic.get("expires"):
        try:
            left = (date.fromisoformat(lic["expires"]) - (today or date.today())).days
        except ValueError:
            return {"state": "invalid", "license": None, "error": "the key has no valid end date"}
        out["days_left"] = left
        if left < 0:
            out["state"] = "expired"
    return out


def status():
    """the stored license: state none|valid|expired|invalid"""
    key = (jsonstore.load(LICENSE_FILE) or {}).get("key", "")
    if not key:
        return {"state": "none", "license": None, "days_left": None, "error": ""}
    st = check(key)
    st["installed"] = (jsonstore.load(LICENSE_FILE) or {}).get("installed", 0)
    return st


def install(key):
    """store a key after checking it – ValueError for an invalid or expired one"""
    st = check(key)
    if st["state"] == "invalid":
        raise ValueError(st["error"])
    if st["state"] == "expired":
        raise ValueError(f"this license ended on {st['license']['expires']}")
    jsonstore.save(LICENSE_FILE, {"key": "".join(key.split()), "installed": time.time()}, mode=0o600)
    return st


def remove():
    try:
        os.remove(LICENSE_FILE)
    except FileNotFoundError:
        pass


def has(feature):
    """True while a valid license includes this feature"""
    st = status()
    return st["state"] == "valid" and feature in (st["license"].get("features") or [])
