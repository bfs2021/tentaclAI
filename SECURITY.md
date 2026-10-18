# Security policy

tentaclAI gives an AI agent access to real infrastructure, so security reports are very welcome.

## Reporting a vulnerability

Please **do not open a public issue**. Use GitHub's private reporting instead:
**Security → Report a vulnerability** on this repository. You will get an answer as soon as possible.

Helpful details: affected component (`svc`, tentacontrol, scripts, image), steps to reproduce, impact.

## Supported versions

Only the latest release / the `main` branch receives fixes.

## Scope and known limitations

The [Security section of the README](README.md#security) lists what tentaclAI protects and what it does not (yet) do –
for example that an `rw` Docker host is effectively root on that machine. Reports that bypass the privilege separation in
the sandbox (Claude as user `claude` reading secrets, escaping `svc`/`sudo`, getting root), the access modes
(`ro`/`rw`/`confirm`), credential handling or tentacontrol authentication are in scope; the documented limitations
themselves are known and on the roadmap.
