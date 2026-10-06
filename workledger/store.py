from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from .util import digest, json_text, now, redact, stamp, sanitize

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sessions(
 id TEXT PRIMARY KEY, source TEXT NOT NULL, native_id TEXT NOT NULL,
 title TEXT NOT NULL DEFAULT '', cwd TEXT NOT NULL DEFAULT '',
 parent_id TEXT, relation TEXT NOT NULL DEFAULT 'root',
 created_at TEXT, evidence TEXT NOT NULL DEFAULT '', metadata TEXT NOT NULL DEFAULT '{}');
CREATE INDEX IF NOT EXISTS sessions_parent ON sessions(parent_id);
CREATE TABLE IF NOT EXISTS events(
 id TEXT PRIMARY KEY, source TEXT NOT NULL, native_id TEXT NOT NULL,
 session_id TEXT, kind TEXT NOT NULL, actor TEXT NOT NULL,
 occurred_at TEXT, observed_at TEXT NOT NULL, ended_at TEXT,
 chronology TEXT NOT NULL, text TEXT NOT NULL DEFAULT '',
 artifact TEXT NOT NULL DEFAULT '', evidence TEXT NOT NULL DEFAULT '',
 metadata TEXT NOT NULL DEFAULT '{}', fingerprint TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS event_time ON events(occurred_at);
CREATE INDEX IF NOT EXISTS event_session ON events(session_id);
CREATE TABLE IF NOT EXISTS revisions(
 id INTEGER PRIMARY KEY, event_id TEXT NOT NULL, observed_at TEXT NOT NULL,
 fingerprint TEXT NOT NULL, previous_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS attribution(
 id INTEGER PRIMARY KEY, event_id TEXT NOT NULL, actor TEXT NOT NULL,
 reason TEXT NOT NULL, changed_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS attr_target ON attribution(event_id, id);
CREATE TABLE IF NOT EXISTS cache(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS snapshots(
 path TEXT PRIMARY KEY, hash TEXT NOT NULL, structure TEXT NOT NULL,
 captured_at TEXT NOT NULL, stat_signature TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS issues(
 key TEXT PRIMARY KEY, source TEXT NOT NULL, detail TEXT NOT NULL,
 updated_at TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1);
"""


class Store:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.conn = sqlite3.connect(str(path), timeout=30)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA busy_timeout=30000")
        if self.conn.execute("PRAGMA journal_mode").fetchone()[0] != "wal":
            self.conn.execute("PRAGMA journal_mode=WAL")
        version = self.conn.execute("PRAGMA user_version").fetchone()[0]
        if version > 1:
            self.conn.close()
            raise ValueError(f"Database version {version} is newer than this WorkLedger")
        # Existing readers must not acquire a write lock during a long import.
        if version == 0:
            self.conn.executescript(SCHEMA)
            self.conn.execute("PRAGMA user_version=1")
            self.conn.commit()
        os.chmod(path, 0o600)

    def close(self):
        self.conn.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    @contextmanager
    def transaction(self):
        with self.conn:
            yield self

    def cache_get(self, key: str, default=None):
        row = self.conn.execute("SELECT value FROM cache WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def cache_set(self, key: str, value: Any):
        self.conn.execute("INSERT INTO cache VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, json_text(value)))

    def issue(self, key: str, source: str, detail: str):
        self.conn.execute("INSERT INTO issues VALUES(?,?,?,?,1) ON CONFLICT(key) DO UPDATE SET detail=excluded.detail,updated_at=excluded.updated_at,active=1", (key, source, redact(detail)[:2000], now()))

    def resolve_issue(self, key: str):
        self.conn.execute("UPDATE issues SET active=0 WHERE key=?", (key,))

    def session(self, source: str, native_id: str, *, title="", cwd="", parent=None, relation="root", created_at=None, evidence="native_log", metadata=None) -> str:
        title = redact(str(title))[:500]
        sid = f"{source}:{native_id}"
        parent_id = (f"{source}:{parent}" if parent and not str(parent).startswith(source + ":") else parent)
        if parent_id == sid:
            parent_id = None
            relation = "unknown"
            self.issue(f"cycle:{sid}", source, "Session points to itself; lineage left unresolved")
        if relation not in {"root", "delegation", "fork", "lineage", "unknown"}:
            raise ValueError("invalid session relation")
        existing = self.conn.execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone()
        if existing:
            old = dict(existing)
            oldmeta = json.loads(old["metadata"])
            if old["evidence"] == "explicit_session_link" and evidence != "explicit_session_link":
                parent_id, relation, evidence = old["parent_id"], old["relation"], old["evidence"]
            elif old["relation"] == "delegation" and relation in {"fork", "lineage"} and old["parent_id"] == parent_id:
                relation = old["relation"]
            oldmeta.update(metadata or {})
            # A late child log can enrich an earlier placeholder; never erase a known edge.
            vals = (title or old["title"], cwd or old["cwd"], parent_id or old["parent_id"], relation if parent_id else old["relation"], stamp(created_at) or old["created_at"], evidence, json_text(sanitize(oldmeta)), sid)
            self.conn.execute("UPDATE sessions SET title=?,cwd=?,parent_id=?,relation=?,created_at=?,evidence=?,metadata=? WHERE id=?", vals)
        else:
            self.conn.execute("INSERT INTO sessions VALUES(?,?,?,?,?,?,?,?,?,?)", (sid, source, str(native_id), str(title)[:500], str(cwd), parent_id, relation, stamp(created_at), evidence, json_text(sanitize(metadata or {}))))
        return sid

    def event(self, source: str, native_id: str, kind: str, *, session_id=None, actor="unknown", occurred_at=None, observed_at=None, ended_at=None, chronology=None, text="", artifact="", evidence="native_log", metadata=None) -> str:
        if actor not in {"human", "agent", "system", "unknown"}:
            raise ValueError("invalid actor")
        at = stamp(occurred_at)
        seen = stamp(observed_at) or now()
        chronology = chronology or ("source_time" if at else "unknown")
        if chronology not in {"source_time", "live_observed", "historical", "unknown"}:
            raise ValueError("invalid chronology")
        family = "message" if kind in {"user_message", "agent_message", "delegated_instruction"} else kind
        eid = digest([source, str(native_id), family])[:32]
        clean_text = redact(str(text))
        meta = dict(metadata or {})
        if len(clean_text) > 131072:
            meta.update(text_truncated=True, original_text_chars=len(clean_text), retained_text_chars=131072)
        data = {
            "id": eid, "source": source, "native_id": str(native_id), "session_id": session_id,
            "kind": kind, "actor": actor, "occurred_at": at, "observed_at": seen,
            "ended_at": stamp(ended_at), "chronology": chronology,
            "text": clean_text[:131072], "artifact": str(artifact), "evidence": evidence,
            "metadata": json_text(sanitize(meta)),
        }
        # Observation is not content; repeated imports don't manufacture edits.
        fingerprint = digest({k: v for k, v in data.items() if k not in {"observed_at"}})
        row = self.conn.execute("SELECT * FROM events WHERE id=?", (eid,)).fetchone()
        if row:
            if row["fingerprint"] == fingerprint:
                return eid
            self.conn.execute("INSERT INTO revisions(event_id,observed_at,fingerprint,previous_json) VALUES(?,?,?,?)", (eid, seen, row["fingerprint"], json_text(dict(row))))
            data["observed_at"] = min(seen, row["observed_at"])
        data["fingerprint"] = fingerprint
        fields = list(data)
        updates = ",".join(f"{k}=excluded.{k}" for k in fields if k != "id")
        self.conn.execute(f"INSERT INTO events({','.join(fields)}) VALUES({','.join('?' for _ in fields)}) ON CONFLICT(id) DO UPDATE SET {updates}", tuple(data.values()))
        return eid

    def get_event(self, eid: str) -> dict | None:
        row = self.conn.execute("SELECT * FROM events WHERE id=?", (eid,)).fetchone()
        return self._event_dict(row) if row else None

    def _event_dict(self, row) -> dict:
        d = dict(row)
        d["metadata"] = json.loads(d["metadata"])
        override = self.conn.execute("SELECT actor,reason,changed_at FROM attribution WHERE event_id=? ORDER BY id DESC LIMIT 1", (d["id"],)).fetchone()
        if override:
            d["recorded_actor"] = d["actor"]
            d["actor"] = override["actor"]
            d["attribution_override"] = dict(override)
        return d

    def events(self, start: str, end: str, *, include_unknown=False) -> list[dict]:
        sql = "SELECT * FROM events WHERE (occurred_at>=? AND occurred_at<? AND chronology IN ('source_time','live_observed'))"
        args = [start, end]
        if include_unknown:
            sql += " OR (occurred_at IS NULL AND observed_at>=? AND observed_at<?)"
            args += [start, end]
        sql += " ORDER BY COALESCE(occurred_at,observed_at),id"
        return [self._event_dict(r) for r in self.conn.execute(sql, args)]

    def annotate(self, eid: str, actor: str, reason: str):
        if actor not in {"human", "agent", "unknown"} or not reason.strip():
            raise ValueError("Actor and a nonempty reason are required")
        if not self.get_event(eid):
            raise ValueError("Event does not exist")
        self.conn.execute("INSERT INTO attribution(event_id,actor,reason,changed_at) VALUES(?,?,?,?)", (eid, actor, reason[:500], now()))

    def all_sessions(self) -> dict[str, dict]:
        result = {}
        for r in self.conn.execute("SELECT * FROM sessions"):
            d = dict(r)
            d["metadata"] = json.loads(d["metadata"])
            result[d["id"]] = d
        return result

    def summary(self):
        return {"events": self.conn.execute("SELECT count(*) FROM events").fetchone()[0], "sessions": self.conn.execute("SELECT count(*) FROM sessions").fetchone()[0], "issues": [dict(r) for r in self.conn.execute("SELECT source,detail,updated_at FROM issues WHERE active=1 ORDER BY source")]}
