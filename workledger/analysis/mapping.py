"""Bounded map refinement using real model replies and unchanged claim validation."""
from __future__ import annotations

import copy
from .backend import AnalysisError
from .schema import validate_map

SOFT_FAILURES = {"schema", "invalid_json"}


def request_map(client, data, instruction, evidence, task_ids, required_ids):
    required = set(required_ids)
    full_tasks = set(task_ids)

    def ask(current):
        current_instruction = instruction
        if any(r.get("evidence") == "browser_visit_database" for r in current["records"]):
            current_instruction += "\n浏览器访问记录是当天线索，不能仅因 kind=context 就全部忽略。按域名和现存标题概括与工作有关的访问主题，陈述用 inference 并引用实际记录；明确仅有访问证据，现存标题未必是访问时标题。不推断阅读、聊天内容、完成成果、活跃时长或操作者；与工作无关的访问仍可忽略。"
        present = {r["id"] for r in current["records"] + current.get("context", [])}
        relevant = {eid: evidence[eid] for eid in present if eid in evidence}
        need = {r["id"] for r in current["records"]} & required
        tasks = {r["task_id"] for r in current["records"]} & full_tasks
        return client.request("map", current, current_instruction,
                              lambda obj: validate_map(obj, relevant, tasks, need, allow_partial=True))

    def subset(records, phase, number):
        current = copy.deepcopy(data)
        current["records"] = copy.deepcopy(records)
        current["map_refinement"] = {"phase": phase, "number": number,
                                     "instruction": "Analyze or explicitly classify every supplied record; do not infer missing work."}
        return current

    def merge(values):
        payload = {"items": [], "accounted_ids": [], "ignored": []}
        for value in values:
            for key in payload:
                payload[key].extend(value[key])
        payload["accounted_ids"] = list(dict.fromkeys(payload["accounted_ids"]))
        try:
            result = validate_map(payload, evidence, full_tasks, required, allow_partial=True)
        except ValueError as error:
            raise AnalysisError("schema", str(error)) from error
        result["discarded_uncited_statements"] = sum(v.get("discarded_uncited_statements", 0) for v in values)
        return result

    try:
        initial = ask(copy.deepcopy(data))
    except AnalysisError as original:
        if original.code not in SOFT_FAILURES or len(data["records"]) < 2:
            raise
        middle = len(data["records"]) // 2
        values = []
        for number, records in enumerate((data["records"][:middle], data["records"][middle:])):
            try:
                values.append(ask(subset(records, "split", number)))
            except AnalysisError as error:
                if error.code not in SOFT_FAILURES:
                    raise
        if not values:
            raise original
        return merge(values)

    values = [initial]
    result = initial
    for number in range(2):
        missing = set(result.get("unaccounted_ids", []))
        records = [r for r in data["records"] if r["id"] in missing]
        if not records or len(result["items"]) >= 20 or len(result["ignored"]) >= 200:
            break
        try:
            addition = ask(subset(records, "missing", number))
        except AnalysisError as error:
            if error.code not in SOFT_FAILURES | {"provider", "rate_limit"}:
                raise
            # A cooled-down transient provider cannot invalidate already
            # validated statements. Stop refinement and retain the real gap;
            # never reset the client or launch another request here.
            break
        if len(result["items"]) + len(addition["items"]) > 20 or len(result["ignored"]) + len(addition["ignored"]) > 200:
            break  # Preserve all accepted issues and claims; never trim to force coverage.
        values.append(addition)
        result = merge(values)
    return result
