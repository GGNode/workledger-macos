from __future__ import annotations

import html
import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from .config import Config
from .store import Store
from .util import atomic_write, clip, day_bounds, digest, intervals_seconds, now, today, under
from .analysis.pipeline import analyze
from .analysis.evidence import exclusions

LABELS = {"human": "本人确认", "agent": "Agent", "unknown": "归属待确认", "system": "系统"}
KIND_LABELS = {"user_message": "用户侧指令", "agent_message": "Agent 回复", "delegated_instruction": "派生指令", "file_edit": "Agent 写入记录", "document_change": "观察到文档变化", "run_interval": "执行区间", "tool_call": "工具调用", "tool_result": "工具结果", "tool_interval": "工具区间", "context": "上下文", "note": "本人补充", "browser_message": "网页消息", "review": "审阅"}


def root_session(sid: str, sessions: dict) -> tuple[str, str | None]:
    seen = set()
    current = sid
    while current in sessions:
        if current in seen:
            return sid, "会话关系包含循环，未自动合并"
        seen.add(current)
        session = sessions[current]
        parent = session.get("parent_id")
        if not parent or session.get("relation") != "delegation":
            return current, None
        if parent not in sessions:
            return current, "父会话日志未采集"
        current = parent
    return sid, "会话元数据未采集"


def project_for(event: dict, sessions: dict, config: Config) -> str:
    session = sessions.get(event.get("session_id"), {})
    for project in config.data["projects"]:
        for root in project.get("paths", []):
            for path in (event.get("artifact"), session.get("cwd")):
                if path and under(Path(path), Path(root).expanduser()):
                    return project["name"]
    return event.get("metadata", {}).get("project") or "其他工作"


def short_result(text: str) -> str:
    """Extract final result sentences, not stream preamble. Keep original wording."""
    import re
    had_code = "```" in (text or "")
    text = re.sub(r"```.*?```", "", text or "", flags=re.S)
    # Strip list/heading markers only; a leading date (2026-10-06 ...) is content.
    candidates = [re.sub(r"^(\s*(#{1,6}\s+|[-*]\s+|\d+[.)]\s+))+", "", line).strip() for line in text.splitlines() if line.strip()]
    substantive = [line for line in candidates if re.search(r"完成|修复|通过|失败|结论|发现|结果|尚未|未验证|总结|成功|已更新|completed|passed|failed|fixed|found|done|updated|success|summar|verif|implement|added",line,re.I)]
    # Prefer trailing substantive lines: the final result, not the preamble.
    picked = (substantive or candidates)[-2:]
    out = "；".join(picked)
    if had_code and not substantive and out:
        out += "（含代码块，见证据）"
    return clip(out, 160)


def brief(event: dict) -> str:
    if event["kind"] in {"file_edit", "document_change"}:
        p = Path(event["artifact"]).name if event["artifact"] else ""
        # Never render a blank card: fall back to the recorded text excerpt.
        if not p:
            return clip(event["text"], 160) or event["kind"]
        sections = [d["section"] for d in event["metadata"].get("changes", [])]
        return p + (" · " + "、".join(sections[:4]) if sections else "")
    return short_result(event["text"]) if event["kind"] == "agent_message" else clip(event["text"], 160)


def build_report(config: Config, store: Store, day: str | None = None, *, refresh_analysis=False, analysis_client=None) -> dict:
    day = day or today(config.data["timezone"])
    start, end = day_bounds(day, config.data["timezone"])
    events = [e for e in store.events(start, end) if not e["metadata"].get("exclude_from_brief")]
    sessions = store.all_sessions()
    excluded_ids = exclusions(config, events, sessions)
    excluded_events = [e for e in events if e["id"] in excluded_ids]
    events = [e for e in events if e["id"] not in excluded_ids]
    for e in events:
        if e["kind"] in {"user_message", "delegated_instruction"} and e["actor"] != "human":
            child = sessions.get(e.get("session_id"), {}).get("relation") == "delegation"
            e["kind"] = "delegated_instruction" if child else "user_message"
            e["actor"] = "agent" if child else "unknown"
    # Include cross-midnight measured intervals, without assigning all duration to its start day.
    interval_rows = store.conn.execute("SELECT * FROM events WHERE kind='run_interval' AND occurred_at<? AND ended_at>? AND chronology IN ('source_time','live_observed')", (end, start))
    runs = [store._event_dict(r) for r in interval_rows]
    run_exclusions = exclusions(config, runs, sessions)
    runs = [e for e in runs if e["id"] not in run_exclusions]
    for r in runs:
        if r["id"] not in {e["id"] for e in events}:
            events.append(r)
    # Identical copied messages in a fork remain in storage, but don't multiply daily work.
    events.sort(key=lambda e: (sessions.get(e.get("session_id"), {}).get("relation") == "fork", e["occurred_at"] or "", e["id"]))
    seen_messages, filtered = set(), []
    for e in events:
        if e["kind"] in {"user_message", "agent_message"}:
            signature = digest([e["source"], e["occurred_at"], e["text"]])
            if signature in seen_messages and sessions.get(e.get("session_id"), {}).get("relation") in {"fork", "lineage"}:
                continue
            seen_messages.add(signature)
        filtered.append(e)
    events = filtered
    change_seen, unique_events = set(), []
    for e in events:
        if e["kind"] == "document_change" and e["metadata"].get("after_hash"):
            signature = (e["artifact"], e["metadata"]["after_hash"], e["actor"])
            if signature in change_seen:
                continue
            change_seen.add(signature)
        unique_events.append(e)
    events = unique_events
    issues = [d["source"] + "：" + d["detail"] for d in store.summary()["issues"]]
    groups = defaultdict(list)
    for e in events:
        if e["kind"] not in {"context", "tool_call", "tool_result", "tool_interval", "run_interval"}:
            groups[project_for(e, sessions, config)].append(e)
    projects = []
    for name, group in groups.items():
        personal = [e for e in group if e["actor"] == "human"]
        prompts = [e for e in group if e["kind"] == "user_message" and e["actor"] != "human"]
        edits = [e for e in group if e["kind"] == "file_edit" and e["actor"] == "agent"]
        unknown = [e for e in group if e["kind"] in {"document_change", "file_edit"} and e["actor"] == "unknown"]
        # One latest substantive response per ROOT task; never dump every subagent narration.
        latest = {}
        for e in group:
            if e["kind"] != "agent_message":
                continue
            root, issue = root_session(e.get("session_id", ""), sessions)
            if issue and issue not in issues:
                issues.append(issue)
            previous = latest.get(root)
            # Prefer root responses over child reports when the root reported today.
            is_root = e.get("session_id") == root
            old_root = previous and previous.get("session_id") == root
            if not previous or (is_root and not old_root) or (is_root == old_root and e["occurred_at"] >= previous["occurred_at"]):
                latest[root] = e
        results = sorted(latest.values(), key=lambda e: e["occurred_at"], reverse=True)
        def cards(items, maximum=4):
            dedup, out = set(), []
            for e in reversed(items) if items is not results else items:
                key = e["artifact"] or e["text"]
                if key in dedup:
                    continue
                dedup.add(key)
                out.append({"id": e["id"], "text": brief(e), "source": e["source"], "session_id": e.get("session_id"), "actor": e["actor"], "kind": e["kind"]})
                if len(out) >= maximum:
                    break
            return out
        item = {"name": name, "personal": cards(personal), "prompts": cards(prompts, 2), "agent_results": cards(results, 3), "agent_files": cards(edits, 4), "needs_review": cards(unknown, 3),
                "counts": {"personal": len(personal), "prompts": len(prompts), "agent_files": len({e['artifact'] for e in edits}), "needs_review": len(unknown)}}
        projects.append(item)
    touched = {e["session_id"] for e in events if e.get("session_id")}
    roots = {root_session(s, sessions)[0] for s in touched}
    descendants = {s for s in touched if sessions.get(s, {}).get("relation") == "delegation"}
    intervals = [(e["occurred_at"], e["ended_at"]) for e in runs]
    wall = intervals_seconds(intervals, start, end)
    per_session = defaultdict(list)
    for e in runs:
        per_session[e.get("session_id")].append((e["occurred_at"], e["ended_at"]))
    summed = sum(intervals_seconds(v, start, end) for v in per_session.values())
    failed = [e for e in events if e["kind"] == "tool_result" and e["metadata"].get("success") is False]
    notes = [e for e in events if e["kind"] == "note" and e["actor"] == "human"]
    undated = store.conn.execute("SELECT count(*) FROM events WHERE occurred_at IS NULL AND observed_at>=? AND observed_at<?", (start, end)).fetchone()[0]
    output = {
        "schema": "workledger.report.v1", "date": day, "timezone": config.data["timezone"], "generated_at": now(),
        "title": "今日工作简报", "demo": bool(store.cache_get("demo", False)),
        "headline": f"{len(projects)} 个工作主题 · {len(roots)} 组会话 · {len(descendants)} 个已识别子会话" if projects else "今天尚无可归入日报的记录",
        "stats": {"human_confirmed": sum(e["actor"] == "human" for e in events), "user_channel_messages": sum(e["kind"] == "user_message" and e["actor"] != "human" for e in events),
                  "agent_files": len({e["artifact"] for e in events if e["kind"] == "file_edit" and e["actor"] == "agent"}),
                  "unattributed_changes": sum(e["kind"] in {"document_change", "file_edit"} and e["actor"] == "unknown" for e in events),
                  "sessions": len(touched), "root_tasks": len(roots), "subsessions": len(descendants), "observed_wall_seconds": wall, "session_interval_sum_seconds": summed,
                  "undated_excluded": undated, "failed_tool_results": len(failed)},
        "projects": projects, "notes": [{"id": e["id"], "text": e["text"]} for e in notes],
        "failures": [{"id": e["id"], "text": clip(e["text"]), "session_id": e["session_id"]} for e in failed[:5]],
        "coverage": {"last_capture": store.cache_get("last_capture"), "issues": issues, "paused": config.data["capture_paused"]},
        "events": sorted(events, key=lambda e: (e["occurred_at"] or "", e["id"])),
        "sessions": sessions, "model_summary": None,
    }
    # Legacy cards remain in JSON only for compatibility, never model input or main UI.
    output["schema"] = "workledger.report.v2"
    output["analysis"] = analyze(config, store, output["events"], sessions, day,
                                 refresh=refresh_analysis, client=analysis_client)
    output["analysis"]["coverage"]["excluded"].update(excluded_ids)
    output["excluded_events"] = [{"id": e["id"], "source": e["source"], "session_id": e.get("session_id"), "reason": excluded_ids[e["id"]]} for e in excluded_events]
    output["legacy_headline"] = output["headline"]
    a = output["analysis"]
    output["headline"] = (a["highlights"][0]["text"] if a["highlights"] else
                          f"{len(a['themes'])} 项工作 · " + ("观察摘要，尚未完成语义分析" if a["status"] in {"disabled", "degraded"} else "查看进展与验证边界"))
    # A compatibility alias, never a separately generated model response.
    output["model_summary"] = ({"deprecated": True, "canonical_path": "analysis", "status": a["status"],
                                 "highlights": a["highlights"]} if a["backend"] != "off" else None)
    return output



def render_html(report: dict) -> str:
    from .analysis.render import render_html as render
    return render(report)


def render_markdown(report: dict) -> str:
    from .analysis.render import render_markdown as render
    return render(report)


def write_report(config: Config, store: Store, day=None, *, refresh_analysis=False, analysis_client=None) -> Path:
    from .analysis.publish import publish
    r = build_report(config, store, day, refresh_analysis=refresh_analysis, analysis_client=analysis_client)
    return publish(config, r)
