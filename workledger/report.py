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
from .llm import summarize

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
    """Extract a result sentence, not a stream preamble. Keep original wording."""
    import re
    text = re.sub(r"```.*?```", "", text, flags=re.S)
    candidates = [re.sub(r"^[#*\-\d. )]+", "", line).strip() for line in text.splitlines() if line.strip()]
    substantive = [line for line in candidates if re.search(r"完成|修复|通过|失败|结论|发现|结果|尚未|未验证|completed|passed|failed|fixed|found",line,re.I)]
    return clip("；".join((substantive or candidates)[:2]),160)


def brief(event: dict) -> str:
    if event["kind"] in {"file_edit", "document_change"}:
        p = Path(event["artifact"]).name
        sections = [d["section"] for d in event["metadata"].get("changes", [])]
        return p + (" · " + "、".join(sections[:4]) if sections else "")
    return short_result(event["text"]) if event["kind"] == "agent_message" else clip(event["text"], 160)


def build_report(config: Config, store: Store, day: str | None = None) -> dict:
    day = day or today(config.data["timezone"])
    start, end = day_bounds(day, config.data["timezone"])
    events = store.events(start, end)
    sessions = store.all_sessions()
    for e in events:
        if e["kind"] in {"user_message", "delegated_instruction"} and e["actor"] != "human":
            child = sessions.get(e.get("session_id"), {}).get("relation") == "delegation"
            e["kind"] = "delegated_instruction" if child else "user_message"
            e["actor"] = "agent" if child else "unknown"
    # Include cross-midnight measured intervals, without assigning all duration to its start day.
    interval_rows = store.conn.execute("SELECT * FROM events WHERE kind='run_interval' AND occurred_at<? AND ended_at>? AND chronology IN ('source_time','live_observed')", (end, start))
    runs = [store._event_dict(r) for r in interval_rows]
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
    projects, facts = [], []
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
        for key in ("personal", "prompts", "agent_results", "agent_files"):
            facts += [{**f, "project": name, "category": key} for f in item[key]]
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
    if config.data["llm"]["mode"] != "off" and facts:
        try:
            output["model_summary"] = summarize(facts, config.data["llm"])
        except Exception as e:
            output["coverage"]["issues"].append("模型摘要不可用，已保留证据摘要：" + clip(str(e), 180))
    return output


CSS = """
:root{color-scheme:light dark;--bg:#f4f5f7;--card:#fff;--ink:#182638;--muted:#617084;--line:#e4e8ee;--accent:#245c86;--soft:#eaf2f8;--warn:#94620e}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.75 -apple-system,BlinkMacSystemFont,'Segoe UI','PingFang SC',sans-serif}main{max-width:1080px;margin:auto;padding:44px 30px 80px}header{margin-bottom:28px}.eyebrow{letter-spacing:2px;font-size:12px;color:var(--accent);font-weight:700}h1{font-size:34px;line-height:1.3;margin:12px 0}h2{font-size:21px;margin:0 0 15px}h3{font-size:13px;color:var(--muted);font-weight:650;margin:0 0 10px}p{margin:7px 0}.muted,small{color:var(--muted)}.stats{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin:24px 0}.stat,.card{background:var(--card);border:1px solid var(--line);border-radius:15px;padding:22px}.stat strong{display:block;font-size:30px;line-height:1.4}.stat span{font-size:12px;color:var(--muted)}.card{margin:16px 0}.columns{display:grid;grid-template-columns:1fr 1fr;gap:24px}.line{margin:12px 0;padding-left:12px;border-left:3px solid var(--line)}a{color:var(--accent);text-decoration:none}a:hover{text-decoration:underline}.pill{display:inline-block;border-radius:6px;background:var(--soft);padding:2px 7px;font-size:11px;margin-right:6px}.notice{border-left:4px solid var(--warn);padding:12px 16px;background:var(--card);border-radius:6px;margin:16px 0}.review{color:var(--warn)}details{margin:12px 0;border-top:1px solid var(--line);padding-top:12px}summary{cursor:pointer;color:var(--accent);font-weight:550}pre{font:12px/1.7 ui-monospace,monospace;white-space:pre-wrap;overflow-wrap:anywhere;max-height:420px;overflow:auto;background:var(--bg);padding:14px;border-radius:8px}.event{padding:16px 0;border-bottom:1px solid var(--line);scroll-margin-top:20px;overflow-wrap:anywhere}.tree{padding-left:18px;border-left:1px solid var(--line);margin:8px 0}.actions{display:flex;gap:10px;flex-wrap:wrap;margin:14px 0}button,.button{font:inherit;border:1px solid var(--line);border-radius:8px;padding:8px 16px;background:var(--card);color:var(--accent);cursor:pointer}footer{margin-top:28px;color:var(--muted);font-size:12px}@media(max-width:680px){main{padding:24px 16px 50px}h1{font-size:28px}.stats{grid-template-columns:1fr 1fr}.columns{grid-template-columns:1fr;gap:14px}.card{padding:18px}}@media(prefers-color-scheme:dark){:root{--bg:#131c26;--card:#1b2734;--ink:#e2e9f2;--muted:#9baec0;--line:#304154;--accent:#8cc8f2;--soft:#263e52;--warn:#efbd67}}@media print{body{background:white}.actions,details,footer{display:none}main{padding:10px}.card,.stat{break-inside:avoid}h1{font-size:26px}}
"""


def render_html(report: dict) -> str:
    esc = lambda s: html.escape(str(s or ""), quote=True)
    def line(card, prefix=""):
        return f'<p class="line">{esc(prefix)}{esc(card["text"])} <a href="#e-{card["id"]}" onclick="document.getElementById(\'evidence\').open=true">↗</a></p>'
    stats = report["stats"]
    cards = "".join(f'<div class="stat"><strong>{stats[key]}</strong><span>{label}</span></div>' for key, label in [("human_confirmed", "本人已确认记录"), ("user_channel_messages", "用户侧指令 · 非打字证明"), ("agent_files", "Agent 写入文件"), ("unattributed_changes", "待确认文档变化")])
    body = ""
    if report.get("demo"):
        body += '<div class="notice"><b>演示数据</b> · 此页用于展示报告样式，不是你的真实工作记录。</div>'
    if report.get("model_summary"):
        model = report["model_summary"]
        body += f'<section class="card"><h2>{esc(model.get("headline"))}</h2><small>模型整理 · 下方原始证据保留，引用匹配不等于结论已经独立验证</small>'
        body += "".join(f'<p class="line">{esc(i["text"])}</p>' for i in model["items"]) + '</section>'
    if not report["projects"]:
        body += '<section class="card"><h2>尚无工作记录</h2><p>选择日志路径和项目目录后开始采集。旧网页内容、没有时间的消息和首次文件基线不会被当作今天的新工作。</p></section>'
    for p in report["projects"]:
        body += f'<section class="card"><h2>{esc(p["name"])}</h2><div class="columns"><div><h3>本人工作与用户侧指令</h3>'
        body += ''.join(line(v, "本人确认 · ") for v in p["personal"]) or '<p class="muted">尚无本人确认的操作。</p>'
        body += ''.join(line(v, "指令 · ") for v in p["prompts"])
        body += '</div><div><h3>Agent 产出</h3>'
        body += ''.join(line(v, "Agent 报告 · ") for v in p["agent_results"]) or '<p class="muted">没有捕获到当天的结果回复。</p>'
        body += ''.join(line(v, "写入 · ") for v in p["agent_files"])
        body += '</div></div>'
        if p["needs_review"]:
            body += f'<p class="review">{p["counts"]["needs_review"]} 项文档变化归属待确认，未计入本人工作。</p>'
        body += '</section>'
    if report["failures"]:
        body += '<section class="card"><h2>需要留意</h2><p class="muted">工具失败记录不一定代表任务最终失败。</p>' + ''.join(line(v) for v in report["failures"]) + '</section>'
    problems = report["coverage"]["issues"]
    body += '<section class="card"><h2>采集情况</h2>'
    body += f'<p>{len(problems)} 项采集提示 · {stats["undated_excluded"]} 条无可靠时间的记录未计入今日成果。</p>'
    if stats["observed_wall_seconds"]:
        body += f'<p class="muted">记录覆盖的 Agent 执行区间：并集 {round(stats["observed_wall_seconds"]/60,1)} 分钟；按会话求和 {round(stats["session_interval_sum_seconds"]/60,1)} 分钟。仅统计有起止记录的区间，包含等待，不是 CPU 工时或本人工作时长。</p>'
    if problems:
        body += '<details><summary>查看采集提示</summary>' + ''.join(f'<p>{esc(p)}</p>' for p in problems) + '</details>'
    body += '<details><summary>主会话与子会话</summary>'
    touched = {e["session_id"] for e in report["events"] if e.get("session_id")}
    sessions = report["sessions"]
    needed = set(touched)
    for sid in list(touched):
        cur, visited = sid, set()
        while cur in sessions and cur not in visited:
            visited.add(cur)
            needed.add(cur)
            cur = sessions[cur].get("parent_id")
    def tree(sid, visited, depth=0):
        if sid in visited or depth > 20:
            return '<p>循环或深度上限，关系未展开。</p>'
        s = sessions.get(sid, {"id": sid, "title": "元数据缺失", "relation": "unknown"})
        children = [k for k in needed if sessions.get(k, {}).get("parent_id") == sid]
        title = s.get("title") or sid
        result = f'<div class="tree"><span class="pill">{esc(s.get("relation"))}</span>{esc(clip(title,100))}<small> · {esc(sid)}</small>'
        for child in sorted(children):
            result += tree(child, visited | {sid}, depth+1)
        return result + '</div>'
    tops = [sid for sid in needed if sessions.get(sid, {}).get("parent_id") not in needed]
    if not tops and needed:
        tops = [sorted(needed)[0]]
    body += ''.join(tree(s, set()) for s in sorted(tops))
    body += '</details><details id="evidence"><summary>展开原始证据与文档差异</summary>'
    for e in report["events"]:
        if e["kind"] in {"tool_call", "tool_interval", "context"}:
            continue
        text = e["text"]
        body += f'<div class="event" id="e-{e["id"]}"><span class="pill">{esc(LABELS[e["actor"]])}</span><b>{esc(KIND_LABELS.get(e["kind"],e["kind"]))}</b><small> · {esc(e["source"])} · {esc(e["occurred_at"])}</small><p>{esc(text)}</p>'
        if e["artifact"]:
            body += f'<small>{esc(e["artifact"])}</small>'
        if e["metadata"].get("observed_not_edit_time"):
            body += '<p class="muted">时间为观察到保存结果的时刻；实际修改发生在两次快照之间，不能据此判断输入者。</p>'
        if e.get("attribution_override"):
            body += f'<p class="muted">归属确认：{esc(e["attribution_override"]["reason"])}</p>'
        for d in e["metadata"].get("changes", []):
            body += f'<details><summary>{esc(d["section"])}</summary><pre>{esc(d.get("diff", ""))}</pre></details>'
        body += f'<small>证据 {esc(e["evidence"])} · ID {e["id"]}</small></div>'
    body += '</details></section>'
    return f'<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>{esc(report["date"])} · WorkLedger</title><style>{CSS}</style></head><body><main><header><div class="eyebrow">WORKLEDGER / DAILY BRIEF</div><h1>{esc(report["date"])} 工作简报</h1><p class="muted">{esc(report["headline"])} · {esc(report["timezone"])}</p><div class="actions"><button onclick="window.print()">打印 / 保存 PDF</button><a class="button" href="report.md">Markdown</a></div></header><div class="stats">{cards}</div>{body}<footer>本机生成 · 只把带有可靠时间的消息算入当天。Agent 的回复和成功写入日志，与本人确认的工作分开呈现。<br>生成于 {esc(report["generated_at"])} · 私有工作记录，请勿将 reports 目录加入代码仓库。</footer></main></body></html>'


def render_markdown(r: dict) -> str:
    lines = [f'# {r["date"]} 工作简报', '', r["headline"], '']
    if r.get("demo"):
        lines += ['> 演示数据，不是真实工作记录。', '']
    for p in r["projects"]:
        lines += ['## ' + p["name"], '']
        for key, title in [("personal", "本人确认"), ("prompts", "用户侧指令"), ("agent_results", "Agent 报告"), ("agent_files", "Agent 写入")]:
            for item in p[key]:
                lines.append(f'- **{title}**：{item["text"]}')
        if p["needs_review"]:
            lines.append(f'- 待确认文档变化：{p["counts"]["needs_review"]} 项，未计入本人工作。')
        lines.append('')
    lines += ['## 采集情况', '', f'{len(r["coverage"]["issues"])} 项采集提示。完整证据、差异和会话树见 report.html。', '']
    return '\n'.join(lines)


def write_report(config: Config, store: Store, day=None) -> Path:
    r = build_report(config, store, day)
    dest = config.reports / r["date"]
    atomic_write(dest / "report.json", json.dumps(r, ensure_ascii=False, indent=2))
    atomic_write(dest / "report.md", render_markdown(r))
    atomic_write(dest / "report.html", render_html(r))
    atomic_write(config.reports / "latest.json", json.dumps({"date": r["date"], "path": str(dest / "report.html")}))
    return dest / "report.html"
