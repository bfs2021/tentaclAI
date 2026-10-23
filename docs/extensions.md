# Writing modules – Extension API v1

A **module** (technically: an extension) is a Python package that tentacontrol loads at start. It can bring pages,
sidebar entries, tabs, an icon, static files, background tasks, notification events, an audit log hook, a Help section
and a part of Export / Import. The example [`examples/extensions/tcx_hello`](../examples/extensions/tcx_hello) uses
every one of them – start from a copy of it.

## Origin and trust

Where a module comes from decides what it may do – never what the package says about itself.

| Origin | Where | Loaded | May |
|---|---|---|---|
| **Built-in** | shipped in the image (`control/extensions/` in this repository) | once enabled on the Modules page | the API below |
| **Third-party** | `<stack>/local/extensions/<name>/` | once enabled on the Modules page | the API below |
| **Enterprise** | named in `TC_EXTENSIONS` (tentaclAI Enterprise) | always | also provide the license and replace tentacontrol's own pages and templates |

A module runs **inside tentacontrol, with all of its rights**: it can read the configuration, approve requests and run
`svc` as root in the sandbox. The rules below (namespaces, no license, no replaced pages) keep modules from getting in
each other's way and from changing tentacontrol by accident – they are no sandbox. Only enable code you trust.

Until a module is enabled tentacontrol reads only its `extension.json`; the code is never imported. *Enable* takes
effect at tentacontrol's next start (the Modules page has a *Restart tentacontrol* button); *Disable* at once – its
pages answer 404, its sidebar entry, tabs and Help section disappear, its tasks and audit hook stop. Loading, enabling,
disabling and failures land in the audit log.

## Layout

```
local/extensions/my_module/
├── extension.json        manifest – read without importing anything
├── __init__.py           REQUIRES_API = 1 and register(api)
├── templates/            used as "my_module/<file>.html"
└── static/               served at /x/my_module/static/
```

`extension.json`:

```json
{
  "name": "my_module",
  "title": "My module",
  "description": "One or two sentences for the Modules page.",
  "version": "1.0.0",
  "author": "Me",
  "homepage": "https://example.org/my_module",
  "requires_api": 1
}
```

`name` must equal the folder name: lowercase letters, digits and `_`, starting with a letter, and not the name of an
installed Python module. `requires_api` must be the API version of this tentacontrol, or the module is not loaded
(the Modules page says why). A third-party module with the name of a built-in one is ignored.

## Registering – `register(api)`

```python
import os
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from app import extapi

NAME = "my_module"
REQUIRES_API = 1
router = APIRouter(prefix=f"/x/{NAME}")


@router.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return extapi.page(request, f"{NAME}/index.html", items=[])


def register(api):
    api.add_router(router)
    api.add_templates(os.path.join(os.path.dirname(__file__), "templates"))
    api.add_module("main", "My module", "What it does.", nav={"href": f"/x/{NAME}/", "label": "Mine", "icon": "grid"})
```

If `register()` raises – or breaks a rule – nothing it registered stays and the module is listed as *not loaded*.

| Call | What it does |
|---|---|
| `add_router(router)` | Your pages. Every path must start with `/x/<name>/`. They are behind tentacontrol's sign-in like every page; POSTs need the same origin and are logged in the audit log automatically. |
| `add_templates(directory)` | Your Jinja templates, used as `"<name>/file.html"`. Extend `base.html` for tentacontrol's layout; `_icons.html` (`icon`), `_tabs.html` (`settings_tabs`, `logs_tabs`, `claude_tabs`) can be imported. |
| `add_static(directory)` | Files at `/x/<name>/static/…`; in templates `ext_static("<name>", "file.css")` (with a cache-busting version). |
| `add_icon(name, svg)` | An icon for `icon(name)`: the inner SVG of a 24×24 stroke icon. Must not exist yet. |
| `add_module(key, title, text, nav=None, count_url=None, planned=False)` | An entry on the Modules page; `nav = {"href", "label", "icon"}` adds a sidebar entry (between Jobs and Modules, `href` below `/x/<name>/`). `count_url`: a page answering a short text – shown as a badge next to the entry, refreshed every 30 s. |
| `add_tab(area, href, label, icon)` | A tab in the tab bar of `settings`, `logs` or `claude`. Your page renders the bar itself, e.g. `{{ settings_tabs('/x/<name>/settings') }}`, and passes `nav="settings"` to `extapi.page` so the sidebar highlights Settings. |
| `add_help_section(id, title, template)` | A section at the end of Help (id becomes `x-<name>-<id>`); the template gets no page context. |
| `add_banner(fn)` | `fn(request)` returns `None` or `{"text", "kind": "info"/"warn"/"err", "href", "link"}` – a banner at the top of every page. Called on every page: keep it cheap. |
| `add_task(fn, every, delay=30)` | `fn` every `every` seconds (≥ 10), first after `delay`. A plain function runs in a thread, an `async` one in the event loop. Errors are logged; skipped while the module is disabled. |
| `add_event(id, label, priority="normal", description="")` | A notification event (Settings → Notifications); until routed it goes where approvals go. `priority`: low, normal, high (high passes quiet hours). |
| `on_audit(fn)` | `fn(entry)` for every new audit log entry (a dict as stored, see Help → Logs) – from the moment tentacontrol started, nothing older. Runs in a background thread: keep it quick, hand slow work to a task. Entries you write yourself come back here too. |
| `add_backup_part(key, label, export, apply)` | A part of Settings → Export / Import (`x_<name>_<key>`, always opt-in): `export()` returns JSON-able data, `apply(data)` writes it back. |

Enterprise only: `provide_license(features, edition)` (once) and `add_module(…, licensed=True)` – a sidebar entry shown
only while the license unlocks the feature `key`.

## At runtime – `app.extapi`

`app.extapi` is the only part of tentacontrol a module imports; everything else in `app.*` may change in any release.

| Name | |
|---|---|
| `page(request, template, nav=None, **ctx)` | A full page in tentacontrol's layout. `nav`: the sidebar entry to highlight – default your own. |
| `respond(request, msg, kind="ok", refresh=False, redirect=None)` | An htmx answer: a toast, or a flash message after a reload/redirect. |
| `toast(msg, kind="ok")` | The headers for a toast, for your own response. |
| `requires(feature)` | Route decorator (Enterprise modules): a locked page (403) unless the license unlocks `feature`. The route needs a `request: Request` parameter. |
| `features()`, `edition()` | What the license unlocks right now; `"community"` or e.g. `"enterprise"`. |
| `data_dir(name)` | Your directory for state (`data/control/ext/<name>`, 0700) in tentacontrol's data directory – Export / Import only carries what you add with `add_backup_part`. |
| `load_json(name, file, default=None)`, `save_json(name, file, data)` | JSON files in `data_dir(name)` – atomic, 0600. |
| `audit(name, action, target="", decision="ok", request=None, **extra)` | An audit log entry: with `request` an action of the signed-in user, without a system entry. |
| `notify(event, title, body="", key=None, link="")` | A notification for an event (yours or tentacontrol's); `key` deduplicates. |
| `control_link(path)` | An absolute link to a tentacontrol page (`CONTROL_URL`), `""` when unset. |
| `svc(*args)` | `svc <args>` in the sandbox as root → `(exit code, output)`. Blocking: call it from a task or `asyncio.to_thread`. |
| `instance_id()` | `(ID, source)` of this installation. |
| `TC_VERSION`, `API_VERSION` | tentaclAI's version, the Extension API's version. |

Secrets: never store credentials in your data files or show them on a page. Use `${VAR}` names that the admin puts
into the stack's `.env`, like services do.

## Versions

`API_VERSION` is a whole number. Additions (new calls, new optional parameters) don't change it. A change that breaks
modules raises it; what goes away is announced in tentacontrol's log one major version ahead. A module declares the
version it was written for in `extension.json` and `REQUIRES_API`.

## Testing

tentaclAI's own tests load the example as a fixture (`tests/test_extensions.py`): they copy it into a temporary
`local/extensions/`, enable it and load it into the test app – the same approach works for your module. Run them with
`scripts/test-quick.sh`.
