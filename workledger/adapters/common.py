from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Iterable
from ..store import Store
from ..util import content_text, digest, nested, stamp, sanitize, redact


def as_dict(value: Any) -> dict:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {"input": value}
        except (ValueError, TypeError):
            return {"input": value}
    return {}


def successful(value: Any) -> bool:
    """Only an explicit successful tool settlement, never an unexecuted proposal."""
    if isinstance(value, dict):
        if value.get("isError") or value.get("is_error") or value.get("error"):
            return False
        if value.get("success") is False or value.get("status") in {"failed", "error", "declined", "cancelled", "running", "pending"}:
            return False
        for key in ("exit_code", "exitCode"):
            if key in value and value[key] != 0:
                return False
        if "exit_code" in nested(value, "metadata", default={}):
            return value["metadata"]["exit_code"] == 0
    return True


def artifact_paths(name: str, arguments: dict, cwd: str) -> list[str]:
    """Shell commands are not parsed as proof of file writes."""
    short = name.lower().split(".")[-1]
    if short not in {"edit", "write", "multiedit", "apply_patch", "write_file", "edit_file", "str_replace_editor", "patch", "create_file", "notebookedit"}:
        return []
    out = []
    for key in ("file_path", "filePath", "path", "notebook_path", "filename"):
        v = arguments.get(key)
        if isinstance(v, str):
            out.append(v)
    patch = arguments.get("patch", arguments.get("input", arguments.get("patchText", "")))
    if isinstance(patch, str):
        out += re.findall(r"^\*\*\* (?:Update|Add|Delete) File: (.+)$", patch, re.M)
        out += re.findall(r"^\*\*\* Move to: (.+)$", patch, re.M)
    return sorted(set(str((Path(cwd) / p).resolve()) if cwd and not Path(p).is_absolute() else p for p in out))


class Writer:
    def __init__(self, store: Store, source: str, native: str, *, cwd="", parent=None, relation="root", title="", created_at=None, metadata=None):
        self.store, self.source, self.native, self.cwd = store, source, str(native), str(cwd)
        self.child = relation == "delegation"
        self.sid = store.session(source, str(native), cwd=self.cwd, parent=parent, relation=relation, title=title, created_at=created_at, metadata=metadata)
        self.child = store.conn.execute("SELECT relation FROM sessions WHERE id=?", (self.sid,)).fetchone()[0] == "delegation"
        self.calls: dict[str, dict] = {}
        self.starts: dict[str, Any] = {}

    def event(self, key, kind, at=None, **kwargs):
        return self.store.event(self.source, f"{self.native}/{key}", kind, session_id=self.sid, occurred_at=at, **kwargs)

    def message(self, key, role, content, at, *, injected=False, metadata=None):
        visible = [b for b in content if isinstance(b, str) or (isinstance(b, dict) and b.get("type") in {"text", "input_text", "output_text"})] if isinstance(content, list) else content
        text = content_text(visible)
        if not text.strip():
            return
        if injected or role not in {"user", "assistant"}:
            return
        actor = "agent" if role == "assistant" or self.child else "unknown"
        kind = "agent_message" if role == "assistant" else ("delegated_instruction" if self.child else "user_message")
        if role == "user" and not self.child:
            row = self.store.conn.execute("SELECT title FROM sessions WHERE id=?", (self.sid,)).fetchone()
            if row and not row[0]:
                self.store.session(self.source, self.native, title=text[:160])
        self.event(key, kind, at, actor=actor, text=text, evidence="native_user_channel" if actor == "unknown" else "native_transcript", metadata=metadata or {})

    def call(self, cid, name, arguments, at):
        cid = str(cid)
        self.calls[cid] = {"name": str(name), "arguments": as_dict(arguments), "at": at}
        args = sanitize(as_dict(arguments))
        encoded = json.dumps(args, ensure_ascii=False, sort_keys=True)
        operation = digest([self.cwd, str(name), args])
        self.calls[cid]["operation_id"] = operation
        # Captured input is inert, bounded, and redacted; never execute it.
        self.event(cid, "tool_call", at, actor="agent", text=str(name), metadata={
            "call_id": cid, "tool": str(name), "operation_id": operation,
            "arguments_excerpt": encoded[:16000], "arguments_chars": len(encoded),
            "arguments_truncated": len(encoded) > 16000})

    def result(self, cid, value, at, *, explicit_ok=None, name=None):
        cid = str(cid)
        call = self.calls.get(cid, {})
        ok = successful(value) if explicit_ok is None else bool(explicit_ok)
        tool = name or call.get("name", "tool")
        output = content_text(value.get("content", value.get("output", ""))) if isinstance(value, dict) else content_text(value)
        if not output and isinstance(value, dict):
            output = str(value.get("error") or "")
        output = redact(output)
        args = sanitize(call.get("arguments", {}))
        operation = call.get("operation_id")  # Missing call/input cannot establish retry equivalence.
        self.event(cid, "tool_result", at, actor="agent", text=f"{tool}: {'completed' if ok else 'failed'}", metadata={
            "call_id": cid, "tool": tool, "success": ok, "operation_id": operation,
            "output_excerpt": output[:2000], "output": output[:131072],
            "output_chars": len(output), "output_truncated": len(output) > 131072,
            "operation_excerpt": json.dumps(args, ensure_ascii=False, sort_keys=True)[:4000],
            "started_at": stamp(call.get("at")), "settled_at": stamp(at)})
        if ok:
            for path in artifact_paths(tool, call.get("arguments", {}), self.cwd):
                self.event(cid + "/" + path, "file_edit", at, actor="agent", artifact=path, text=f"{tool} 已返回成功", evidence="successful_tool_result", metadata={"call_id": cid, "tool": tool, "verification": "tool-reported; final disk content not independently verified"})
        if call.get("at") and stamp(at):
            self.event(cid, "tool_interval", call["at"], actor="agent", ended_at=at, text=tool, metadata={"success": ok})

    def start(self, key, at):
        self.starts[str(key)] = at

    def end(self, key, at, *, started=None, status="completed"):
        start = started or self.starts.get(str(key))
        if stamp(start) and stamp(at) and stamp(start) <= stamp(at):
            self.event(str(key), "run_interval", start, actor="agent", ended_at=at, text=status, metadata={"status": status})


def blocks(value) -> Iterable[dict]:
    return (v for v in value if isinstance(v, dict)) if isinstance(value, list) else ()
