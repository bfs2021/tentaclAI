# Built-in modules

Extensions shipped with tentaclAI (copied to `/app/extensions/` in the tentacontrol image). Each is a package with an
`extension.json` manifest, off until the admin enables it on the Modules page. They use only the public Extension API
(`register(api)` and `app.extapi`) – see [docs/extensions.md](../../docs/extensions.md).

| Module | What it does |
|---|---|
| [`tentaclai_health`](tentaclai_health) | Service-Health: `svc test` of every service on a schedule, availability over 24 h / 7 days, notifications on outages |
