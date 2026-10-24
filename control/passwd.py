#!/usr/bin/env python3
"""Reset the tentacontrol admin password or username, or switch two-factor sign-in off – on the host:
  docker exec -it tentacontrol tentacontrol-passwd                     (./tentaclai.sh passwd)
  docker exec -it tentacontrol tentacontrol-passwd --username <name>   (./tentaclai.sh passwd --username <name>)
  docker exec -it tentacontrol tentacontrol-passwd --reset-2fa         (./tentaclai.sh reset-2fa)
  docker exec -it tentacontrol tentacontrol-passwd --open              (./tentaclai.sh passwd --open)
While tentaclAI Enterprise allows only sign-ins through the central, a password reset (or --open, without one) also
opens the local sign-in for an hour – the emergency access.
"""
import getpass
import sys

sys.path.insert(0, "/app")
from app import auth, central  # noqa: E402


def emergency():
    """the local sign-in for an hour, when the central's profile allows only its own sign-ins"""
    if central.sign_in_policy() == "enterprise":
        import time
        until = central.open_breakglass("host")
        print(f"Local sign-in opened until {time.strftime('%H:%M', time.localtime(until))} (only tentaclAI Enterprise sign-in is "
              "allowed here otherwise).")
        return True
    return False


if sys.argv[1:] == ["--reset-2fa"]:
    if not auth.twofactor_enabled():
        sys.exit("Two-factor sign-in is not on.")
    auth.disable_twofactor()
    print("Two-factor sign-in switched off (secret, recovery codes and trusted browsers removed).")
    print("Sign in with the password and set it up again under Settings → Account.")
    sys.exit(0)

auth.migrate()  # an installation from before accounts that wasn't started since the update
admin = auth.admin()
if not admin:
    sys.exit("No account yet – create it with the setup link: ./tentaclai.sh setup-url")

if sys.argv[1:2] == ["--username"] and len(sys.argv) == 3:
    error = auth.profile_error(sys.argv[2], admin.get("email") or "x@example.org", admin.get("name"), uid=admin["id"])
    if error:
        sys.exit(error)
    auth.update_profile(admin["id"], sys.argv[2], admin.get("email", ""), admin.get("name", ""))
    print(f"Username set: {sys.argv[2].strip().lower()}")
    sys.exit(0)
if sys.argv[1:] == ["--open"]:
    if not emergency():
        print("Local sign-in is open anyway – nothing to do.")
    sys.exit(0)
if sys.argv[1:]:
    sys.exit(__doc__)

print(f"Username: {admin.get('username') or '(not set yet – chosen at the next sign-in)'}")
pw = getpass.getpass("New tentacontrol password: ")
error = auth.password_error(pw, getpass.getpass("Repeat: "))
if error:
    sys.exit(error)
auth.set_password(pw, admin["id"])
auth.revoke_trust()  # a browser trusted for two-factor sign-in must not outlive a password reset
print("Password set; browsers trusted for two-factor sign-in are revoked. Existing sessions stay valid until they expire.")
emergency()
