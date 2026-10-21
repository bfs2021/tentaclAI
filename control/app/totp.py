"""Time-based one-time passwords (RFC 6238, as authenticator apps use them) – standard library only.

HMAC-SHA1, 6 digits, 30 s steps; a code is accepted one step early or late (clock drift) and every step only once.
"""
import base64
import hashlib
import hmac
import secrets
import struct
import time
import urllib.parse

STEP, DIGITS, WINDOW = 30, 6, 1


def new_secret():
    """160 random bits, base32 – what authenticator apps expect"""
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def _key(secret):
    s = secret.upper().replace(" ", "")
    return base64.b32decode(s + "=" * (-len(s) % 8))


def code(secret, counter, digits=DIGITS):
    mac = hmac.new(_key(secret), struct.pack(">Q", counter), hashlib.sha1).digest()
    off = mac[-1] & 0x0F
    n = struct.unpack(">I", mac[off:off + 4])[0] & 0x7FFFFFFF
    return str(n % 10 ** digits).zfill(digits)


def verify(secret, given, last_counter=-1, now=None):
    """the counter of the matching step, or None – steps up to last_counter were used already (replay)"""
    given = "".join(ch for ch in str(given) if ch.isdigit())
    if len(given) != DIGITS:
        return None
    now_counter = int((now or time.time()) // STEP)
    for c in range(now_counter - WINDOW, now_counter + WINDOW + 1):
        if c > last_counter and hmac.compare_digest(code(secret, c), given):
            return c
    return None


def uri(secret, account, issuer="tentaclAI"):
    """otpauth:// link – shown as a QR code to scan with the authenticator app"""
    label = urllib.parse.quote(f"{issuer}:{account}")
    return f"otpauth://totp/{label}?" + urllib.parse.urlencode({"secret": secret, "issuer": issuer, "digits": DIGITS, "period": STEP})


def new_recovery_codes(n=10):
    """one-time codes for a lost phone – xxxx-xxxx, no ambiguous characters"""
    alphabet = "abcdefghjkmnpqrstuvwxyz23456789"
    return ["-".join("".join(secrets.choice(alphabet) for _ in range(4)) for _ in range(2)) for _ in range(n)]
