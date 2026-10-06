"""Provenance and assertion-strength validation, not an entailment oracle."""
from __future__ import annotations

import copy

BASES = {"human_confirmed", "user_direction", "tool_observed", "agent_claim", "unverified_change", "inference"}
SECTIONS = ("work", "results", "remaining", "suggestions")
BASIS_LABELS = {"human_confirmed": "本人确认", "user_direction": "用户推动", "tool_observed": "执行证据",
                "agent_claim": "Agent 自述 · 待验证", "unverified_change": "变化待确认", "inference": "分析判断"}


def array(value, name, maximum=1000):
    if not isinstance(value, list) or len(value) > maximum:
        raise ValueError(name + " must be a bounded list")
    return value


def ids(value, allowed, name="evidence_ids", required=True):
    values = array(value, name, 300)
    if any(not isinstance(v, str) for v in values) or (required and not values) or not set(values) <= set(allowed):
        raise ValueError(name + " contains unknown/missing references")
    return list(dict.fromkeys(values))


def sentence(value, maximum=800):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError("analysis text is missing or too long")
    if value.strip() == "PROBE_OK" or value.lstrip().startswith("Objective:"):
        raise ValueError("raw diagnostic/delegation label is not an analysis")
    return value.strip()


def observed(e):
    return ((e["kind"] == "tool_result" and e.get("metadata", {}).get("success") is True) or
            (e["kind"] == "file_edit" and e.get("evidence") == "successful_tool_result"))


def claim(value, evidence, *, section="work"):
    if not isinstance(value, dict):
        raise ValueError("claim must be an object")
    references = ids(value.get("evidence_ids"), evidence)
    basis = value.get("basis")
    scope = value.get("scope")
    if basis not in BASES or scope not in {"today", "history"}:
        raise ValueError("claim basis/scope invalid")
    rows = [evidence[i] for i in references]
    if scope == "today" and not any(e.get("scope") == "today" for e in rows):
        raise ValueError("historical-only evidence cannot support today's progress")
    if basis == "human_confirmed" and not any(e.get("actor") == "human" for e in rows):
        raise ValueError("no human confirmation in cited evidence")
    if basis == "user_direction" and not any(e["kind"] in {"user_message", "note", "review"} for e in rows):
        raise ValueError("no user decision evidence")
    if basis == "tool_observed" and not any(observed(e) for e in rows):
        raise ValueError("no successful tool settlement")
    if basis == "agent_claim" and not any(e["kind"] == "agent_message" for e in rows):
        raise ValueError("no agent statement")
    if basis == "unverified_change" and not any(e["kind"] in {"document_change", "file_edit"} and e["actor"] == "unknown" for e in rows):
        raise ValueError("no unattributed change")
    if section == "results":
        if basis == "user_direction" or all(e["kind"] in {"user_message", "delegated_instruction", "tool_call", "context"} for e in rows):
            raise ValueError("a request/plan alone is not an achieved result")
        if scope == "history":
            raise ValueError("historical outcomes belong in context/work, not today's results")
    return {"text": sentence(value.get("text")), "evidence_ids": references, "basis": basis, "scope": scope}


def issue(value, evidence):
    if not isinstance(value, dict) or value.get("state") not in {"resolved", "open", "uncertain"} or value.get("severity") not in {"routine", "material", "blocking"}:
        raise ValueError("invalid issue state/severity")
    result = {k: value[k] for k in ("state", "severity")}
    for field in ("problem", "impact"):
        try:
            result[field] = claim(value.get(field), evidence)
        except ValueError as exc:
            raise ValueError(f"issues.{field}: {exc}; expected object with text/evidence_ids/basis/scope, never a string") from exc
    try:
        result["resolution"] = claim(value["resolution"], evidence) if value.get("resolution") else None
    except ValueError as exc:
        raise ValueError(f"issues.resolution: {exc}; expected claim object or null, never a string") from exc
    if result["state"] == "resolved":
        if not result["resolution"]:
            raise ValueError("resolved issue needs resolution evidence")
        failed = [evidence[i] for i in result["problem"]["evidence_ids"]]
        fixes = [evidence[i] for i in result["resolution"]["evidence_ids"]]
        # A final 'done' is insufficient, and a pre-failure success is not a retry.
        latest_failure = max((e.get("occurred_at") or "" for e in failed if e.get("metadata", {}).get("success") is False), default="")
        if not any((observed(e) or e.get("actor") == "human") and (e.get("occurred_at") or "") >= latest_failure for e in fixes):
            result["state"] = "uncertain"
            result["resolution"]["basis"] = "agent_claim" if any(e["kind"] == "agent_message" for e in fixes) else "inference"
    if result["state"] == "open" and result["severity"] == "blocking":
        context = [evidence[i] for field in ("problem", "impact") for i in result[field]["evidence_ids"]]
        failed_at = max((e.get("occurred_at") or "" for e in context if e.get("metadata", {}).get("success") is False), default="")
        if not any(e["kind"] in {"agent_message", "user_message", "note", "review"} and
                   (e.get("occurred_at") or "") >= failed_at for e in context):
            # A failed tool line alone has no task-level closing assessment.
            result["state"] = "uncertain"
    return result


def item(value, evidence, tasks):
    if not isinstance(value, dict):
        raise ValueError("work item must be an object")
    tids = ids(value.get("task_ids"), tasks, "task_ids")
    result = {"title": sentence(value.get("title"), 80), "task_ids": tids}
    relevant = {eid: e for eid, e in evidence.items() if e["task_id"] in tids}
    for section in SECTIONS:
        result[section] = [claim(c, relevant, section=section) for c in array(value.get(section, []), section, 8)]
    result["issues"] = [issue(c, relevant) for c in array(value.get("issues", []), "issues", 6)]
    if not any(result[k] for k in SECTIONS) and not result["issues"]:
        raise ValueError("empty work item")
    if not any(evidence[eid].get("scope") == "today" for eid in item_ids(result)):
        raise ValueError("history-only work item cannot count as today's theme")
    return result


def item_claims(value):
    for section in SECTIONS:
        yield from value.get(section, [])
    for problem in value.get("issues", []):
        for field in ("problem", "impact", "resolution"):
            if problem.get(field):
                yield problem[field]


def item_ids(value):
    return list(dict.fromkeys(eid for c in item_claims(value) for eid in c["evidence_ids"]))


def validate_map(obj, evidence, task_ids, required_ids):
    if not isinstance(obj, dict):
        raise ValueError("map response must be an object")
    accounted = ids(obj.get("accounted_ids"), evidence, "accounted_ids")
    if not set(required_ids) <= set(accounted):
        raise ValueError("map response silently omitted evidence")
    items = [item(v, evidence, task_ids) for v in array(obj.get("items"), "items", 20)]
    ignored = []
    for row in array(obj.get("ignored", []), "ignored", 200):
        if row.get("reason") not in {"routine", "duplicate", "context_only", "not_work", "probe"}:
            raise ValueError("missing noise exclusion reason")
        ignored.append({"evidence_ids": ids(row.get("evidence_ids"), evidence), "reason": row["reason"]})
    cited = {eid for v in items for eid in item_ids(v)} | {eid for row in ignored for eid in row["evidence_ids"]}
    if not set(required_ids) <= cited:
        raise ValueError("accounted IDs must also be cited or explicitly classified as noise/context")
    return {"items": items, "accounted_ids": accounted, "ignored": ignored}


def validate_routes(obj, available):
    if not isinstance(obj, dict):
        raise ValueError("route response must be an object")
    groups = []
    used = []
    for g in array(obj.get("groups"), "groups", 1000):
        members = ids(g.get("item_ids"), available, "item_ids")
        used += members
        groups.append({"title": sentence(g.get("title"), 80), "item_ids": members})
    if len(used) != len(set(used)) or set(used) != set(available):
        raise ValueError("semantic routes must partition every work item exactly once")
    return {"groups": groups}


def validate_theme(obj, evidence, tasks, expected_tasks):
    result = item(obj, evidence, tasks)
    if set(result["task_ids"]) != set(expected_tasks):
        raise ValueError("theme synthesis omitted a participating native task")
    return result


def validate_day(obj, evidence):
    if not isinstance(obj, dict):
        raise ValueError("day response must be an object")
    highlights = [claim(v, evidence) for v in array(obj.get("highlights"), "highlights", 5)]
    if any(v["scope"] != "today" for v in highlights):
        raise ValueError("daily highlights must concern today")
    return {"highlights": highlights}
