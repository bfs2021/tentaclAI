#!/usr/bin/env python3
"""tentaclAI license keys – for the issuer, not for installations.

  license-key.py init <dir>                       new signing key pair: <dir>/license-private.pem (0600, keep it
                                                  offline and backed up) – prints the public key for
                                                  control/app/license.py (PUBLIC_KEY)
  license-key.py issue <private.pem> --licensee "Example GmbH" [--email a@b] [--id L-2026-0001]
                 [--features reports,siem] [--seats 10] [--edition enterprise]
                 (--expires 2027-12-31 | --days 365 | --perpetual)
                                                  prints a key: TCL1.<payload>.<signature>
  license-key.py show <key>                       the payload of a key (not verified)

Needs the Python package cryptography – e.g. run it in the tentacontrol image:
  docker run --rm -v /path/to/keys:/keys -v "$PWD/scripts:/s:ro" local/tentacontrol:latest \\
      python3 /s/license-key.py issue /keys/license-private.pem --licensee "Example GmbH" --days 365
"""
import argparse
import base64
import json
import os
import sys
from datetime import date, timedelta

PREFIX = "TCL1"


def b64e(b):
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def b64d(s):
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def new_pair():
    """(private PEM bytes, public key as base64url of the raw 32 bytes)"""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    priv = Ed25519PrivateKey.generate()
    pem = priv.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    pub = priv.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return pem, b64e(pub)


def issue(private_pem, payload):
    """a signed key for this payload (dict)"""
    from cryptography.hazmat.primitives import serialization
    priv = serialization.load_pem_private_key(private_pem, password=None)
    raw = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode()
    return f"{PREFIX}.{b64e(raw)}.{b64e(priv.sign(raw))}"


def payload_of(args):
    if args.perpetual:
        expires = None
    elif args.expires:
        expires = date.fromisoformat(args.expires).isoformat()
    else:
        expires = (date.today() + timedelta(days=args.days)).isoformat()
    today = date.today()
    return {"id": args.id or f"L-{today:%Y%m%d}-{os.urandom(3).hex()}", "licensee": args.licensee, "email": args.email or "",
            "edition": args.edition, "features": [f.strip() for f in args.features.split(",") if f.strip()],
            "seats": args.seats, "issued": today.isoformat(), "expires": expires}


def main(argv):
    ap = argparse.ArgumentParser(description="tentaclAI license keys")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("init")
    p.add_argument("dir")
    p = sub.add_parser("issue")
    p.add_argument("private_pem")
    p.add_argument("--licensee", required=True)
    p.add_argument("--email", default="")
    p.add_argument("--id", default="")
    p.add_argument("--edition", default="enterprise")
    p.add_argument("--features", default="reports,siem")
    p.add_argument("--seats", type=int, default=0, help="0 = not limited")
    when = p.add_mutually_exclusive_group(required=True)
    when.add_argument("--expires")
    when.add_argument("--days", type=int)
    when.add_argument("--perpetual", action="store_true")
    p = sub.add_parser("show")
    p.add_argument("key")
    args = ap.parse_args(argv)

    if args.cmd == "init":
        path = os.path.join(args.dir, "license-private.pem")
        if os.path.exists(path):
            sys.exit(f"{path} exists – never overwrite a signing key (every key issued with it would become worthless)")
        pem, pub = new_pair()
        os.makedirs(args.dir, mode=0o700, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(pem)
        print(f"private key: {path} – keep it offline and backed up, never commit it")
        print(f"public key (control/app/license.py → PUBLIC_KEY): {pub}")
    elif args.cmd == "issue":
        with open(args.private_pem, "rb") as f:
            pem = f.read()
        payload = payload_of(args)
        print(json.dumps(payload, indent=1, ensure_ascii=False), file=sys.stderr)
        print(issue(pem, payload))
    elif args.cmd == "show":
        parts = "".join(args.key.split()).split(".")
        if len(parts) != 3 or parts[0] != PREFIX:
            sys.exit("not a tentaclAI license key")
        print(json.dumps(json.loads(b64d(parts[1])), indent=1, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
