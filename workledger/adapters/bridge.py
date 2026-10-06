from __future__ import annotations

from ..store import Store
from ..util import digest, content_text, now, stamp

ALLOWED_KINDS = {"user_message", "agent_message", "delegated_instruction", "file_edit", "document_change", "run_interval", "tool_call", "tool_result", "context", "note", "browser_message", "review"}


def ingest_bridge(rows: list[dict], store: Store) -> list[str]:
    """Explicit small interchange format for extensions and local/custom agents.

    Native adapters should be preferred. Producer-reported actor=human is *not*
    proof of physical input; explicit human confirmation is a separate audit action.
    """
    ids = []
    for r in rows:
        if r.get("schema") == "workledger.hook.v1":
            from ..hooks import process_hook
            process_hook(r, store)
            continue
        if r.get("schema") != "workledger.event.v1":
            raise ValueError("Bridge schema must be workledger.event.v1")
        source = r.get("source", "bridge")
        if not isinstance(source, str) or not source or len(source) > 80:
            raise ValueError("Invalid source")
        key, kind = r.get("id"), r.get("kind")
        if not isinstance(key, str) or not key or kind not in ALLOWED_KINDS:
            raise ValueError("Bridge needs a stable id and a recognized kind")
        native = r.get("session_id")
        sid = None
        if native:
            sid = store.session(source, native, title=r.get("session_title", ""), cwd=r.get("cwd", ""), parent=r.get("parent_session_id"), relation=r.get("relation", "root"), evidence="producer_reported")
        actor = r.get("actor", "unknown")
        # Confirmation is allowed only through UI/CLI annotate, not incoming log assertions.
        if actor == "human":
            actor = "unknown"
        chronology = r.get("chronology")
        at = r.get("occurred_at")
        if chronology == "live_observed" and not stamp(at):
            raise ValueError("live_observed requires the original observation timestamp")
        if chronology in {"historical", "unknown"}:
            at = None  # a DOM baseline observation cannot acquire a source creation time
        meta = r.get("metadata", {})
        if not isinstance(meta, dict):
            raise ValueError("metadata must be an object")
        meta = {**meta, "producer_reported": True}
        ids.append(store.event(source, key, kind, session_id=sid, actor=actor, occurred_at=at,
                    ended_at=r.get("ended_at"), chronology=chronology, text=r.get("text", ""),
                    artifact=r.get("artifact", ""), evidence=r.get("evidence", "bridge"), metadata=meta))
    return ids


def import_chatgpt_export(value, store: Store):
    """Official data export: message create_time, not conversation update_time."""
    conversations = value if isinstance(value, list) else [value]
    for c in conversations:
        if not isinstance(c, dict) or not isinstance(c.get("mapping"), dict):
            raise ValueError("Expected ChatGPT conversations.json mapping")
        cid = c.get("id") or c.get("conversation_id")
        if not cid:
            raise ValueError("Conversation id missing")
        sid = store.session("chatgpt", cid, title=c.get("title", ""), created_at=c.get("create_time"))
        # Export branches are kept as evidence; only current branch enters today's report.
        active = set()
        nodeid = c.get("current_node")
        while nodeid and nodeid not in active:
            active.add(nodeid)
            nodeid = c["mapping"].get(nodeid, {}).get("parent")
        for nid, node in c["mapping"].items():
            msg = node.get("message") or {}
            role = (msg.get("author") or {}).get("role")
            if role not in {"user", "assistant"}:
                continue
            if (msg.get("metadata") or {}).get("is_visually_hidden_from_conversation"):
                continue
            if (msg.get("channel") or (msg.get("metadata") or {}).get("channel")) in {"analysis", "commentary"}:
                continue
            text = content_text(msg.get("content", {}))
            if not text.strip():
                continue
            on_branch = not active or nid in active
            store.event("chatgpt", f"{cid}/{msg.get('id', nid)}", "user_message" if role == "user" else "agent_message",
                 session_id=sid, actor="unknown" if role == "user" else "agent", occurred_at=msg.get("create_time"),
                 chronology=None if on_branch else "historical", text=text, evidence="chatgpt_export_create_time",
                 metadata={"role": role, "current_branch": on_branch, "message_parent": node.get("parent")})
