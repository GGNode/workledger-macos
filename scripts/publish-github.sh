#!/bin/bash
set -euo pipefail
# Run on the user's own Mac. Uses the user's existing gh OAuth session; no credentials in this project.
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OWNER="${WORKLEDGER_GITHUB_OWNER:-YOUR_GITHUB_ACCOUNT}"
NAME="${WORKLEDGER_GITHUB_REPO:-workledger-macos}"
command -v gh >/dev/null 2>&1 || { echo '需要 GitHub CLI。安装后运行 gh auth login，再重新运行本脚本。'; exit 1; }
command -v git >/dev/null 2>&1 || { echo '需要 git。'; exit 1; }
gh auth status >/dev/null
LOGIN="$(gh api user --jq .login)"
if [ "$LOGIN" != "$OWNER" ]; then echo "当前 gh 账号为 $LOGIN，目标账号为 $OWNER；已停止，未创建仓库。"; exit 1; fi
if gh repo view "$OWNER/$NAME" >/dev/null 2>&1; then
  echo "仓库 $OWNER/$NAME 已存在；本脚本不覆盖已有远端。请人工检查后推送，或设置 WORKLEDGER_GITHUB_REPO。"
  exit 1
fi
# Stage an allowlisted source copy, NEVER the entire working folder or personal reports.
STAGING="$(mktemp -d "${TMPDIR:-/tmp}/workledger-publish.XXXXXX")"
trap 'rm -rf "$STAGING"' EXIT
for file in workledger extensions scripts docs tests examples .github README.md AGENTS.md LICENSE pyproject.toml .gitignore Install.command Run.command Publish-to-GitHub.command; do
  if [ -e "$ROOT/$file" ]; then cp -R "$ROOT/$file" "$STAGING/"; fi
done
find "$STAGING" -type d -name __pycache__ -prune -exec rm -rf {} +
find "$STAGING" -type f \( -name '*.pyc' -o -name '*.db' -o -name '*.sqlite*' -o -name '.env*' -o -name 'token' \) -delete
cd "$STAGING"
git init -b main
git add .
git -c user.name="$LOGIN" -c user.email="$LOGIN@users.noreply.github.com" commit -m "Initial WorkLedger local-first macOS work report tool"
gh repo create "$OWNER/$NAME" --private --source . --remote origin --push --description 'Local-first macOS work reports with human/agent attribution, native session adapters and concise daily briefs'
printf '\n已创建并推送私有仓库：\n'
gh repo view "$OWNER/$NAME" --json url --jq .url
# Keep the downloaded source untouched; use gh repo clone for future work in a checkout.
