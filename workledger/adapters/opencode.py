from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from urllib.parse import quote
from ..store import Store
from ..util import nested
from .common import Writer


def parse_opencode_export(export: dict, store: Store, *, origin="export"):
    info = export.get("info", export.get("session", {}))
    if not info.get("id"):
        raise ValueError("OpenCode export needs info.id and messages[{info,parts}]")
    parent = info.get("parentID", info.get("parent_id"))
    w = Writer(store, "opencode", info["id"], cwd=info.get("directory", ""), parent=parent,
               relation="delegation" if parent else "root", title=info.get("title", ""),
               created_at=nested(info, "time", "created", default=info.get("time_created")), metadata={"origin": origin})
    for row in export.get("messages", []):
        msg = row.get("info", {})
        at = nested(msg, "time", "created", default=msg.get("time_created"))
        mid = msg.get("id")
        if not mid:
            continue
        parts = row.get("parts", [])
        textparts = [p for p in parts if p.get("type") == "text" and not p.get("synthetic") and not p.get("ignored")]
        w.message(mid, msg.get("role"), textparts, at)
        start = nested(msg, "time", "created")
        end = nested(msg, "time", "completed")
        if msg.get("role") == "assistant" and end:
            w.end(mid, end, started=start)
        for part in parts:
            if part.get("type") != "tool":
                continue
            state = part.get("state", {})
            cid = part.get("callID", part.get("id"))
            began = nested(state, "time", "start", default=at)
            ended = nested(state, "time", "end", default=at)
            w.call(cid, part.get("tool", "tool"), state.get("input", {}), began)
            if state.get("status") in {"completed", "error"}:
                w.result(cid, state, ended, explicit_ok=state["status"] == "completed")


def import_opencode_db(path: Path, store: Store):
    """Read a consistent view including live WAL; never migrate or mutate source DB."""
    db = sqlite3.connect("file:" + quote(str(path.resolve()), safe="/") + "?mode=ro", uri=True, timeout=5)
    db.row_factory = sqlite3.Row
    try:
        db.execute("PRAGMA query_only=ON")
        db.execute("BEGIN")
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {"session", "message", "part"} <= tables:
            raise ValueError("OpenCode schema needs session/message/part tables; use opencode export for this version")
        for table, required in {"session": {"id"}, "message": {"id", "session_id", "data"}, "part": {"id", "message_id", "data"}}.items():
            cols = {r[1] for r in db.execute(f'PRAGMA table_info("{table}")')}
            if not required <= cols:
                raise ValueError(f"Unsupported OpenCode {table} columns: missing {required-cols}")
        sessions = [dict(r) for r in db.execute('SELECT * FROM "session"')]
        for raw in sessions:
            info = dict(raw)
            if isinstance(info.get("data"), str):
                info.update(json.loads(info["data"]))
            # Canonical SQLite schema stores identity/time outside the JSON body.
            messages = []
            for row in db.execute('SELECT * FROM "message" WHERE session_id=? ORDER BY id', (raw["id"],)):
                m = dict(row)
                data = json.loads(m.get("data") or "{}")
                data.setdefault("id", m["id"])
                data.setdefault("time", {})
                if m.get("time_created") is not None:
                    data["time"].setdefault("created", m["time_created"])
                parts = []
                for pr in db.execute('SELECT * FROM "part" WHERE message_id=? ORDER BY id', (m["id"],)):
                    p = dict(pr)
                    value = json.loads(p.get("data") or "{}")
                    value.setdefault("id", p["id"])
                    parts.append(value)
                messages.append({"info": data, "parts": parts})
            parse_opencode_export({"info": info, "messages": messages}, store, origin=str(path))
    finally:
        db.close()
