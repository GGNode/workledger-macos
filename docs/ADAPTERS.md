# Agent 适配与统一事件接口

所有原生读取器位于 `workledger/adapters/`。本版本验证的是明确列出的结构；不是“所有版本自动兼容”。路径发现与内容解析分开显示。

## 原生字段

Codex 从 session_meta 取得会话身份和 cwd，从 `source.subagent.thread_spawn.parent_thread_id` 或明确父字段取得派生关系；`forked_from_id` 单列为 fork。用户/Assistant 的 response_item 优先于重复 event_msg；开发者消息、环境注入、thinking 不作为本人工作。function_call/custom_tool_call 和结算按 call_id 配对。apply_patch 必须有成功结算或明确 patch_apply_end.success 才形成文件写入记录。Shell 内部产生的文件不从命令字符串推断作者。

Claude Code 的 `parentUuid` 是消息树边，不是 session 边。`sessionId/subagents/agent-ID.jsonl` 与 agentId 识别子会话。user 消息包含 tool_result 时不会计为用户侧指令。仅靠文件位置无法知道所有深层直接父会话；需要实际 hook / 原生 spawn 元数据。

Pi 的 session header.parentSession 是父日志路径，作为 fork；消息 entry.parentId 只保存为消息元数据。工具由 toolCall/toolResult 配对。第三方 subagent 扩展需要补显式 delegation 关系，统一事件接口可直接接入。

OpenCode 优先只读 DB，要求 session、message、part 表及其 JSON data 列。session.parent_id、message.role、part.type、tool state.status/time/input/output 是主要读取字段。不是该 schema 时显示错误，配置 `sources.opencode.export_paths` 指向原生 export JSON。也可 `workledger import file.json --format opencode`。

dsh 支持 v2/v3/v4 逐事件结构，明确拒绝 v0/v1 打包 delta。忽略嵌入的 provider stream，不重复计入消息。最后一个 `session/end-seed` 且 `data.inherited=true` 的 seq 决定继承前缀，前缀不作为孩子的新增工作。user/message 的 source.kind 区分用户与插件注入；v4 tool result 使用 source.callId 和 content[0] 的 tool_result。非连续 seq、未来版本或缺失继承切点会显示错误。v4 已对当前声明结构实现读取，尚无用户实机验证。

## 通用 bridge

为一个已有但日志特殊的 Agent 写适配器，只需输出以下 JSONL。每行必须有换行。`id` 必须是稳定的原生事件 ID，不要每次同步生成新 UUID。

```json
{"schema":"workledger.event.v1","source":"pi","id":"child-23/result-5","session_id":"child-23","parent_session_id":"root-1","relation":"delegation","kind":"agent_message","actor":"agent","occurred_at":"2026-10-06T09:12:00+08:00","text":"测试完成，仍有一个边界条件待确认。","evidence":"native_extension_event"}
```

可写入 `~/Library/Application Support/WorkLedger/inbox/`，用临时文件 + rename 交付；或配置 `sources.bridge.paths` 指向其他 JSONL 文件。也可 POST `/api/events`，body 为 `{"events":[...]}`，Header 为 `Authorization: Bearer <workledger pair 给出的 token>`，Content-Type 为 application/json。

支持的 kinds：user_message、agent_message、delegated_instruction、file_edit、document_change、run_interval、tool_call、tool_result、context、note、browser_message、review。

actor 支持 agent、system、unknown。外部生产方自报 human 会降为 unknown，不能冒充本人确认。本人确认使用本机控制面板或 `workledger confirm EVENT_ID --actor human --reason '本人确认'`。

chronology 支持 source_time、live_observed、historical、unknown。前两者必须有有效的带时区时间，未知时间不填。墙钟执行区间还需 ended_at。源缺少精确时间时不要猜测。

session 的 parent 只接受该来源自身 namespace 内的 native ID；跨产品的编排父子关系需新增显式跨源边适配，不能把标题相似当链接。当前报告可同时分组多个产品，但不会伪造一个跨产品调用树。

## 日志适配不是运行代理

本工具不会替用户启动上述 Agent、代替它们调用模型或抓取远程账户。它观察本机已存在记录。Agent 在远程主机运行且没有把日志同步到 Mac 时，这部分自然不可见。需要用户指定同步后的本地路径或将规范事件送至本机服务。
