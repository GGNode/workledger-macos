#!/bin/bash
set -euo pipefail
if [ -x "$HOME/.local/bin/workledger" ]; then "$HOME/.local/bin/workledger" service uninstall || true; fi
rm -f "$HOME/.local/bin/workledger"
printf '%s\n' '已移除后台登录任务与命令入口。原始记录、报告、配置和 Applications/WorkLedger 入口均保留，未删除个人数据。'
