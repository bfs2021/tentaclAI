# Built-in modules

Extensions shipped with tentaclAI (copied to `/app/extensions/` in the tentacontrol image). Each is a package with an
`extension.json` manifest, off until the admin enables it on the Modules page. They use only the public Extension API
(`register(api)` and `app.extapi`) – see [docs/extensions.md](../../docs/extensions.md).
