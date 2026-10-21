"""egress gateway – pure functions, loaded from egress/gw.py without starting it."""
import importlib.machinery
import importlib.util
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("EGRESS_DEFAULT_LIST", os.path.join(ROOT, "lib", "egress-default.txt"))
os.environ.setdefault("DATA_DIR", os.environ.get("DATA_DIR", "/tmp"))
_loader = importlib.machinery.SourceFileLoader("gw", os.path.join(ROOT, "egress", "gw.py"))
gw = importlib.util.module_from_spec(importlib.util.spec_from_loader("gw", _loader))
_loader.exec_module(gw)


def test_prune_drops_old_and_caps():
    now = 1_000_000_000
    entries = {f"h{i}:443": {"last": now - i} for i in range(gw.SEEN_MAX + 50)}
    entries["old:443"] = {"last": now - (gw.SEEN_DAYS + 1) * 86400}
    kept = gw.prune(entries, now)
    assert len(kept) == gw.SEEN_MAX and "old:443" not in kept
    assert "h0:443" in kept and f"h{gw.SEEN_MAX + 49}:443" not in kept  # the most recent stay
