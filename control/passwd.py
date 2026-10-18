#!/usr/bin/env python3
"""Set/reset the tentacontrol admin password: docker exec -it tentacontrol tentacontrol-passwd"""
import getpass
import sys

sys.path.insert(0, "/app")
from app.auth import set_password  # noqa: E402

pw = getpass.getpass("New tentacontrol password: ")
if len(pw) < 10:
    sys.exit("At least 10 characters.")
if getpass.getpass("Repeat: ") != pw:
    sys.exit("Passwords do not match.")
set_password(pw)
print("Password set. Existing sessions stay valid until they expire.")
