from __future__ import annotations

import json
import re
from pathlib import Path
from ..store import Store
from ..util import content_text, digest, nested, stamp
from .common import Writer, as_dict, blocks, successful


def parse_codex(rows: list[dict], path: Path, store: Store):
    meta = next((r.get("payload", {}) for r in rows if r.get("type") == "session_meta"), None)
    if not meta or not meta.get("id"):
        raise ValueError("Codex session_meta.id missing; unsupported rollout schema")
    source = meta.get("source", {})
    spawned = nested(source, "subagent", "thread_spawn", default={})
    parent = meta.get("parent_thread_id") or spawned.get("parent_thread_id")
    fork = meta.get("forked_from_id")
    w = Writer(store, "codex", meta["id"], cwd=meta.get("cwd", ""), parent=parent or fork,
               relation="delegation" if parent else "fork" if fork else "root", created_at=meta.get("timestamp"),
               metadata={"log": str(path), "parser": "rollout-jsonl", "agent_version": meta.get("cli_version", "unknown")})
    response_roles = {r.get("payload", {}).get("role") for r in rows if r.get("type") == "response_item" and r.get("payload", {}).get("type") == "message"}
    current_turn = ""
    for i, row in enumerate(rows):
        p = row.get("payload", {})
        if not isinstance(p, dict):
            continue
        at = row.get("timestamp")
        t = p.get("type")
        key = p.get("id") or p.get("call_id") or f"line-{row.get('__workledger_line_number',i)}"
        if row.get("type") == "response_item":
            if t == "message":
                text = content_text(p.get("content", []))
                injected = text.lstrip().startswith(("<environment_context>", "# AGENTS.md instructions", "<permissions instructions>", "<subagent_notification>"))
                w.message(key, p.get("role"), p.get("content"), at, injected=injected, metadata={"phase": p.get("phase", "")})
            elif t in {"function_call", "custom_tool_call"}:
                w.call(p.get("call_id", key), p.get("name", "tool"), p.get("arguments", {"input": p.get("input", "")}), at)
            elif t in {"function_call_output", "custom_tool_call_output"}:
                output = p.get("output", "")
                obj = as_dict(output)
                txt = content_text(output)
                failed_exit = re.search(r"(?:exit(?:ed)?(?: with)? code|exit_code)[:\s]+(-?\d+)", txt, re.I)
                ok = successful(obj) and not (failed_exit and int(failed_exit[1]) != 0) and not re.search(r"(?im)^\s*(error[: ]|failed|patch rejected)", txt)
                tool_name = w.calls.get(str(p.get("call_id", key)), {}).get("name", "")
                if tool_name.split(".")[-1] == "apply_patch" and not ("success" in txt.lower() or obj.get("success") is True or nested(obj, "metadata", "exit_code") == 0):
                    ok = False
                w.result(p.get("call_id", key), output, at, explicit_ok=ok)
        elif row.get("type") == "event_msg":
            if t in {"user_message", "agent_message"}:
                role = "user" if t == "user_message" else "assistant"
                if role not in response_roles:
                    w.message(key, role, p.get("message", ""), at)
            elif t in {"task_started", "turn_started"}:
                current_turn = str(p.get("turn_id", key))
                w.start(current_turn, p.get("started_at") or at)
            elif t in {"task_complete", "turn_complete", "task_completed", "turn_aborted"}:
                w.end(p.get("turn_id", current_turn or key), p.get("completed_at") or at,
                      started=p.get("started_at"), status="aborted" if "aborted" in t else "completed")
            elif t == "patch_apply_end":
                cid = str(p.get("call_id", key))
                if p.get("success") is True:
                    for f in p.get("changes", {}):
                        f = str((Path(w.cwd) / f).resolve()) if w.cwd else f
                        w.event(cid + "/" + f, "file_edit", at, actor="agent", artifact=f, text="apply_patch 已返回成功", evidence="successful_tool_result", metadata={"call_id": cid, "tool": "apply_patch"})
            elif t in {"collab_agent_spawn_end", "collab_agent_spawn_complete"}:
                child = p.get("new_thread_id") or p.get("receiver_thread_id")
                if child:
                    store.session("codex", child, parent=p.get("sender_thread_id", w.native), relation="delegation", created_at=at, evidence="native_spawn_event")


def parse_claude(rows: list[dict], path: Path, store: Store):
    first = next((r for r in rows if r.get("sessionId")), None)
    if not first:
        raise ValueError("Claude sessionId missing; unsupported transcript schema")
    main = str(first["sessionId"])
    agent = next((r.get("agentId") for r in rows if r.get("agentId")), None)
    is_subpath = "subagents" in path.parts
    if is_subpath and not agent:
        agent = path.stem.removeprefix("agent-")
    native = main + "/" + str(agent) if agent else main
    # parentUuid is a MESSAGE link, NEVER a session parent.
    w = Writer(store, "claude", native, parent=main if agent else None, relation="delegation" if agent else "root", cwd=first.get("cwd", ""), created_at=first.get("timestamp"), metadata={"log": str(path), "parent_precision": "root-container only; deeper lineage needs hook" if agent else "root"})
    for i, r in enumerate(rows):
        msg = r.get("message", {})
        if not isinstance(msg, dict):
            continue
        at = r.get("timestamp") or msg.get("timestamp")
        key = r.get("uuid") or msg.get("id") or f"line-{r.get('__workledger_line_number',i)}"
        role = msg.get("role", r.get("type"))
        content = msg.get("content", "")
        if role in {"user", "assistant"}:
            w.message(key, role, content, at, injected=r.get("isMeta", False), metadata={"message_parent": r.get("parentUuid")})
        for b in blocks(content):
            if b.get("type") == "tool_use":
                w.call(b.get("id", key), b.get("name", "tool"), b.get("input", {}), at)
            elif b.get("type") == "tool_result":
                w.result(b.get("tool_use_id", key), b, at)


def _pi_parent(parent: str) -> str:
    # Pi stores a file path, not an agent id. Read only its bounded first header.
    try:
        p = Path(parent).expanduser()
        if p.is_file():
            with p.open(encoding="utf-8") as f:
                header = json.loads(f.readline(16384))
            if header.get("type") == "session" and header.get("id"):
                return str(header["id"])
    except (OSError, ValueError):
        pass
    return "unresolved-" + digest(parent)[:12]


def parse_pi(rows: list[dict], path: Path, store: Store):
    h = next((r for r in rows if r.get("type") == "session"), {})
    if not h.get("id") or h.get("version", 1) not in {1, 2, 3}:
        raise ValueError("Unsupported Pi session header/version")
    parent = _pi_parent(h["parentSession"]) if h.get("parentSession") else None
    w = Writer(store, "pi", h["id"], cwd=h.get("cwd", ""), parent=parent, relation="fork" if parent else "root", created_at=h.get("timestamp"), metadata={"log": str(path), "version": h.get("version"), "parent_file": h.get("parentSession")})
    for i, r in enumerate(rows):
        if r.get("type") != "message":
            continue  # compaction and branch summaries are old context, not new outcomes
        msg = r.get("message", {})
        role = msg.get("role")
        key = r.get("id", f"line-{r.get('__workledger_line_number',i)}")
        at = r.get("timestamp") or msg.get("timestamp")
        w.message(key, role, msg.get("content", ""), at, metadata={"message_parent": r.get("parentId")})
        for b in blocks(msg.get("content")):
            if b.get("type") in {"toolCall", "tool_call"}:
                w.call(b.get("id", key), b.get("name", "tool"), b.get("arguments", {}), at)
        if role in {"toolResult", "tool_result"}:
            w.result(msg.get("toolCallId", key), msg, at, name=msg.get("toolName"))


def parse_dsh(rows: list[dict], path: Path, store: Store):
    h = next((r.get("header", r) for r in rows if (r.get("header") or (r.get("id") and "seq" not in r and "version" in r))), {})
    if not h.get("id"):
        raise ValueError("DSH SessionHeader.id missing. Export current raw SessionEvent JSONL or use bridge")
    version = h.get("version")
    if version not in {2, 3, 4}:
        raise ValueError(f"DSH format version {version!r} not supported natively (v0/v1 packed deltas need upstream migration; never rewrite originals)")
    events = [r.get("event", r) for r in rows if "seq" in r or "event" in r]
    seqs = [r.get("seq") for r in events]
    if any(not isinstance(v, int) for v in seqs) or (seqs and seqs != list(range(seqs[0], seqs[0] + len(seqs)))):
        raise ValueError("DSH noncontiguous event sequence; import refused instead of silently dropping context")
    cut = max((r["seq"] for r in events if r.get("type") == "session/end-seed" and nested(r, "data", "inherited", default=False)), default=None)
    if h.get("isSeeded") and cut is None:
        raise ValueError("Seeded DSH session lacks inherited-prefix marker; refused to count copied history as new work")
    parent = h.get("parentSession")
    if isinstance(parent, dict):
        parent = parent.get("id") or parent.get("sessionId")
    relation = "delegation" if parent and h.get("origin") == "subagent" else "lineage" if parent else "root"
    w = Writer(store, "dsh", h["id"], parent=parent, relation=relation, cwd=h.get("cwd", ""), created_at=h.get("createdAt"), metadata={"log": str(path), "version": version, "inherited_prefix": cut})
    for r in events:
        seq = r.get("seq")
        if not isinstance(seq, int):
            raise ValueError("DSH event seq is missing; packed or unknown format")
        if cut is not None and seq <= cut:
            continue
        t, data, at = r.get("type"), r.get("data", {}), r.get("time")
        if not isinstance(data, dict):
            continue
        if isinstance(r.get("surfaceOp"), dict):
            continue  # replacement/compaction is resurfaced context
        if t == "user/message":
            source = data.get("source", "unknown")
            external = source == "user" or (isinstance(source, dict) and source.get("kind", source.get("type")) == "user")
            w.message(str(seq), "user", data.get("content", ""), at, injected=not external and not w.child, metadata={"input_source": source})
        elif t == "assistant/message":
            msg = data.get("message", {})
            w.message(str(seq), "assistant", msg.get("content", ""), at)
        elif t == "tool/call":
            w.call(data.get("callId", seq), data.get("name", "tool"), data.get("arguments", {}), at)
        elif t == "tool/result":
            msg = data.get("message", {})
            result_blocks = [b for b in blocks(msg.get("content")) if b.get("type") in {"tool_result", "toolResult"}]
            result = result_blocks[0] if result_blocks else msg
            cid = nested(msg, "source", "callId") or result.get("toolCallId") or msg.get("callId") or msg.get("toolCallId") or data.get("callId") or seq
            w.result(cid, result, at, explicit_ok=not data.get("error") and successful(result))
        elif t == "turn/start":
            w.start(data.get("turn", seq), at)
        elif t == "turn/end":
            w.end(data.get("turn", seq), at, status=nested(data, "reason", "kind", default="unknown"))
