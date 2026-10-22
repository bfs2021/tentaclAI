"""Extensions: optional packages that hook into tentacontrol at start – e.g. tentaclAI Enterprise (tentaclai_enterprise).

An extension is a Python package on the path with register(api). TC_EXTENSIONS names them (comma-separated, default
"tentaclai_enterprise"); one that isn't installed is skipped. Its routes come before tentacontrol's own, so it can
replace a page (e.g. the Community placeholder of Settings → License); its templates come first too.
Without an extension tentacontrol is tentaclAI Community: edition() "community", features() empty.
"""
import importlib
import os
import traceback

from jinja2 import ChoiceLoader, FileSystemLoader

LOADED = []      # names of the extensions that registered
_features = []   # callables → iterable of unlocked feature names (e.g. "reports")
_modules = []    # Enterprise modules: {key, title, text, nav, planned} – listed on Modules, nav shown once unlocked
_edition = ["community"]


class Api:
    """what an extension may use to hook in"""

    def __init__(self, app, templates):
        self.app, self.templates = app, templates

    def add_router(self, router):
        self.app.include_router(router)

    def add_templates(self, directory):
        self.templates.env.loader = ChoiceLoader([FileSystemLoader(directory), self.templates.env.loader])

    def add_features(self, fn):
        _features.append(fn)

    def set_edition(self, name):
        _edition[0] = name

    def add_module(self, key, title, text, nav=None, planned=False):
        """a module for the Modules page; nav = {"href", "label", "icon"} – its sidebar entry, shown while the license
        unlocks the feature `key` (and the module isn't planned only)"""
        _modules.append({"key": key, "title": title, "text": text, "nav": nav, "planned": planned})


def load(app, templates):
    """import and register every installed extension – called before tentacontrol includes its own routers"""
    for name in [n.strip() for n in os.environ.get("TC_EXTENSIONS", "tentaclai_enterprise").split(",") if n.strip()]:
        try:
            mod = importlib.import_module(name)
        except ImportError as e:
            if e.name != name:  # installed, but broken inside
                print(f"tentacontrol: extension {name} failed to import: {e}", flush=True)
            continue
        try:
            mod.register(Api(app, templates))
            LOADED.append(name)
            print(f"tentacontrol: extension {name} loaded", flush=True)
        except Exception:  # noqa: BLE001 – a broken extension must not take tentacontrol down
            print(f"tentacontrol: extension {name} failed to register:\n{traceback.format_exc()}", flush=True)


def features():
    """feature names the extensions unlock right now (e.g. by a valid license)"""
    out = set()
    for fn in _features:
        try:
            out |= set(fn() or ())
        except Exception:  # noqa: BLE001
            pass
    return out


def edition():
    return _edition[0]


def modules():
    return list(_modules)


def nav_entries(unlocked):
    """sidebar entries of the modules the license unlocks: [{href, label, icon, nav_id}]"""
    out = []
    for m in _modules:
        n = m.get("nav")
        if n and not m["planned"] and m["key"] in unlocked:
            out.append({"href": n["href"], "label": n["label"], "icon": n.get("icon") or "grid",
                        "nav_id": n.get("nav_id") or n["href"].strip("/").split("/")[0]})
    return out
