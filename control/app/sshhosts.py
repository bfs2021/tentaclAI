"""SSH hosts in ssh/config.d/*.conf for tentacontrol – thin wrappers around tclib (shared with the sandbox tools)."""
import os

import tclib

from .config import SSH_DIR

CONF_D = os.path.join(SSH_DIR, "config.d")
SUDO_RE = tclib.SUDO_RE
validate = tclib.ssh_validate
known_name = tclib.ssh_known_name


def hosts():
    return tclib.ssh_hosts(CONF_D)


def get(alias):
    return tclib.ssh_host(CONF_D, alias)


def sudo_of(alias):
    return tclib.ssh_sudo(CONF_D, alias)


def add(fields):
    tclib.ssh_add(CONF_D, fields)


def update(alias, fields):
    tclib.ssh_update(CONF_D, alias, fields)


def remove(alias):
    tclib.ssh_remove(CONF_D, alias)


def set_sudo(alias, cmd):
    tclib.ssh_set_sudo(CONF_D, alias, cmd)


def public_key():
    try:
        with open(os.path.join(SSH_DIR, "keys", "tentaclai_ed25519.pub")) as f:
            return f.read().strip()
    except FileNotFoundError:
        return None
