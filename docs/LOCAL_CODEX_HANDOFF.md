# 给本机 Codex 的交接任务

这是可运行的 0.1.0 源码，不是空架构。已有 SQLite 事件库、五类 Agent 适配、文档差异、网页/VS Code 扩展、本机控制面板、报告、调度、安装和测试。请在用户的真实 Mac 上完成适配及验收，保持现有数据语义。

## 可直接交给 Codex 的任务

> 阅读本仓库 AGENTS.md、docs/ADAPTERS.md 和这份交接。先运行现有测试，再检查本机 Python、macOS、各 Agent 版本及其实际日志路径。不要读取 API key、认证文件或整块磁盘。用本机最小脱敏样本验证各解析器，修复不匹配字段，补回归测试。验证主会话与子会话、fork、当天与历史消息、人工确认与 Agent 写入区分。安装并验收控制面板、手动日报、launchd 定时、Finder 打开、网页扩展。最终报告只列真正通过的验收项，不能把 found 文件当作适配成功。用户已授权将本工具放在自己的 GitHub 新私有仓库中；使用本机 gh 验证账号 YOUR_GITHUB_ACCOUNT 后运行发布脚本，已有同名仓库时先检查，不强推、不覆盖其他仓库。

## 1. 确认本机实际环境

在源码根目录运行：

```bash
sw_vers
uname -m
command -v python3
python3 --version
command -v codex claude opencode pi dsh
python3 -m workledger doctor
python3 -m unittest discover -s tests -v
node --test tests/*.test.js
```

不同 CLI 的版本参数可能不同，先用帮助确认。若当前 Python 低于 3.11，使用用户已有 Conda/Homebrew Python；不要替换系统 Python。不要硬编码用户名、Homebrew 前缀、时区、项目路径或任何模型名称。

开发时使用独立目录，避免演示数据进入正式数据库：

```bash
python3 -m workledger --home /tmp/workledger-acceptance init
python3 -m workledger --home /tmp/workledger-acceptance ui
```

## 2. 真实日志逐项验收

| Agent | 需要取得的最小证据 | 要核实的字段 |
|---|---|---|
| Codex | 一次主任务、一次真实子任务、一次成功/失败写入 | session_meta.id / source.subagent.thread_spawn.parent_thread_id；call_id；response_item；event_msg |
| Claude Code | 主 transcript、subagents 文件各一份 | sessionId / agentId；parentUuid 仅是消息父 ID；tool_use/tool_result 的 id 配对 |
| OpenCode | 当前 DB 的只读表结构、一条用户消息及 tool part | session/message/part；parent_id；JSON data；state.status；WAL 下的新行可见 |
| Pi | 当前 session header、一条消息和工具结果；使用的 subagent 扩展配置 | version / id / parentSession；entry.parentId；toolCallId；扩展自己的真实主/子 ID |
| dsh | 当前持久化根目录，header，继承切点和工具结算 | version / id / origin / parentSession / isSeeded；最后 inherited end-seed；seq/time；source.kind；result source.callId / content[0] |

不匹配时改对应 `workledger/adapters/` 文件，不要改源日志来迎合解析器。存一份脱敏最小夹具并加入测试。`dsh` 原始文件默认压缩时，应使用同一解释器安装 zstandard 并验证一份实际文件；当前开发环境没有该解码器，压缩分支没有实际运行。

DSH 同一会话目录可能保留多个不可变格式代际。扫描时选文件名中最高代际，同代际选最近写入文件；若本机保留试验迁移与仍在写的旧代际，请将实际活跃文件设为明确路径，不要一并读取。已采集过一个格式代际后再迁移到另一代际时，需补实机去重测试；当前按 session/seq 去重，不能保证任意迁移中被重新编号的事件会自动合并。

Pi 的原生 `parentSession` 只证明 fork/继承。需要查看本机 subagent 扩展源代码中真正的 spawn/fork 行为，输出 `workledger.event.v1` 的 `parent_session_id` 和 `relation=delegation`。已掌握准确 ID 时可显式链接：

```bash
workledger link --source pi --child CHILD_ID --parent PARENT_ID \
  --relation delegation --reason '从本机 subagent 扩展的显式 spawn 事件取得'
```

未知父 ID 留空并显示缺口，不能用同时启动、同一目录、标题相似来推断。

自研或日志结构不同的 Agent 使用 `docs/ADAPTERS.md` 的 bridge JSONL 即可。直接写入 inbox 要原子写入或追加完整换行；`/api/events` 批量接口每次最多 200 条，要求配对 token。

## 3. 人工编辑验收

在已配置测试目录中，先扫描建立基线，再亲自改一份 Markdown / PPT / Word / Excel；并让 Agent 单独改另一份。核实：

- 初始文件不会被算成今天新产物。
- 后续保存能显示相应正文、页面、单元格或图片/图表变化。
- 纯 Excel 公式缓存重算不会被当作人工改公式。
- Agent 原生日志中的成功写入属于 Agent；失败写入不算完成。
- 通用文件变化初始归属必须是待确认。控制面板确认本人编辑后，原始证据仍保留，报告单列本人工作。
- 没有监测期间的逐步快照，就不能重建这段期间每一次人工/Agent 交错编辑。不要声称能精确还原到每一行作者。

文档监测是每 30 秒一次的稳定快照，不是每个按键。快速改后又撤销可能不被捕获。Word 接受/拒绝修订、复杂 PPT 对象、Excel 格式及共享编辑的作者归属需要额外 Office 侧集成。当前可以比较内容，不会凭 `lastModifiedBy` 自动认人。

## 4. 网页实时验收

加载 `extensions/browser`，用 `workledger pair` 配对。先在 ChatGPT 验证，再验证实际使用的 Claude/Gemini。至少做这些场景：

1. 打开昨天的长对话、向上滚动旧消息：今日消息数不增加。
2. 在这个旧对话发送一个新问题：只增加新问题和新回答，流式增量保持同一 message id。
3. 新建对话发送问题：等 URL 有稳定 conversation id 后采集，不产生临时 `/` 会话的重复记录。
4. 刷新页面、重复打开同一页：不把历史重新导入成新工作。
5. 暂时停本机服务后继续发送，恢复服务：队列重试，按原观察时间归日，不能按送达时间归日。

当前扩展不劫持浏览器内部 API，不读取 Cookie，不承诺旧消息创建时间能从 DOM 获得。它以可信发送动作及新 DOM 消息配对来识别新内容；真实站点变更、语音输入、只有附件的问题、编辑旧问题、重新生成、长时间回答等有覆盖缺口。需要精确补录用官方导出创建时间。Safari 不在当前已实现范围。

## 5. 安装和调度验收

运行 `bash scripts/install.sh`，确保登录 LaunchAgent 正常：

```bash
launchctl print gui/$(id -u)/local.workledger.agent
~/.local/bin/workledger doctor
~/.local/bin/workledger report --open
```

在面板将自动时间设置为当前时间后的几分钟，确认只生成一次，HTML 和 Finder 文件夹均打开。验证关闭自动模式后仍可手动生成；暂停后台扫描不等于停止 Agent 自己写日志，恢复扫描会补读。退出登录/重启、Mac 睡眠唤醒及企业终端策略，需要在本机真实检查。

若安装器被管理策略阻止注册 launchd，保留已有源码与数据，使用 `workledger ui` 的前台模式验收，不绕过企业策略。若 `.command` 被 macOS 标记为下载内容，可直接从终端执行已检查的 `bash scripts/install.sh`；不要递归清除整个用户目录的扩展属性。

## 6. 性能与升级方向

当前跳过签名未改变的日志；改变的大型 JSONL 会整文件重读并按稳定 ID 去重。OpenCode 变更后读取一致只读快照。大量多月历史会增加首次导入和重读成本。实机性能不足时，优先实现带偏移 checkpoint 的增量读取，并保留 pending tool calls、半行缓冲、轮转检测、源时间和去重测试。不要简单用最后修改时间替代消息时间。

报告 JSON 包含所选日证据以及当前会话元数据。若多年历史很大，可裁剪为当日相关会话及祖先。服务日志有轮转；原始事件没有自动删除策略，后续可以增加明确可配置的保留天数与用户确认。

## 7. GitHub 交付

当前远端仓库未创建，因为连接器缺少该动作。开发环境没有 gh 或 GitHub token。源码没有使用其他已有仓库作为替代。

本机使用用户自己的已授权 gh 会话：

```bash
gh auth status
gh api user --jq .login
bash scripts/publish-github.sh
```

脚本默认目标 `YOUR_GITHUB_ACCOUNT/workledger-macos`，私有，不覆盖同名仓库，不强推。创建成功后以 `gh repo view` 返回的真实 URL 交付；不能预写一个不存在的仓库链接。需要继续开发时 clone 刚创建的远端，不要把个人 data 目录当仓库。
