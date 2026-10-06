# 开发参考资料

查阅日期：2026-10-06。实现依据官方文档和上游源码中的字段契约，代码为本项目实现，不复制上游整套项目。主分支内容会变化；真正兼容性最终取决于本机版本及回归夹具。

| 用途 | 一手资料 |
|---|---|
| Codex rollout / 协议事件 / 子线程 | https://github.com/openai/codex/blob/main/codex-rs/protocol/src/protocol.rs |
| Codex 高级配置 | https://developers.openai.com/codex/config-advanced/ |
| Claude Code hooks：SubagentStart/Stop、PostToolUse | https://code.claude.com/docs/en/hooks |
| Pi session header、message tree、parentSession | https://pi.dev/docs/latest/session-format |
| OpenCode 官方仓库 | https://github.com/anomalyco/opencode |
| OpenCode 本机 session API | https://opencode.ai/docs/server/ |
| dsh 会话事件与继承切点 | https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/core/session/src/types.ts |
| dsh 持久化路径与物理头 | https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/session/session-persistence-jsonl/src/format.ts |
| dsh v3 物理 codec | https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/session/session-format-v2-to-v3/src/codec.ts |
| dsh 格式版本与发布记录 | https://github.com/deepseek-ai/deepseek-harness/blob/master/docs/session-format-status.md |
| dsh 消息来源与 ToolResultMessage | https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/llm/llm/src/message.ts |
| VS Code document change/save API | https://code.visualstudio.com/api/references/vscode-api |
| Chromium content scripts | https://developer.chrome.com/docs/extensions/develop/concepts/content-scripts |
| Python sqlite3、zoneinfo、plistlib | https://docs.python.org/3/library/ |
| SQLite WAL | https://sqlite.org/wal.html |
| Apple Launch Agents | https://developer.apple.com/library/archive/documentation/MacOSX/Conceptual/BPSystemStartup/Chapters/CreatingLaunchdJobs.html |

DSH 查阅时主分支 writer 常量为 v4，发布记录仍将 v3 作为最新已发布格式。两者不混用。适配器读取 v2/v3/v4 的逐事件报表相关子集，不是 dsh 的完整恢复、迁移或消息投影实现。

OpenCode 本机 DB 没有在本环境取得，读取器对已知表结构进行检测，未知表不做猜测。网页 DOM 选择器也并非供应商承诺的公共接口，已单独标记实机验收需求。
