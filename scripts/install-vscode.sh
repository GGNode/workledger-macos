#!/bin/bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DEST="$HOME/.vscode/extensions/workledger-local.workledger-evidence-0.1.0"
mkdir -p "$DEST"
cp "$ROOT/extensions/vscode/package.json" "$ROOT/extensions/vscode/extension.js" "$DEST/"
echo '扩展已放入本机 VS Code 扩展目录。重启 VS Code，在设置中启用 workledger.enabled；它只采集 WorkLedger 已配置项目中的保存变化。'
echo '扩展无法证明物理输入者，归属请在控制面板确认。VS Code Insiders/Cursor/远程环境需按交接文档调整目录。'
