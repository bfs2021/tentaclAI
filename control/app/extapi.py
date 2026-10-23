"""What an extension's code uses at runtime (Extension API v1, docs/extensions.md) – the stable face of tentacontrol.

Import it as `from app import extapi`. Everything else in app.* may change between releases; what is here only
changes with a new API_VERSION (a removal is announced in the log one major version ahead).
"""
import functools
import os

from fastapi import Request

from . import auditlog, dockerops, extensions, instance, jsonstore
from . import notify as _notify
from .config import DATA_DIR
from .web import TC_VERSION, respond, templates, toast  # noqa: F401 – part of the API
from .web import page as _page

API_VERSION = extensions.API_VERSION


def page(request, template, nav=None, **ctx):
    """a full page in tentacontrol's layout (sidebar, banners). nav: the sidebar entry to highlight – default the
    extension's own (the first part of "<name>/file.html"); e.g. "settings" for a page behind a Settings tab"""
    return _page(request, template, nav=nav or (template.split("/")[0] if "/" in template else None), **ctx)


def features():
    """feature names the license unlocks right now – empty in tentaclAI Community"""
    return extensions.features()


def edition():
    """"community", or what the license provider set (e.g. "enterprise")"""
    return extensions.edition()


def requires(feature):
    """route decorator: the page only while the license unlocks `feature`, a locked page (403) otherwise.
    The route needs a `request: Request` parameter."""
    def deco(fn):
        @functools.wraps(fn)
        async def wrapper(*args, **kwargs):
            if feature not in extensions.features():
                request = kwargs.get("request") or next((a for a in args if isinstance(a, Request)), None)
                title = next((m["title"] for m in extensions.licensed_modules() if m["key"] == feature), feature)
                r = _page(request, "module_locked.html", nav="modules", feature=feature, module_title=title)
                r.status_code = 403
                return r
            return await fn(*args, **kwargs)
        return wrapper
    return deco


def data_dir(name):
    """the extension's own directory for state (DATA_DIR/ext/<name>, 0700) – kept in tentacontrol's data volume"""
    if not extensions.NAME_RE.match(name or ""):
        raise ValueError(f"invalid extension name {name!r}")
    path = os.path.join(DATA_DIR, "ext", name)
    os.makedirs(path, mode=0o700, exist_ok=True)
    return path


def _file(name, file):
    if not file or "/" in file or file.startswith("."):
        raise ValueError(f"invalid file name {file!r}")
    return os.path.join(data_dir(name), file)


def load_json(name, file, default=None):
    """a JSON file from data_dir(name) – default when it is missing or broken"""
    return jsonstore.load(_file(name, file), default)


def save_json(name, file, data):
    """write a JSON file to data_dir(name) atomically (0600)"""
    jsonstore.save(_file(name, file), data, mode=0o600)


def instance_id():
    """(ID, source) of this installation – e.g. ("TCI-…", "hardware"); ("", "") when there is none"""
    return instance.instance_id()


def audit(name, action, target="", decision="ok", request=None, **extra):
    """an audit log entry of the extension `name`: with request – an action of the signed-in user (type admin),
    without – a system entry. Never raises. Note: entries written here reach on_audit hooks too."""
    if request is not None:
        auditlog.user(request, "admin", target or name, action, decision, part=name[:20], **extra)
    else:
        auditlog.write("system", target or name, action, decision, actor="system", part=name[:20], **extra)


def notify(event, title, body="", key=None, link=""):
    """send a notification for an event (one of add_event's or tentacontrol's) – routed, quiet hours, deduplicated"""
    _notify.emit(event, title, body, key=key, link=link)


def control_link(path):
    """an absolute link to a page of tentacontrol (CONTROL_URL) – "" when CONTROL_URL isn't set"""
    return _notify.control_link(path)


def svc(*args):
    """run `svc <args>` in the sandbox as root (e.g. svc("test", "myservice")) → (exit code, output). Blocking – call
    it from a task or with asyncio.to_thread."""
    return dockerops.run(["svc", *[str(a) for a in args]])
