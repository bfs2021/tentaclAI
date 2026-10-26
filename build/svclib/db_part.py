"""svc db: databases (postgres, mysql/mariadb) through a local tunnel."""
import os
import re
import subprocess
import sys

import tclib  # shared with tentacontrol (lib/tclib.py)

from . import core, gate, safeguards


# ---------- databases (db) ----------

DB_RO_BYPASS = re.compile(r"read\s+write|default_transaction_read_only|transaction_read_only|tx_read_only|"
                          r"session\s+characteristics|\bset\s+role\b|\bset\s+session\s+authorization\b", re.I)


def pg_sslmode(db):
    """libpq sslmode; insecure_tls keeps the encryption but drops certificate verification"""
    mode = str(db.get("ssl", "prefer"))
    return "require" if db.get("insecure_tls") and mode in ("verify-ca", "verify-full") else mode


def db_engine(db):
    e = (db.get("engine") or "postgres").lower()
    return "mysql" if e in ("mysql", "mariadb") else "postgres"


def db_run(name, db, sql=None, fmt="table", interactive=False):
    """run SQL (or an interactive client) – password only in env / a 0600 tmpfs file, read enforced per session"""
    ro = gate.access_mode(db) == "read"
    if sql is not None and ro and DB_RO_BYPASS.search(sql):
        sys.exit(f"svc: {name} is read-only – statements that change the transaction mode are not allowed")
    if db_engine(db) == "postgres":
        addr, lport = core.tunnel(db.get("host", ""), db.get("port", 5432))
        env = dict(PGHOST=str(db.get("host", "")), PGHOSTADDR=addr, PGPORT=str(lport),
                   PGDATABASE=str(db.get("database", "")), PGUSER=str(db.get("user", "")), PGPASSWORD=str(db.get("password", "")),
                   PGSSLMODE=pg_sslmode(db), PGAPPNAME="tentaclai", PGCONNECT_TIMEOUT="10")
        if ro:
            env["PGOPTIONS"] = "-c default_transaction_read_only=on"
        cmd = ["psql", "-X", "-v", "ON_ERROR_STOP=1"]
        if fmt == "csv":
            cmd.append("--csv")
        if sql is not None:
            cmd += ["-c", sql]
        # psql runs as tcrun: shell escapes (\!) in the interactive shell get that user, not root
        return subprocess.call(cmd, **core.runner(env)) if interactive or sql is not None else 0
    addr, lport = core.tunnel(db.get("host", ""), db.get("port", 3306))
    cfg = core._shm_file("[client]\n" + "".join(f"{k}={v}\n" for k, v in (
        ("host", addr), ("port", lport), ("user", db.get("user", "")),
        ("password", db.get("password", "")), ("database", db.get("database", ""))) if v != ""))
    try:
        cmd = ["mariadb", f"--defaults-extra-file={cfg}", "--connect-timeout=10", "--sandbox"]
        if db.get("ssl") in (True, "require", "true"):
            cmd.append("--ssl")
            if db.get("insecure_tls"):
                cmd.append("--skip-ssl-verify-server-cert")
        if ro:
            cmd.append("--init-command=SET SESSION TRANSACTION READ ONLY")
        if sql is not None:
            cmd += (["--batch"] if fmt == "csv" else ["--table"]) + ["-e", sql]
        return subprocess.call(cmd, **core.runner())
    finally:
        os.unlink(cfg)


def cmd_db(services, args):
    dbs = core.part_services(services, "db")
    if not args:
        if not dbs:
            print("No database services.")
            return 0
        print(f"{'Name':<16}{'Engine':<10}{'Access':<8}{'Target':<36}Status")
        for n, s in dbs.items():
            d = core.render(s["db"])
            tgt = f"{d.get('host', '')}:{d.get('port') or (3306 if db_engine(d) == 'mysql' else 5432)}/{d.get('database', '')}"
            print(f"{n:<16}{db_engine(d):<10}{tclib.access(d.get('access')):<8}{tgt:<36}{core.status(s)}")
        return 0
    name, rest = args[0], args[1:]
    db = gate.get_part(services, name, "db")
    fmt = "table"
    if rest and rest[0] in ("--csv", "--tsv"):
        fmt, rest = "csv", rest[1:]
    if not rest:
        sys.exit(f"usage: svc db {name} \"<SQL>\" | tables | describe <table> | shell")
    pg = db_engine(db) == "postgres"
    mode = gate.access_mode(db)
    if rest[0] == "shell":
        if mode == "read":
            sys.exit(f"svc: {name} is read-only – use svc db {name} \"<SQL>\" (the shell could switch the session to read-write)")
        return gate.guarded(name, "db", mode, "interactive SQL shell", lambda: db_run(name, db, interactive=True), interactive=True)
    if rest[0] == "tables":
        return db_run(name, db, "\\dt+" if pg else "SHOW FULL TABLES", fmt)
    if rest[0] == "describe" and len(rest) == 2:
        t = rest[1]
        if not re.fullmatch(r"[A-Za-z0-9_.]+", t):
            sys.exit("svc: invalid table name")
        return db_run(name, db, f"\\d {t}" if pg else f"DESCRIBE `{t.replace('.', '`.`')}`", fmt)
    sql = " ".join(rest)
    if mode != "read" and sql_writes(sql):
        return gate.guarded(name, "db", mode, f"SQL: {sql}", lambda: db_run(name, db, sql, fmt))
    return db_run(name, db, sql, fmt)


def db_test(name, db):
    """(ok, detail)"""
    if db_engine(db) == "postgres":
        addr, lport = core.tunnel(db.get("host", ""), db.get("port", 5432))
        env = dict(PGHOST=str(db.get("host", "")), PGHOSTADDR=addr, PGPORT=str(lport), PGDATABASE=str(db.get("database", "")),
                   PGUSER=str(db.get("user", "")), PGPASSWORD=str(db.get("password", "")), PGSSLMODE=pg_sslmode(db),
                   PGCONNECT_TIMEOUT="8", PGOPTIONS="-c default_transaction_read_only=on")
        r = subprocess.run(["psql", "-X", "-At", "-c", "SELECT version()"], capture_output=True, text=True, timeout=20, **core.runner(env))
    else:
        addr, lport = core.tunnel(db.get("host", ""), db.get("port", 3306))
        cfg = core._shm_file("[client]\n" + "".join(f"{k}={v}\n" for k, v in (
            ("host", addr), ("port", lport), ("user", db.get("user", "")),
            ("password", db.get("password", "")), ("database", db.get("database", ""))) if v != ""))
        try:
            r = subprocess.run(["mariadb", f"--defaults-extra-file={cfg}", "--connect-timeout=8", "-N", "-e", "SELECT VERSION()"],
                               capture_output=True, text=True, timeout=20, **core.runner())
        finally:
            os.unlink(cfg)
    if r.returncode == 0:
        return True, f"{db_engine(db)} {db.get('host')}/{db.get('database', '')} → {r.stdout.strip()[:80]} ({tclib.access(db.get('access'))})"
    return False, (r.stderr.strip().splitlines() or ["error"])[-1][:180]


def sql_writes(sql):
    s = sql.strip()
    if safeguards.READ_SQL.match(s) and not (s.lower().startswith(("explain analyze",)) and safeguards.WRITE_SQL.search(s)):
        return False
    if re.match(r"^\s*with\b", s, re.I):
        return bool(safeguards.WRITE_SQL.search(s))
    return True
