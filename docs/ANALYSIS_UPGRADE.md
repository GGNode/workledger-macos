# WorkLedger 0.2：安装升级与本机 Codex 验收

本轮以公开仓库提交 `1042da82d9e750740a0a0b7d35ebeb96b5bf1a2f` 为基线，保留既有 macOS 服务、五类日志适配、浏览器配对和人工归属确认。不要退回最初的下载包。先读 `AGENTS.md` 和 `docs/LOCAL_CODEX_HANDOFF.md`，本文只增加 0.2 的操作及验收要求。

## 本轮解决了什么

模型输入不再来自简短展示卡片。原始保留证据先按本地报告日和真实委派关系整理，再分块理解、跨会话归并主题、检查进展和错误恢复，最后综合全天。正文解释工作、结果或影响、未完成事项；建议单列。HTML、Markdown 和 JSON 都使用 `analysis`，不再各自生成不同摘要。

新增 `workledger/analysis/`：`evidence.py` 负责证据与分块，`backend.py` 负责模型调用、缓存、超时和运行登记，`schema.py` 检查引用及断言强度，`pipeline.py` 做分层分析，`render.py` 负责一致呈现，`publish.py` 原子发布不同格式。原 `projects` 卡片和统计保留为 JSON 兼容字段，不是模型输入或页面主体。

## 升级步骤

在本机已有仓库中先检查工作区，保留未提交修改；不要用 `reset --hard` 或覆盖用户配置。通过分支/PR取得修改，或在正确基线上应用交付补丁：

```bash
git status --short
git rev-parse HEAD
# 补丁交付方式：先检查，不直接覆盖已有修改。
git apply --check /实际位置/workledger-0.2-analysis.patch
git apply /实际位置/workledger-0.2-analysis.patch
python3 -m unittest discover -s tests -v
npm ci --prefix tests --ignore-scripts --no-audit --no-fund
node --test tests/*.test.js
python3 -m compileall -q workledger
bash scripts/install.sh
```

若 HEAD 已前进，先比较冲突并迁移，不能把基线之后的修改删掉。安装器只替换程序目录，保留数据库、token、报告、项目配置和来源路径。不会自动启用模型、改变已选模型，也不会擅自开启定时日报。

浏览器代码本轮只增加内容截断标记，未更换配对协议或选择器。确定扩展加载的是仓库还是安装副本；在既有 Edge/Chrome 扩展管理页重新加载对应目录。原有配对码继续使用，不需要重新授权整套账户。

## 配置日常 OpenCode 分析

以下命令配置的是**工具以后每次生成日报时的分析后端**，不是让 OpenCode 参与本次软件开发。

在用户平时正常使用 OpenCode、其项目配置和插件能生效的目录中执行：

```bash
OPENCODE_BIN="$(command -v opencode)"
test -n "$OPENCODE_BIN" || { echo '当前终端找不到 OpenCode'; exit 1; }
~/.local/bin/workledger analysis configure \
  --backend opencode --dir "$PWD" --executable "$OPENCODE_BIN"
~/.local/bin/workledger analysis status
~/.local/bin/workledger report --date 2026-10-06 --refresh-analysis --no-open --json
```

日期只是命令示例，实机验收应选择有记录的实际日期。配置命令不调用模型；`report` 才会分析。首轮验收不要开自动日报，避免未检查质量就定时调用。报告末尾覆盖区可看到后端调用尝试、缓存命中、遗漏和错误类别。

本机 CLI 的实际调用形状：

```text
opencode run --dir <明确目录> --format json --title <本次专用标题> --agent <随机分析代理> --no-auto --no-interactive
```

证据经标准输入传入，不放在命令参数里。仅当用户明确填写模型时才增加 `--model`；空值不硬编码提供商或模型。分析代理使用正常 OpenCode 的全局模型选择规则，不复制用户日常代理的特定模型覆盖；日常配置依赖代理专属模型时，应在 WorkLedger 明确填入所需模型并实测。

HOME、XDG、正常配置位置和登录均沿用当前 CLI 环境。不会读取、复制 `auth.json`，也不会创建独立 XDG 目录。子进程通过 `OPENCODE_CONFIG_CONTENT` 添加随机命名、全部工具权限为 `ask` 的分析代理，并禁用本次共享；保留原有内联配置、提供商和插件，不使用 `--pure`，不写回正常配置文件。已有内联 JSON 非法时直接报配置错误，不偷偷丢弃它。

OpenCode 的非交互 `run` 默认自动拒绝权限请求；这里显式使用 `--no-auto --no-interactive`，不会批准工具执行。`ask` 保留原生工具定义，同时阻止实际工具访问；不能改成 `allow` 或开启自动批准。分析流出现任何工具结果（包括被拒绝的请求）仍立即拒收。部分 Zen 免费模型会将 `deny` 导致的工具定义缺失误判为非 OpenCode 请求，返回服务端 HTTP 403 `FreeTierError`；该错误不证明登录或模型不可用，也不能据此直接要求换模型。原生 `ask` 调用方式在保持权限拒绝的同时解决这一兼容问题，不修改服务请求头或身份。相关上游报告见 [OpenCode #51315](https://github.com/anomalyco/opencode/issues/51315)。若旧 CLI 不支持显式参数，保留配置错误并升级正常 CLI，不能偷偷取消权限约束。

**本机启动 CLI 不等于本机推理。** 传入的工作证据和输出会经过正常 OpenCode 的提供商、插件与会话持久化；模型可能是远程服务。工具权限限制不是操作系统沙箱，也不能替代对用户原有插件的信任。WorkLedger 不执行日志里的命令；分析流出现工具执行事件时拒收结果并终止本次调用。实际提供商/模型只有在返回的事件中给出时才显示，不猜测。

## launchd 和终端差异

配置时尽量保存 `command -v opencode` 得到的绝对路径；不要只假设后台 PATH 与交互终端相同。控制面板支持填写额外可执行文件目录 `llm.opencode_path`，供 OpenCode 所需的正常插件命令使用。自动查找仅检查 PATH 与几个常见安装位置，不递归扫描 HOME。

`analysis status` / `doctor` 只检查路径和配置，不代表模型登录、插件或推理成功。必须分别执行一次手动报告和一次经本机后台入口生成的报告。不要通过新建 XDG、拷贝认证、切换提供商或删除正常插件来让测试表面通过。

## 配置字段和成本边界

| 字段 | 默认值 | 作用 |
|---|---:|---|
| `llm.mode` | `off` | `off`、`opencode`、`ollama`、`openai-compatible` |
| `llm.opencode_executable` | 空 | 实际 CLI 路径；为空才按常见位置查找 |
| `llm.opencode_dir` | 空 | 设置界面应填写日常目录；未填时仍显式传入私有分析工作目录 |
| `llm.opencode_path` | `[]` | 仅为子进程附加 PATH 目录 |
| `llm.model` | 空 | OpenCode 不加 `--model`；HTTP 后端必须填写 |
| `llm.timeout` | 120 秒 | 一次后端调用上限 |
| `analysis.total_timeout` | 900 秒 | 一份报告的模型调用总预算 |
| `analysis.max_calls` | 96 | 包括失败尝试和重试；不是不受限调用 |
| `analysis.chunk_chars` | 20,000 字符 | 原始证据分块的近似字符预算，不是 tokenizer 精确 token 数 |
| `analysis.max_map_packets` | 48 | 原始证据分析包上限 |
| `analysis.context_chars` | 5,000 字符 | 每包额外上下文预算 |
| `analysis.context_days` | 7 天 | 相关原生任务的历史窗口 |
| `analysis.history_events_per_task` | 12 | 每个任务保留的历史条数上限 |
| `analysis.retries` | 1 | 限流、服务、JSON/结构错误可有一次修复尝试 |
| `analysis.cache_hours` | 24 小时 | 同证据、配置、提示版本下复用已验证结构的响应 |
| `analysis.failure_cooldown_seconds` | 300 秒 | 同一失败请求不会被立即反复重启 |
| `analysis.max_output_bytes` | 1 MiB | 单次 stdout/stderr 或 HTTP 响应总量限制 |

预算达限明确显示覆盖缺口，不把首尾几条日志冒充全日分析。任务按跨日内时间分散顺序和轮询进入分块；每包最多 16 个原生任务、80 个证据片段。长记录切片全部排队，仅分析部分切片不算完整覆盖该条证据。输出主题再做有界归并与全天分层综合，未能全局合并会说明，不能伪造原生关系。

缓存存放于私有数据目录 `analysis/cache/`，依据完整请求内容、配置与提示版本生成键。它不读取认证文件来判断是否登录。用户改变外部 OpenCode 默认模型或配置文件后，使用 `--refresh-analysis` 或面板“跳过缓存”重新分析；文件外部变动不是自动可知。打开已经生成的 HTML、Markdown、JSON 或面板中的已有报告，不会调用模型。

## 证据、降级与防循环

`analysis.status` 是统一状态：`complete`、`partial`、`degraded`、`disabled`、`empty`。`complete` 只表示本轮保留证据的分析流程完成，不是结论获得独立实测证明。来源文本已截断会显示为覆盖缺口。`model_summary` 仅保留弃用的状态/重点别名，判断依据改为完整 `analysis`，不要继续按旧字段是否为 null 判断质量。

关闭模型时采用明确标记的观察摘要，不复制原始指令假装理解工作。模型失败时保留全部原始证据和旧报告；一次生成使用不可变版本目录，三种格式和状态文件通过同一指针发布。新版本失败不会删除上次完整分析，升级前的旧版报告也备份。版本目前不自动清理，使用量较大时可由用户在确认后清理旧版本，不要在升级脚本中删除。

默认报告只展示未恢复且有实际影响的问题。恢复判断必须有后续成功操作或人类确认支持；单独一句“完成”不足以恢复。准确的操作 ID 只提供重试候选，不证明整个任务成功。采集器权限错误另列在采集状态，不能混成项目阻塞。

分析启动前写入私有 `analysis/runs/` 登记，记录随机标题、nonce、原生 session ID、开始结束时间。后续报告排除这些 session 及其明确后代、私有报告产物和精确探测事件。不能仅因为目录叫 WorkLedger、文字提到日报或 PROBE_OK，就排除正常开发任务。源日志与数据库仍保留原始记录；排除发生在分析输入和报告统计阶段。

新采集器每条普通文本和工具输出最多保留 131,072 字符，工具参数为有界摘录，超出部分有长度与截断标记。Office 差异、浏览器正文仍有限制并显式标记。首次升级会重新解析仍存在的原生日志，以补充工具结果；原日志已经不存在的旧片段无法补全。人工确认和稳定 evidence ID 保留。

## 必须在本机完成的验收

这些检查不能由容器测试代替，也不得先写成通过。

1. 保留正常 OpenCode 配置，使用实际 CLI生成一个有代表性日期的报告。确认参数包含 `--dir`，模式、提供商、插件符合预期。不要打印认证文件或把请求正文放入终端日志。
2. 读取 HTML 和 Markdown 主体，不展开证据，能说出该日的主要工作、产生的进展、尚缺的验证及真正阻塞。要求而未完成的事不能被写成成果；Agent 分数声明不能被当作独立验证。
3. 选至少一个跨 session 同主题和一个同目录不同主题核查；语义主题可以变化，SQLite 原生 `parent_id/relation` 不能因此改变。检查历史背景不计入今天结果。
4. 对照一次先失败后恢复和一次真正受阻。前者不占据主问题区，后者明确影响、当前状态和证据。无充分上下文时用未确定，不擅自升级成功或失败。
5. 点开重要分析的依据，检查源内容确实支持该结论及其时间、归属、验证范围。引用存在只是第一步；语义错误必须补充脱敏反例或修正提示/流水线，不能放宽检查让它“通过”。
6. 连续生成同一天两次，第二次多数后端请求命中缓存。再正常打开已有报告，调用次数不增加。新增用户工作后应使对应分析重新计算。
7. 等一轮采集后再生成，确认分析自身的 OpenCode session 没有变成新工作；正常 WorkLedger 开发仍在。确认配置数据目录与生成报告未被文件观察再次计入成果。
8. 用隔离的测试配置检查不可用 CLI 和短超时，得到清楚降级且旧报告仍可打开。不要删除正常登录。手动、Finder、自动生成与面板看到的状态必须一致。
9. 最后才开启用户选择的自动生成时间，并验证 launchd 下的 PATH、登录、插件及 Finder 打开行为。真实验收记录保存在仓库之外。

## 回归与样例

```bash
python3 scripts/render-analysis-fixture.py --output /tmp/workledger-analysis-acceptance
# 可选，已有 Playwright/Chromium 才运行，不修改浏览器组织策略。
python3 tests/render_smoke.py
python3 tests/analysis_render_smoke.py
```

合成样本使用人工编写的预期 JSON 响应回放，用来检验组织、引用和渲染；生产代码中没有回放后端或按关键词替换的“语义分析”。样例不证明真实模型的推理质量，必须保留其醒目标记。

## 提交与公开边界

只提交源码、通用文档和脱敏合成测试。不要提交数据库、真实日报、真实模型输入、配对码、用户邮箱、实际机器配置或本机验收日志。使用 noreply 作者/提交者身份，检查暂存区后再推送；GitHub Actions 的状态以新提交实际运行结果为准。
