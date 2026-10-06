# WorkLedger 0.1.0 验证记录

日期：2026-10-06。以下是交付开发环境的实际运行结果，不等同于用户 Mac 上的验收。

## 已运行并通过

| 验证 | 结果 | 范围 |
|---|---:|---|
| Python 单元及本机 HTTP 集成测试 | **76 / 76 通过** | 五类 Agent 解析样本、父子与 fork、角色区分、消息去重、版本修订、时间、文档差异、鉴权、HTTP 事件事务、调度逻辑 |
| JavaScript 消息及队列测试 | **14 / 14 通过** | 旧消息基线、新消息、流式更新、可信提交、路由、去重、过期、中文 UTF-8 分批、失败重试与溢出提示 |
| 离线浏览器页面检查 | **6 项通过** | 实际 HTML/app.js，使用明确标记的模拟 fetch：控制面板渲染、项目保存请求、桌面日报、390px 无横向溢出、证据折叠、无 JavaScript 异常 |
| 安装脚本及升级检查 | **通过** | Linux 隔离 HOME 和含空格目录；安装后 CLI、本人备注、日报、演示、再次安装保留数据、扩展文件、快捷入口和 token 权限 |
| Python wheel 构建 | **通过** | `pip wheel --no-deps --no-build-isolation --no-index .`，没有下载运行依赖 |
| 语法检查 | **通过** | 全部核心 Python 编译；浏览器、VS Code、面板 JavaScript；全部 shell 安装与启动脚本 |

测试使用 Python 3.13.5、Node.js 22.16、Linux 容器、Playwright 与容器内 Chromium。除 HTTP 测试的真实 loopback 请求和 SQLite WAL 测试外，Agent 日志均为依据文档结构构造的样本，未读取用户本机原始日志。

执行命令：

```bash
python3 -m unittest discover -s tests -v
node --test tests/*.test.js
python3 -m compileall -q workledger
for script in scripts/*.sh *.command; do bash -n "$script"; done
for script in extensions/browser/*.js extensions/vscode/*.js workledger/static/*.js; do node --check "$script"; done
```

可选视觉检查依赖 Playwright 和已安装浏览器：

```bash
CHROMIUM_EXECUTABLE="/实际路径/Chromium" python3 tests/render_smoke.py
```

## 未通过运行验证，不能据此声称已经兼容

**真实 macOS。** 当前环境没有 macOS。launchd plist 内容、参数和含空格路径有测试，但登录启动、睡眠恢复、Finder 自动打开、macOS 权限、Apple Silicon 和 Intel 真机均待验收。GitHub Actions 的 macOS/Python 3.11–3.13 矩阵已写入配置，但仓库尚未建立，因此没有远端 CI 结果。

**真实扩展与网站。** 环境的浏览器管理策略禁止导航和加载扩展。`tests/ui_smoke.py` 尝试访问本机测试页面时被 `ERR_BLOCKED_BY_ADMINISTRATOR` 拦截；没有绕过策略。离线页面检查只通过 `set_content` 进行，并不证明实时网站选择器或扩展安装已通过。保留该集成脚本供本机在允许的浏览器上运行；它使用构造的 ChatGPT 页面，仍不能替代 ChatGPT、Claude、Gemini 在线页面的人工验收。

**各 Agent 的当前真实版本。** 解析器覆盖所列格式，未在用户实际的 Codex、Claude Code、OpenCode、Pi、dsh 安装上运行。Pi 第三方 subagent 插件、Claude 更深层父子边、跨产品委派以及非标准日志需要本机适配。dsh Zstd 解压路径已实现，但此环境没有可选 `zstandard` 模块，未运行真实压缩日志测试。DSH 跨格式升级重新编号的事件需额外迁移去重验证。

**实际 Office 与作者身份。** Word/PPT/Excel 测试采用最小合法结构样本，未在 Office for Mac 中逐项保存后验收。文档差异不覆盖完整 Office 渲染语义。常规保存事件不足以证明由本人输入；待确认项不会自动进入本人工作。

**模型摘要和 GitHub 发布。** 默认无模型模式已通过。没有调用真实 Ollama 或远程模型。发布脚本需要用户本机已有 gh 登录；在线 CI 状态以 GitHub Actions 的实际运行结果为准。

## 结果文件与复现

`examples/report.html`、`report.md` 和 `report-preview.png` 来自独立演示数据库，均明确标为演示；不会混入真实采集数据库。完整会话树和差异可以在示例 HTML 中展开。

本机验收次序及具体字段见 [LOCAL_CODEX_HANDOFF.md](LOCAL_CODEX_HANDOFF.md)。需要新增实机回归样本时，先脱敏，再加入 tests；不要提交真实对话、实验资料、token 或数据库。
