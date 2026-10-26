"""A tiny stdio MCP server for tests/test_svc.py: three tools – get_state (read-only), turn_on (no hints), wipe
(destructive); a call answers with what it did, unknown tools with an error result."""
import json
import sys

TOOLS = [
    {"name": "get_state", "description": "State of an entity", "annotations": {"readOnlyHint": True}},
    {"name": "turn_on", "description": "Turn something on"},
    {"name": "wipe", "description": "Delete everything", "annotations": {"destructiveHint": True}},
]

for line in sys.stdin:
    msg = json.loads(line)
    rid, method = msg.get("id"), msg.get("method")
    if rid is None:
        continue
    if method == "initialize":
        res = {"protocolVersion": "2025-06-18", "capabilities": {"tools": {}}, "serverInfo": {"name": "fake", "version": "1"}}
    elif method == "tools/list":
        res = {"tools": TOOLS}
    elif method == "tools/call":
        name = msg["params"]["name"]
        known = name in {t["name"] for t in TOOLS}
        res = {"content": [{"type": "text", "text": f"did {name}" if known else f"no tool {name}"}], "isError": not known}
    else:
        res = {}
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": rid, "result": res}) + "\n")
    sys.stdout.flush()
