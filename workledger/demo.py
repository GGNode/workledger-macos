from pathlib import Path
from .config import Config
from .store import Store
from .report import write_report


def create_demo(output: Path) -> Path:
    cfg = Config(output)
    cfg.save({"timezone": "Asia/Hong_Kong", "report_open": False, "projects": [{"name": "电化学模型分析", "paths": ["/demo/electrochemistry"]}, {"name": "实验结果汇报", "paths": ["/demo/reports"]}], "llm": {"mode": "off"}})
    with Store(cfg.db_path) as s, s.transaction():
        s.cache_set("demo", True)
        s.cache_set("last_capture", "2026-10-06T10:30:00+00:00")
        root=s.session("codex", "demo-main", title="检查模型守恒与测试", cwd="/demo/electrochemistry")
        child=s.session("codex", "demo-test", parent="demo-main", relation="delegation", title="检查边界条件", cwd="/demo/electrochemistry")
        child2=s.session("claude", "demo-review", title="检查汇报材料", cwd="/demo/reports")
        s.event("demo", "instruction", "user_message", session_id=root, occurred_at="2026-10-06T01:00:00Z", text="对比两套边界条件，检查电荷守恒；先做测试，再修改实现。", evidence="synthetic_fixture", actor="unknown")
        s.event("demo", "child-output", "agent_message", session_id=child, occurred_at="2026-10-06T01:50:00Z", text="边界条件检查完成，建议补充零通量边界测试。", actor="agent")
        s.event("demo", "root-output", "agent_message", session_id=root, occurred_at="2026-10-06T02:15:00Z", text="已修正边界通量的符号，新增 3 个测试。现有测试均通过；尚未验证真实实验数据。", actor="agent")
        s.event("demo", "write", "file_edit", session_id=child, occurred_at="2026-10-06T01:55:00Z", artifact="/demo/electrochemistry/tests/test_flux.py", text="write 返回成功", actor="agent", evidence="successful_tool_result")
        s.event("demo", "manual-note", "note", occurred_at="2026-10-06T03:10:00Z", text="亲自复查电荷守恒推导，确认比较两套边界条件时必须使用相同的积分区域。", actor="human", evidence="explicit_user_note", metadata={"project":"电化学模型分析"})
        s.event("demo", "ppt", "document_change", occurred_at="2026-10-06T06:00:00Z", artifact="/demo/reports/阶段汇报.pptx", text="阶段汇报.pptx：第 7 页、第 11 页", actor="unknown", evidence="snapshot_diff",metadata={"project":"实验结果汇报", "observed_not_edit_time":True, "changes":[{"section":"第 7 页", "diff":"- 对所有电芯使用相同归一化基准\n+ 分型号报告基准差异，并保留原始幅值"},{"section":"第 11 页","diff":"- 已证明模型正确\n+ 模型通过当前测试，真实数据仍待验证"}]})
        s.event("demo", "ppt-confirmed", "note", occurred_at="2026-10-06T06:20:00Z", text="本人调整了汇报结构，将实验观察和模型解释分开呈现。", actor="human", metadata={"project":"实验结果汇报"})
        s.event("demo", "review", "agent_message", session_id=child2, occurred_at="2026-10-06T05:00:00Z", text="发现两处结论超出已有数据支持范围，已给出替换措辞，等待人工定稿。", actor="agent")
        s.event("demo", "run1", "run_interval", session_id=root, occurred_at="2026-10-06T01:00:00Z", ended_at="2026-10-06T02:15:00Z", actor="agent")
        s.event("demo", "run2", "run_interval", session_id=child, occurred_at="2026-10-06T01:10:00Z", ended_at="2026-10-06T01:55:00Z", actor="agent")
        s.event("demo", "old-visible", "context", chronology="historical", observed_at="2026-10-06T01:00:00Z", text="上个月的旧聊天，不应算作今天新完成的工作")
        return write_report(cfg,s,"2026-10-06")
