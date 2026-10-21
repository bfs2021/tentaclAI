#!/usr/bin/env python3
"""Set/reset the tentacontrol admin password, or switch two-factor sign-in off – on the host:
  docker exec -it tentacontrol tentacontrol-passwd                (./tentaclai.sh passwd)
  docker exec -it tentacontrol tentacontrol-passwd --reset-2fa    (./tentaclai.sh reset-2fa)
"""
import getpass
import sys

sys.path.insert(0, "/app")
from app.auth import disable_twofactor, revoke_trust, set_password, twofactor_enabled  # noqa: E402

if sys.argv[1:] == ["--reset-2fa"]:
    if not twofactor_enabled():
        sys.exit("Two-factor sign-in is not on.")
    disable_twofactor()
    print("Two-factor sign-in switched off (secret, recovery codes and trusted browsers removed).")
    print("Sign in with the password and set it up again under Settings → Security.")
    sys.exit(0)

pw = getpass.getpass("New tentacontrol password: ")
if len(pw) < 10:
    sys.exit("At least 10 characters.")
if getpass.getpass("Repeat: ") != pw:
    sys.exit("Passwords do not match.")
set_password(pw)
revoke_trust()  # a browser trusted for two-factor sign-in must not outlive a password reset
print("Password set; browsers trusted for two-factor sign-in are revoked. Existing sessions stay valid until they expire.")
