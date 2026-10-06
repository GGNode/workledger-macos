"""One narrative contract for HTML and Markdown. No model calls in this module."""
from __future__ import annotations

import html
import json
import re
from collections import Counter

from .backend import MESSAGES
from .schema import BASIS_LABELS, SECTIONS

STATUS = {"complete": "语义分析已完成", "partial": "部分分析 · 有覆盖缺口", "disabled": "语义分析未启用",
          "degraded": "模型不可用 · 观察模式", "empty": "暂无可归日证据"}
HEADINGS = {"work": "今天做了什么", "results": "进展与影响", "remaining": "未完成与待确认", "suggestions": "建议 · 尚未执行"}
CSS = """
:root{color-scheme:light;--bg:#f5f6f3;--paper:#fff;--ink:#1b2d33;--muted:#657575;--line:#e1e7e2;--accent:#17645a;--soft:#eaf3ef;--warn:#895c19;--warn-bg:#faf3e6;--blue:#355f85}
*{box-sizing:border-box}html{scroll-behavior:smooth}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.85 -apple-system,BlinkMacSystemFont,'Segoe UI','PingFang SC','Microsoft YaHei',sans-serif}a{color:var(--accent);text-underline-offset:3px}a:focus-visible,summary:focus-visible,button:focus-visible{outline:3px solid var(--accent);outline-offset:5px}main{max-width:1160px;margin:auto;padding:30px 36px 72px}.topbar{display:flex;justify-content:space-between;align-items:center;gap:16px;margin-bottom:40px}.brand{font-size:16px;font-weight:750;letter-spacing:.2px;display:flex;align-items:center;gap:10px}.mark{background:var(--accent);color:#fff;width:33px;height:33px;border-radius:10px;display:grid;place-items:center;font-size:13px}.export{display:flex;gap:16px;font-size:12px;align-items:center}.export button{font:inherit;background:none;color:var(--muted);border:0;cursor:pointer}.eyebrow{font-size:12px;letter-spacing:2px;color:var(--muted);text-transform:uppercase}h1{font-size:38px;letter-spacing:-1px;line-height:1.3;margin:8px 0 12px}h2{font-size:23px;line-height:1.45;margin:0 0 16px}h3{font-size:13px;line-height:1.5;color:var(--accent);margin:0 0 10px;font-weight:650}h4{font-size:14px;margin:0 0 8px}p{margin:0 0 12px}.subhead{color:var(--muted);font-size:14px}.status{font-size:12px;padding:5px 11px;border:1px solid var(--line);border-radius:20px;display:inline-block;color:var(--accent);background:var(--paper)}.status.partial,.status.degraded,.status.disabled{color:var(--warn);background:var(--warn-bg);border-color:#eadbc0}.intro{margin-bottom:30px}.hero{background:var(--accent);color:#fff;border-radius:20px;padding:28px 32px;margin:24px 0 32px}.hero h2{font-size:14px;letter-spacing:1px;color:#d6ece3;margin-bottom:17px}.hero p{font-size:18px;line-height:1.8}.hero .basis{color:#dbebe3;border-color:#6b9888}.hero a{color:#dcf1e6}.hero p:last-child{margin-bottom:0}.layout{display:grid;grid-template-columns:minmax(0,1fr) 215px;gap:26px;align-items:start}.content{min-width:0}.topic{background:var(--paper);border:1px solid var(--line);border-radius:18px;padding:28px 30px;margin-bottom:22px;scroll-margin-top:20px}.topic-title{display:flex;gap:16px;align-items:baseline;justify-content:space-between}.number{color:#9caeaa;font-size:12px;letter-spacing:1px}.sections{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:24px;margin-top:22px}.section{min-width:0}.section.full{grid-column:1/-1}.claim{margin-bottom:13px;overflow-wrap:anywhere}.claim p{margin:0;display:inline}.basis{display:inline-block;font-size:10px;line-height:1.8;color:var(--muted);border:1px solid var(--line);border-radius:4px;padding:0 5px;margin-right:6px;vertical-align:1px}.refs{font-size:11px;white-space:nowrap;margin-left:5px;text-decoration:none}.remaining{padding:17px 19px;border-radius:12px;background:#f7f8f5}.suggestion{border-top:1px dashed var(--line);padding-top:18px;margin-top:8px}.issue{background:var(--warn-bg);border-radius:11px;padding:17px 19px;margin:14px 0}.issue h3{color:var(--warn)}.issue .claim:last-child{margin:0}.aside{position:sticky;top:24px;color:var(--muted);font-size:12px}.aside nav a{display:block;text-decoration:none;padding:7px 0;border-bottom:1px solid var(--line);overflow-wrap:anywhere}.aside h3{color:var(--muted);font-size:11px;letter-spacing:1px}.aside p{margin-top:20px;font-size:12px}.notice{padding:18px 22px;margin:20px 0;background:var(--warn-bg);border:1px solid #eadbc0;border-radius:13px;color:#695629;font-size:14px}.notice p:last-child{margin:0}.evidence-area{margin-top:25px;border-top:1px solid var(--line);padding-top:24px}details{margin:12px 0}summary{cursor:pointer;color:var(--accent);font-size:13px;padding:7px 0}details[open]>summary{margin-bottom:14px}.evidence-event{margin:12px 0;background:var(--paper);padding:18px 20px;border:1px solid var(--line);border-radius:12px;scroll-margin-top:15px;overflow-wrap:anywhere}.evidence-event:target{outline:2px solid var(--accent)}pre{font:12px/1.7 ui-monospace,SFMono-Regular,monospace;white-space:pre-wrap;overflow-wrap:anywhere;max-height:480px;overflow:auto;background:var(--bg);padding:15px;border-radius:8px}small,.muted{color:var(--muted);font-size:12px}.tree{padding-left:14px;border-left:1px solid var(--line);margin:8px 0;overflow-wrap:anywhere}.coverage-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px}.coverage-grid div{background:var(--paper);padding:15px;border-radius:10px;font-size:13px}.coverage-grid strong{display:block;color:var(--ink);font-size:20px}.audit{font-size:12px;color:var(--muted)}footer{margin-top:34px;font-size:11px;color:var(--muted);border-top:1px solid var(--line);padding-top:20px}.demo{background:#fff4cf;border:1px solid #e5cd87}.observation h2{font-size:19px;color:var(--muted)}.observation .number{display:none}
@media(max-width:780px){main{padding:20px 17px 45px}.topbar{margin-bottom:26px}.export{gap:10px}h1{font-size:29px}.hero{padding:22px}.hero p{font-size:16px}.layout{display:block}.aside{position:static;margin-top:24px}.aside nav{display:none}.topic{padding:22px 20px}.sections{grid-template-columns:1fr;gap:18px}.coverage-grid{grid-template-columns:1fr}.topic-title h2{font-size:21px}}
@media print{body{background:#fff}main{max-width:none;padding:0}.export,.aside,.evidence-area{display:none}.layout{display:block}.topic{break-inside:avoid}.hero{background:#fff;color:#1b2d33;border:1px solid #aaa}.hero h2,.hero a,.hero .basis{color:#17645a}.topic,.hero{box-shadow:none}a{text-decoration:none}h1{font-size:26px}.notice{break-inside:avoid}}
@media(prefers-reduced-motion:reduce){html{scroll-behavior:auto}}
"""
SCRIPT = """document.addEventListener('click',event=>{const a=event.target.closest('a[data-evidence]');if(!a)return;const ids=a.dataset.evidence.split(',');let first;for(const id of ids){const el=document.getElementById('e-'+id);if(!el)continue;if(!first)first=el;let p=el.parentElement;while(p){if(p.tagName==='DETAILS')p.open=true;p=p.parentElement;}}if(first){event.preventDefault();history.replaceState(null,'','#'+first.id);first.scrollIntoView({block:'start'});first.setAttribute('tabindex','-1');first.focus({preventScroll:true});}});document.getElementById('print-report').addEventListener('click',()=>window.print());function revealHash(){const el=document.getElementById(location.hash.slice(1));if(!el)return;let p=el.parentElement;while(p){if(p.tagName==='DETAILS')p.open=true;p=p.parentElement;}el.scrollIntoView();}window.addEventListener('hashchange',revealHash);if(location.hash)revealHash();"""


def esc(value):
    return html.escape(str(value or ""), quote=True)


def references(c):
    refs = c["evidence_ids"]
    return f'<a class="refs" href="#e-{esc(refs[0])}" data-evidence="{esc(",".join(refs))}" aria-label="展开{len(refs)}条证据">依据 {len(refs)} ↗</a>' if refs else ""


def show_claim(c):
    label = BASIS_LABELS[c["basis"]] + (" · 历史背景" if c["scope"] == "history" else "")
    return f'<div class="claim"><span class="basis">{esc(label)}</span><p>{esc(c["text"])}</p>{references(c)}</div>'


def main_issues(theme):
    return [p for p in theme["issues"] if p["state"] != "resolved" and p["severity"] in {"material", "blocking"}]


def _status_message(a):
    if a["status"] == "disabled":
        return "当前没有调用模型。下面只说明哪些记录已有执行依据、哪些仍不能确认，不将日志片段冒充工作分析。可在控制面板选择 OpenCode 或其他分析后端。"
    if a["status"] == "empty":
        return "当前没有可靠时间且可纳入这一天的工作证据。这不代表没有工作；历史消息、探测和报告自身活动不会被计作当天成果。"
    if a["status"] == "degraded":
        first = next((w["code"] for w in a["warnings"] if w["code"] in MESSAGES), "provider")
        return MESSAGES[first] + " 下面是明确标记的观察摘要，不是模型分析。"
    if a["status"] == "partial":
        c = a["coverage"]
        return f'已完整分析 {c["evidence_analyzed"]}/{c["today_evidence"]} 条可用证据。部分分块、主题归并或全天综合未完成；已生成的分析与未分析记录分开保留。'
    return ""


def status_message(a):
    notices = [w["detail"] for w in a["warnings"] if w["code"] == "capture_pending"]
    return _status_message(a) + (" " + " ".join(notices) if notices else "")


def evidence_html(r, a):
    out = '<div class="evidence-area" id="audit"><h2>依据与覆盖情况</h2><p class="audit">归属标记只说明证据来源。缺少本人确认，不代表本人没有工作。原始记录默认折叠，不参与主页面叙述。</p>'
    c = a["coverage"]
    out += '<details><summary>分析覆盖、归属统计与采集状态</summary><div class="coverage-grid">'
    for label, value in (("完整分析的证据", f'{c["evidence_analyzed"]} / {c["today_evidence"]}'),
                         ("完整覆盖的原生任务", f'{c["tasks_analyzed"]} / {c["tasks_total"]}'),
                         ("已处理分块", f'{c["packets_analyzed"]} / {c["packets_total"]}')):
        out += f'<div><strong>{esc(value)}</strong>{esc(label)}</div>'
    out += '</div>'
    out += f'<p class="audit">后端调用尝试 {a["transport"]["calls"]} 次，缓存命中 {a["transport"]["cache_hits"]} 次。读取报告不会重新请求模型。</p>'
    out += f'<p class="audit">历史上下文起点 {esc(c["history_window_start"])}；历史条数上限省略 {c["history_omitted"]} 条。上下文摘录不完整的包 {c["context_truncated_packets"]} 个，未纳入全部上下文的包 {c["context_omitted_packets"]} 个。源记录已截断或旧版只保留片段的证据 {len(c["source_truncated_ids"])} 条。</p>'
    if c["excluded"]:
        out += '<p class="audit">排除的报告自身活动及探测：'+esc(dict(Counter(c["excluded"].values())))+'</p>'
    out += '<details><summary>详细覆盖清单和分析诊断</summary><pre>'+esc(json.dumps({"coverage": c, "analysis_warnings": a["warnings"], "attribution_counts": r["stats"]}, ensure_ascii=False, indent=2))+'</pre></details>'
    out += '<h3>采集器状态 · 与工作任务问题分开</h3>'
    for value in r["coverage"]["issues"]:
        out += '<p class="audit">'+esc(value)+'</p>'
    if not r["coverage"]["issues"]:
        out += '<p class="audit">当前没有活跃采集错误。此状态不保证所有软件和工作目录均已配置。</p>'
    out += f'<p class="audit">无可靠源时间的记录 {r["stats"]["undated_excluded"]} 条未纳入当天成果。</p></details>'
    out += '<details id="evidence"><summary>展开原始消息、工具结果、文档变化与引用依据</summary>'
    all_evidence = {e["id"]: e for e in r["events"]}
    all_evidence.update(a.get("evidence", {}))
    for eid, e in all_evidence.items():
        out += f'<article class="evidence-event" id="e-{esc(eid)}"><b>{esc(e["source"])} · {esc(e["kind"])}</b><br><small>{esc(e.get("scope", "today"))} · {esc(e.get("occurred_at"))} · actor={esc(e["actor"])}</small><pre>{esc(e.get("text"))}</pre>'
        if e.get("artifact"):
            out += '<p class="audit">'+esc(e["artifact"])+'</p>'
        if e.get("attribution_override"):
            out += '<p class="audit">归属确认：'+esc(e["attribution_override"].get("reason"))+'</p>'
        out += '<details><summary>工具完整保留内容、文档差异与来源元数据</summary><pre>'+esc(json.dumps(e.get("metadata", {}), ensure_ascii=False, indent=2))+'</pre></details>'
        out += f'<small>Evidence ID: {esc(eid)} · {esc(e.get("evidence"))}</small></article>'
    out += '</details><details><summary>原生主任务、子任务和分支关系</summary><p class="audit">主题的语义归并不会改变以下原生关系；fork/lineage 不表示子 Agent 委派。</p>'
    sessions = r["sessions"]
    needed = {e.get("session_id") for e in all_evidence.values() if e.get("session_id")}
    for sid in list(needed):
        cur, seen = sid, set()
        while cur in sessions and cur not in seen:
            seen.add(cur); needed.add(cur); cur=sessions[cur].get("parent_id")
    def tree(sid, seen):
        if sid in seen or len(seen) > 30:
            return '<p>关系循环或过深，停止展开。</p>'
        row = sessions.get(sid, {})
        text = f'<div class="tree"><small>{esc(row.get("relation", "unknown"))} · {esc(sid)}</small><br>{esc(row.get("title", ""))}'
        for child in sorted(s for s in needed if sessions.get(s, {}).get("parent_id") == sid):
            text += tree(child, seen | {sid})
        return text+'</div>'
    tops = [sid for sid in needed if sessions.get(sid, {}).get("parent_id") not in needed]
    for sid in sorted(tops or list(needed)[:1]):
        out += tree(sid, set())
    out += '</details></div>'
    return out


def render_html(r):
    a = r["analysis"]
    out = '<main><div class="topbar"><div class="brand"><span class="mark">Wl</span>WorkLedger</div><div class="export"><a href="report.md">Markdown</a><a href="report.json">JSON</a><button id="print-report">打印</button></div></div>'
    out += f'<header class="intro"><div class="eyebrow">{esc(r["date"])} / DAILY REVIEW</div><h1>今天，工作推进到了哪里</h1><p class="subhead">关注实际进展、重要决定，以及仍未解决的事。</p><span class="status {esc(a["status"])}">{esc(STATUS[a["status"]])}</span></header>'
    if r.get("demo"):
        out += '<div class="notice demo">'+esc(r.get("demo_notice", "合成演示数据，不是真实个人工作记录。"))+'</div>'
    notice = status_message(a)
    if notice:
        out += '<div class="notice"><p>'+esc(notice)+'</p></div>'
    if r.get("previous_success"):
        out += '<p class="audit">'+esc(r["previous_success"]["label"])+' <a href="'+esc(r["previous_success"]["url"])+'">打开保留版本 ↗</a></p>'
    if a["highlights"]:
        out += '<section class="hero" aria-label="今日重点"><h2>今日重点</h2>'+''.join(show_claim(c) for c in a["highlights"])+'</section>'
    out += '<div class="layout"><div class="content" id="main-narrative">'
    for n, theme in enumerate(a["themes"], 1):
        cls = ' observation' if theme.get("analysis_status") == "observation_only" else ''
        out += f'<article class="topic{cls}" id="topic-{esc(theme["id"])}"><div class="topic-title"><h2>{esc(theme["title"])}</h2><span class="number">{n:02}</span></div><div class="sections">'
        for section in ("work", "results"):
            if theme[section]:
                out += '<section class="section"><h3>'+HEADINGS[section]+'</h3>'+''.join(show_claim(c) for c in theme[section])+'</section>'
        if theme["remaining"]:
            out += '<section class="section full remaining"><h3>'+HEADINGS["remaining"]+'</h3>'+''.join(show_claim(c) for c in theme["remaining"])+'</section>'
        for issue in main_issues(theme):
            title = "当前阻塞" if issue["state"] == "open" and issue["severity"] == "blocking" else "影响尚待确认" if issue["state"] == "uncertain" else "仍需处理的问题"
            out += '<section class="section full issue"><h3>'+title+'</h3>'+show_claim(issue["problem"])+show_claim(issue["impact"])
            if issue.get("resolution"):
                out += show_claim(issue["resolution"])
            out += '</section>'
        if theme["suggestions"]:
            out += '<section class="section full suggestion"><h3>'+HEADINGS["suggestions"]+'</h3>'+''.join(show_claim(c) for c in theme["suggestions"])+'</section>'
        out += '</div>'
        secondary = [p for p in theme["issues"] if p not in main_issues(theme)]
        if secondary:
            out += '<details><summary>已恢复或例行问题的处理依据</summary>'
            for p in secondary:
                out += show_claim(p["problem"])
                if p.get("resolution"):
                    out += show_claim(p["resolution"])
            out += '</details>'
        out += '</article>'
    out += '</div><aside class="aside"><h3>工作主题</h3><nav>'
    for t in a["themes"]:
        out += f'<a href="#topic-{esc(t["id"])}">{esc(t["title"])}</a>'
    out += '<a href="#audit">依据与覆盖情况</a></nav><p>'+esc(a["transport"]["data_flow"])+'.</p><p>阅读已有报告不会触发新的模型调用。事实、Agent 自述和建议各自标注。</p></aside></div>'
    out += evidence_html(r, a)
    out += '<footer>'+esc(r["timezone"])+' · 生成于 '+esc(r["generated_at"])+'<br>主题分类来自已采集证据；未监测的工作不在本报告的覆盖范围内。</footer></main>'
    return '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="referrer" content="no-referrer"><title>'+esc(r["date"])+' · WorkLedger</title><style>'+CSS+'</style></head><body>'+out+'<script>'+SCRIPT+'</script></body></html>'


def md_text(value):
    value = html.escape(str(value), quote=False).replace("\\", "\\\\")
    return re.sub(r'([`*_\[\]!])', r'\\\1', value).replace("\n", " ")


def render_markdown(r):
    a = r["analysis"]
    lines = [f'# {r["date"]} 工作分析', '', '**'+STATUS[a["status"]]+'**', '']
    if r.get("demo"):
        lines += ['> '+md_text(r.get("demo_notice", "合成演示数据，不是真实工作记录。")), '']
    if status_message(a):
        lines += [status_message(a), '']
    if r.get("previous_success"):
        lines += ['上次成功分析已保留，入口见同目录 report.html。', '']
    def statement(c):
        label = BASIS_LABELS[c["basis"]] + (" · 历史背景" if c["scope"] == "history" else "")
        links = ' '.join('[依据'+str(i+1)+'](report.html#e-'+eid+')' for i, eid in enumerate(c["evidence_ids"]))
        return f'**{label}**　{md_text(c["text"])} {links}'
    if a["highlights"]:
        lines += ['## 今日重点', '']
        for c in a["highlights"]:
            lines += [statement(c), '']
    for theme in a["themes"]:
        lines += ['## '+md_text(theme["title"]), '']
        for section in ("work", "results", "remaining"):
            if theme[section]:
                lines += ['### '+HEADINGS[section], '']
                for c in theme[section]:
                    lines += [statement(c), '']
        for p in main_issues(theme):
            lines += ['### '+("当前阻塞" if p["state"] == "open" and p["severity"] == "blocking" else "影响尚待确认" if p["state"] == "uncertain" else "仍需处理的问题"), '', statement(p["problem"]), '', statement(p["impact"]), '']
            if p.get("resolution"):
                lines += [statement(p["resolution"]), '']
        if theme["suggestions"]:
            lines += ['### '+HEADINGS["suggestions"], '']
            for c in theme["suggestions"]:
                lines += [statement(c), '']
        secondary = [p for p in theme["issues"] if p not in main_issues(theme)]
        if secondary:
            lines += ['<details><summary>已恢复或例行问题的处理依据</summary>', '']
            for p in secondary:
                lines += [statement(p["problem"]), '']
                if p.get("resolution"):
                    lines += [statement(p["resolution"]), '']
            lines += ['</details>', '']
    c = a["coverage"]
    lines += ['## 覆盖与依据', '', f'完整分析 {c["evidence_analyzed"]}/{c["today_evidence"]} 条证据，{c["tasks_analyzed"]}/{c["tasks_total"]} 个原生任务。', '',
              '归属标记只说明来源；缺少本人确认，不代表本人没有工作。采集器状态与任务阻塞分开记录。', '',
              f'源记录已截断或旧版仅保留片段：{len(c["source_truncated_ids"])} 条。完整范围、原始消息、工具结果、文档差异及会话关系见 [展开证据](report.html#audit)。', '',
              a["transport"]["data_flow"]+'。阅读本文件不触发模型。', '']
    if r["coverage"]["issues"]:
        lines += ['<details><summary>采集器状态</summary>', '']
        lines.extend(md_text(v)+'\n' for v in r["coverage"]["issues"])
        lines += ['</details>', '']
    return '\n'.join(lines)
