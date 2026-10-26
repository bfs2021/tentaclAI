"""svc, the sandbox's service gateway, in parts – build/svc is only the entry point (runs as root via sudo).

core        paths, privileges, loading services/systems/.env, running client tools – imports no other part
gate        access to a tentacle: lookup with the job limit (get, get_part), the audit log, approvals, guarded()
safeguards  what reaches Claude's context: secrets blanked (redact), untrusted text framed, the injection scan
*_part      one module per command family: api, git, docker (+ compose), db, mail, web, files, ssh, jobs, config,
            mcp (the proxy in front of every MCP server: tools/call through gate.admit/finish)
catalog     svc list / systems / info / test / sync – what Claude gets to know about the services
cli         the command line (main)

Modules use each other only as modules (`core.ENV`, `gate.audit(...)`) – so a value set or patched in one place is
the same everywhere. At import time a module may only use `core`.
"""
