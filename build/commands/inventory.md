---
description: System inventory – scan docker hosts, stacks, services and SSH hosts, clarify with the user, record the result in local/CLAUDE.md
---

Run a **system inventory** of this tentaclai installation. Goal: afterwards `$TC_DIR/local/CLAUDE.md` contains the site knowledge you need for future tasks. Talk to the user in the language they write in.

Keep the context small – every turn re-reads all of it: few tool calls (bundle independent read-only commands), few question rounds.

## Steps

1. **Read and scan** in one go: current `$TC_DIR/local/CLAUDE.md`, the host documentation `/etc/tentaclai/host-docs.md` if present, and `mkdir -p /workspace/.inventory && tc-scan > /workspace/.inventory/scan.md` (read-only, no secrets). Then read the scan file **once, in full** – not in slices. Don't ask again for things already documented.
2. **Look deeper only where the scan leaves a real gap**: compose files and `README`/`agents.md` of individual stacks (`svc files` / `svc ssh`, `grep`/`head` instead of whole files) – **never** print or copy `.env` contents or other secrets.
3. **Summarize** briefly (docker hosts, number of stacks/containers, reverse proxy, services, SSH hosts, notable things like stopped stacks or unreachable services) **and ask the first round in the same turn**.
4. **Ask** with AskUserQuestion – up to 4 questions per round, each with suggestions derived from the scan, ideally 2–3 rounds in total – only about what the scan cannot tell:
   - purpose of each host and who uses it; language/tone
   - per stack (grouped): what for, how critical, may Claude restart/update it? Stacks that must **never** be touched or only be updated by their own script?
   - container manager (Arcane, Portainer, Dockge …) and whether changes must go through it
   - reverse proxy and DNS conventions (domain, how new services are published, certificates)
   - backups: what, where, when; what must be backed up before changes
   - network/remote access (VPN, tunnels), SSH hosts: role, what is off limits there
   - conventions: secrets (.env?), naming, where new stacks go, update policy (latest vs. pinned tags)
   - wishes: when must Claude ask first, what may it do on its own?
5. **Draft** a new/updated `local/CLAUDE.md` – concise facts and rules for Claude (it is loaded into every request, so no prose and nothing the host documentation already says), sections: *Overview*, *Hosts*, *Stacks* (table: stack · purpose · criticality · rules), *Services*, *Network & access*, *Backups*, *Rules & conventions*. Keep and merge existing content, nothing invented, no secrets, no IP lists without purpose. Note the date of the inventory at the top. Write it in the user's language.
6. **Approval**: show the draft (or a diff) and ask whether to write it. Only after "yes": save it as `/workspace/.inventory/CLAUDE.md` and run `svc config put local /workspace/.inventory/CLAUDE.md` – the user approves it in tentacontrol → Approvals, svc keeps a backup of the old file (don't make your own).
7. **Finish**: say that the new rules are loaded automatically from the next Claude session and list open points (missing tokens, unreachable hosts, services that have a template in `services/_templates/` but aren't set up yet – suggest `/onboard <service>` for each new service).

Change **nothing** on stacks, services or hosts during the inventory – only read, and write `local/CLAUDE.md` at the end.
