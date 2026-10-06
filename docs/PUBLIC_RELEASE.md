# 公开发行与持续集成

WorkLedger 是本地优先的工作简报工具。公开仓库仅包含程序、合成测试、演示和通用文档，不包含部署者的电脑环境清单、工作日志、报告、配对码或设置。`examples/` 明确标记为演示。

## 开发检查

```sh
python3 -m unittest discover -s tests -v
npm ci --prefix tests --ignore-scripts --no-audit --no-fund
node --test tests/*.test.js
```

Node 22 用于 CI。jsdom 仅为开发依赖，由 lockfile 固定；不安装它时 DOM 回归测试会 skip。CI 安装它，因此必须实际执行 DOM 回归。

`.github/workflows/tests.yml` 在 push、pull_request 和手动运行时，执行 Linux/macOS × Python 3.11/3.12/3.13 的测试矩阵、Node 测试、编译、脚本语法和演示生成。Actions 使用固定提交 SHA，令牌为 contents:read，不读取个人设备数据，不需要任何仓库 secret。

## Actions 配置

在仓库 Settings → Actions → General 启用 Actions。工作流只使用官方 actions/checkout、setup-python、setup-node，保留默认只读令牌权限。不要为测试开启写权限、PR 审批权限或自托管运行器。

若 gh 的 OAuth 凭据不能推送 workflow，可通过 `gh auth refresh -h github.com -s workflow` 补充 workflow scope（浏览器需账户本人确认），或使用本来就有相应权限的认证方式。不要将 token 提交到仓库。上传后必须查看实际运行结果，文件存在不代表 CI 已通过。

## 本机升级

保留 WorkLedger 数据目录，在源码目录执行 `bash scripts/install.sh`。可通过 `WORKLEDGER_PYTHON` 指定已有 Python/虚拟环境；安装器保留 config、SQLite、token 和 reports。重新加载本地浏览器扩展与需要采集的页面；重新加载 VS Code 后编辑器扩展生效。

默认自动日报关闭、后台采集开启。Pi 等日志不存在、旧版 dsh packed 格式、超过大小限制的日志应明确显示缺口。合成回归、解析成功、实际人工保存与登录/休眠场景是不同的验收边界。
