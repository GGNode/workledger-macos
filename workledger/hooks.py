from __future__ import annotations

import json
import shlex
from pathlib import Path
from .config import Config
from .store import Store
from .util import now, digest, atomic_write
from .adapters.common import Writer


def record_hook(config: Config, data: dict):
    # Spool atomically. The agent never waits for report generation or an HTTP server.
    received = now()
    row = {"schema": "workledger.hook.v1", "received_at": received, "payload": data}
    path = config.home / "inbox" / ("claude-hook-" + digest([received, data])[:24] + ".jsonl")
    atomic_write(path, json.dumps(row, ensure_ascii=False) + "\n")


def process_hook(row: dict, store: Store):
    data = row.get("payload", {})
    at = row.get("received_at")
    main = data.get("session_id")
    if not main:
        raise ValueError("Claude hook session_id missing")
    agent = data.get("agent_id")
    event = data.get("hook_event_name")
    native = main + "/" + agent if agent else main
    store.session("claude", main)
    w = Writer(store, "claude", native, cwd=data.get("cwd", ""), parent=main if agent else None, relation="delegation" if agent else "root", metadata={"hook_seen": event})
    if event == "SubagentStart":
        # Resumed subagents may have several runs: don't overwrite earlier intervals.
        store.cache_set("claude-hook-start:" + native, at)
    elif event == "SubagentStop":
        started = store.cache_get("claude-hook-start:" + native)
        if started:
            w.end("hook-run:" + started, at, started=started)
            store.cache_set("claude-hook-start:" + native, None)
    elif event == "PostToolUse":
        cid = data.get("tool_use_id") or digest([at, data.get("tool_name")])[:24]
        name = data.get("tool_name", "tool")
        w.call(cid, name, data.get("tool_input", {}), at)
        w.result(cid, data.get("tool_response", {}), at)


def hook_settings_fragment(executable: str, home: Path):
    command = " ".join(shlex.quote(v) for v in [executable, "--home", str(home), "hook", "--source", "claude"]) + " || true"
    return {"hooks": {name: [{"hooks": [{"type": "command", "command": command, "timeout": 5}]}] for name in ("SubagentStart", "SubagentStop", "PostToolUse")}}
