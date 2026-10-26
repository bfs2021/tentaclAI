"""tentacontrol's log on stdout (docker logs, Logs → Containers): one line per event, LOG_FORMAT=text (default, for
people) or json (one object per line, for a log shipper), LOG_LEVEL (default info). A line written while a web request
is handled carries its request id – the X-Request-ID response header and the `cid` of the audit entries it wrote."""
import contextvars
import json
import logging
import os
import re
import secrets
import sys
import time

REQUEST_ID = contextvars.ContextVar("request_id", default="")
_ID_RE = re.compile(r"[A-Za-z0-9._-]{8,64}")


class Formatter(logging.Formatter):
    def __init__(self, as_json=False):
        super().__init__()
        self.as_json = as_json

    def format(self, record):
        ts = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(record.created)) + time.strftime("%z", time.localtime(record.created))
        rid, msg = REQUEST_ID.get(), record.getMessage()
        err = self.formatException(record.exc_info) if record.exc_info else ""
        if self.as_json:
            d = {"ts": ts, "level": record.levelname.lower(), "logger": record.name, "msg": msg}
            if rid:
                d["request_id"] = rid
            if err:
                d["error"] = err
            return json.dumps(d, ensure_ascii=False)
        return f"{ts} {record.levelname:<7} {record.name.removeprefix('tentacontrol.')}: {msg}" + (f" [req {rid}]" if rid else "") + (f"\n{err}" if err else "")


def setup():
    """the handler of the "tentacontrol" logger – once; uvicorn's own loggers stay as they are"""
    root = logging.getLogger("tentacontrol")
    if root.handlers:
        return root
    h = logging.StreamHandler(sys.stdout)
    h.setFormatter(Formatter(os.environ.get("LOG_FORMAT", "text").lower() == "json"))
    root.addHandler(h)
    level = os.environ.get("LOG_LEVEL", "info").upper()
    root.setLevel(level if level in ("DEBUG", "INFO", "WARNING", "ERROR") else "INFO")
    root.propagate = False
    return root


def get(name):
    """the logger of one area, e.g. get("usage") → tentacontrol.usage"""
    setup()
    return logging.getLogger(f"tentacontrol.{name}")


def request_id(header=""):
    """the id of a web request: the proxy's X-Request-ID when it is well-formed, else a new one"""
    return header if _ID_RE.fullmatch(header or "") else secrets.token_hex(6)
