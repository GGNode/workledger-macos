"""Read-only evidence planning. Semantic themes never change native session edges."""
from __future__ import annotations

import json
import re
from collections import defaultdict, deque
from datetime import datetime, timedelta
from pathlib import Path

from ..util import day_bounds, digest, json_text, under

CONTENT_KINDS = {"user_message", "delegated_instruction", "agent_message", "file_edit", "document_change",
                 "tool_call", "tool_result", "note", "review", "browser_message", "context"}


def native_root(sid, sessions):
    seen = set()
    current = sid
    while current in sessions and current not in seen:
        seen.add(current)
        row = sessions[current]
        parent = row.get("parent_id")
        if row.get("relation") != "delegation" or not parent or parent not in sessions:
            return current
        current = parent
    return sid if current in seen else current


def generated_sessions(config, sessions, events):
    ids, nonces, titles = set(), set(), set()
    for path in (config.home / "analysis" / "runs").glob("*.json"):
        try:
            row = json.loads(path.read_text())
            if row.get("origin") != "workledger_analysis":
                continue
            nonces.add(row["run_id"]); titles.add(row["title"])
            ids.update("opencode:" + str(s) for s in row.get("session_ids", []))
        except (OSError, ValueError, KeyError):
            continue
    ids.update(sid for sid, row in sessions.items() if row.get("source") == "opencode" and row.get("title") in titles)
    for e in events:
        if e.get("source") != "opencode":
            continue
        match = re.match(r"^WORKLEDGER_ANALYSIS_RUN=([a-f0-9]{32})(?:\s|$)", e.get("text", ""))
        if match and match[1] in nonces and e.get("session_id"):
            ids.add(e["session_id"])
    changed = True
    while changed:
        more = {sid for sid, s in sessions.items() if s.get("parent_id") in ids and s.get("relation") in {"delegation", "fork", "lineage"}}
        changed = bool(more - ids); ids.update(more)
    return ids


def exclusions(config, events, sessions, *, known_own=None):
    own = generated_sessions(config, sessions, events) if known_own is None else known_own
    out = {}
    for e in events:
        meta = e.get("metadata", {})
        if e.get("session_id") in own or meta.get("origin") == "workledger_analysis":
            out[e["id"]] = "analysis_self"
        elif e.get("artifact") and under(Path(e["artifact"]), config.home):
            out[e["id"]] = "generated_workledger_data"
        elif meta.get("exclude_from_brief"):
            out[e["id"]] = "source_excluded"
        elif meta.get("purpose") == "diagnostic_probe" or e.get("text", "").strip() in {"PROBE_OK", "WORKLEDGER_PROBE_OK"}:
            # An exact probe EVENT is excluded, never every session mentioning a probe.
            out[e["id"]] = "diagnostic_probe"
    return out


def workspace(e, sessions, config):
    row = sessions.get(e.get("session_id"), {})
    for project in config.data["projects"]:
        if any(path and under(Path(path), Path(root).expanduser())
               for root in project.get("paths", []) for path in (e.get("artifact"), row.get("cwd"))):
            return project["name"]
    explicit = e.get("metadata", {}).get("project")
    if explicit and explicit != "其他工作":
        return explicit
    if row.get("cwd"):
        return Path(row["cwd"]).name or "未配置工作区"
    if e.get("artifact"):
        return Path(e["artifact"]).parent.name or "文档观察"
    return "未分类会话"


def task_id(e, sessions):
    if e.get("session_id"):
        return native_root(e["session_id"], sessions)
    # No session relationship is invented for a standalone document/note.
    return "standalone:" + digest([e.get("source"), str(Path(e.get("artifact") or ".").parent), e.get("metadata", {}).get("project")])[:16]


def evidence_body(e):
    meta = {k: v for k, v in e.get("metadata", {}).items()
            if k not in {"log", "physical_typing_verified", "message_parent"}
            and not (k == "output_excerpt" and "output" in e.get("metadata", {}))}
    return e.get("text", "") + ("\nMETADATA\n" + json.dumps(meta, ensure_ascii=False, sort_keys=True) if meta else "")


def prepare(config, store, events, sessions, day):
    start, _ = day_bounds(day, config.data["timezone"])
    excluded = exclusions(config, events, sessions)
    today_rows = [e for e in events if e["id"] not in excluded and e["kind"] in CONTENT_KINDS]
    tasks = {}
    for e in today_rows:
        tid = task_id(e, sessions)
        tasks.setdefault(tid, {"id": tid, "workspace": workspace(e, sessions, config), "session_ids": [], "events": []})
        tasks[tid]["events"].append(e)
        if e.get("session_id") and e["session_id"] not in tasks[tid]["session_ids"]:
            tasks[tid]["session_ids"].append(e["session_id"])
    earliest = (datetime.fromisoformat(start)-timedelta(days=config.data["analysis"]["context_days"])).isoformat(timespec="milliseconds")
    context_limit = config.data["analysis"]["history_events_per_task"]
    # Keep only a bounded tail per participating task; do not materialize a week
    # of every unrelated agent's full tool outputs in memory.
    history = defaultdict(lambda: deque(maxlen=context_limit))
    history_counts = defaultdict(int)
    own = generated_sessions(config, sessions, events)
    for row in store.conn.execute("SELECT * FROM events WHERE occurred_at>=? AND occurred_at<? AND chronology IN ('source_time','live_observed') ORDER BY occurred_at,id", (earliest, start)):
        if row["kind"] not in CONTENT_KINDS or row["session_id"] in own:
            continue
        if row["session_id"] and native_root(row["session_id"], sessions) not in tasks:
            continue
        e = store._event_dict(row)
        tid = task_id(e, sessions)
        if tid in tasks and not exclusions(config, [e], sessions, known_own=own):
            history_counts[tid] += 1
            history[tid].append(e)
    by_id = {e["id"]: {**e, "scope": "today", "task_id": task_id(e, sessions)} for e in today_rows}
    context_omitted = 0
    for tid, rows in history.items():
        context_omitted += history_counts[tid]-len(rows)
        tasks[tid]["history_ids"] = [e["id"] for e in rows]
        for e in rows:
            by_id[e["id"]] = {**e, "scope": "history", "task_id": tid}
    # Link potential retries by the exact recorded operation, not the word 'failed'.
    operations = defaultdict(list)
    for e in by_id.values():
        op = e.get("metadata", {}).get("operation_id")
        if e["kind"] == "tool_result" and op:
            operations[(e["task_id"], op)].append(e)
    for rows in operations.values():
        rows.sort(key=lambda e: (e.get("occurred_at") or "", e["id"]))
        for i, e in enumerate(rows):
            if e.get("metadata", {}).get("success") is False:
                later = next((r for r in rows[i+1:] if r["metadata"].get("success") is True), None)
                if later:
                    # This is a candidate, not an assertion that the whole task is fixed.
                    e["possible_retry_success"] = later["id"]
    queues = {}
    truncations = []
    segment = max(1000, config.data["analysis"]["chunk_chars"] // 3)
    for tid, task in tasks.items():
        records = []
        for e in sorted(task["events"], key=lambda e: (e.get("occurred_at") or "", e["id"])):
            evidence = by_id[e["id"]]
            text = evidence_body(e)
            size = max(1, (len(text)+segment-1)//segment)
            if any(e.get("metadata", {}).get(k) for k in ("text_truncated", "output_truncated", "arguments_truncated", "changes_truncated", "content_truncated")):
                truncations.append(e["id"])
            # Legacy 0.1 records cannot recover beyond their stored excerpt.
            if e["kind"] == "tool_result" and "output_excerpt" in e.get("metadata", {}) and "output_chars" not in e["metadata"]:
                truncations.append(e["id"])
            for n in range(size):
                records.append({"id": e["id"], "task_id": tid, "scope": "today", "kind": e["kind"],
                                "actor": e["actor"], "at": e.get("occurred_at"), "source": e["source"],
                                "artifact": e.get("artifact", ""), "evidence": e.get("evidence", ""),
                                "success": e.get("metadata", {}).get("success"),
                                "part": n+1, "parts": size, "content": text[n*segment:(n+1)*segment],
                                "possible_retry_success": evidence.get("possible_retry_success")})
        queues[tid] = deque(records)
    # Spread an incomplete first round through the day as well. A small budget
    # must not systematically privilege only the earliest or latest sessions.
    ordered = list(queues)
    ranges = deque([(0, len(ordered))]); order = []
    while ranges:
        lo, hi = ranges.popleft()
        if lo < hi:
            mid = (lo+hi)//2; order.append(ordered[mid])
            ranges.extend(((lo, mid), (mid+1, hi)))
    fair = []
    while any(queues.values()):
        for tid in order:
            q = queues[tid]
            if q:
                fair.append(q.popleft())
    packets, packet, chars = [], [], 0
    limit = config.data["analysis"]["chunk_chars"]
    for record in fair:
        cost = len(json_text(record))
        if packet and (chars+cost > limit or len(packet)>=80 or
                       (record["task_id"] not in {r["task_id"] for r in packet} and len({r["task_id"] for r in packet})>=16)):
            packets.append(packet); packet=[]; chars=0
        packet.append(record); chars+=cost
    if packet:
        packets.append(packet)
    return {"tasks": tasks, "evidence": by_id, "packets": packets, "excluded": excluded,
            "truncated_evidence_ids": sorted(set(truncations)), "history_omitted": context_omitted,
            "history_window_start": earliest, "today_ids": [e["id"] for e in today_rows]}


def packet_input(plan, records, config):
    tids = list(dict.fromkeys(r["task_id"] for r in records))
    direct = {r["id"] for r in records}
    context = []
    # Bound repeated context separately; its time/scope is always explicit.
    budget = config.data["analysis"]["context_chars"]
    context_ids = []
    for tid in tids:
        task = plan["tasks"][tid]
        # Same-day purpose and the latest prior decision keep later tool-heavy
        # chunks interpretable. Their scope remains today, not invented history.
        cutoff = min((r.get("at") or "" for r in records if r["task_id"]==tid), default="")
        prior = [e for e in task["events"] if (e.get("occurred_at") or "") <= cutoff and e["id"] not in direct]
        requests = [e for e in prior if e["kind"] in {"user_message", "note", "review"}]
        if requests:
            context_ids += [requests[0]["id"], requests[-1]["id"]]
        replies = [e for e in prior if e["kind"] == "agent_message"]
        if replies:
            context_ids.append(replies[-1]["id"])
        context_ids += task.get("history_ids", [])
    context_ids += [r["possible_retry_success"] for r in records if r.get("possible_retry_success")]
    used = 0
    omitted = []
    for eid in dict.fromkeys(context_ids):
        if eid in direct or eid not in plan["evidence"]:
            continue
        e = plan["evidence"][eid]
        body = evidence_body(e)
        remaining = budget-used
        if remaining < 200:
            omitted.append(eid); continue
        text = body[:min(2000, remaining)]
        context.append({"id": eid, "task_id": e["task_id"], "scope": e["scope"], "kind": e["kind"],
                        "actor": e["actor"], "at": e.get("occurred_at"), "content": text,
                        "success": e.get("metadata", {}).get("success"), "evidence": e.get("evidence", ""),
                        "truncated": len(text)<len(body)})
        used += len(text)
    return {"tasks": [{"id": tid, "workspace": plan["tasks"][tid]["workspace"],
                       "native_session_ids": plan["tasks"][tid]["session_ids"]} for tid in tids],
            "records": records, "context": context, "context_omitted_ids": omitted}
