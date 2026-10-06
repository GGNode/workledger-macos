#!/bin/bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
HOME_DIR="${WORKLEDGER_HOME:-$HOME/Library/Application Support/WorkLedger}"
PY="${WORKLEDGER_PYTHON:-}"
if [ -z "$PY" ]; then
  for candidate in /opt/homebrew/bin/python3 /usr/local/bin/python3 python3; do
    if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c 'import sys;sys.exit(sys.version_info<(3,11))' 2>/dev/null; then PY="$(command -v "$candidate")"; break; fi
  done
fi
if [ -z "$PY" ] || ! "$PY" -c 'import sys;sys.exit(sys.version_info<(3,11))'; then
  echo '需要 Python 3.11 或更高版本。可使用已有的 Homebrew / Conda Python。'
  echo '例如：WORKLEDGER_PYTHON=/实际路径/python3 bash scripts/install.sh'
  exit 1
fi
export WORKLEDGER_HOME="$HOME_DIR"
"$PY" - "$ROOT" "$HOME_DIR" "$PY" <<'PY'
import os,shlex,shutil,sys
from pathlib import Path
root,home,python=map(Path,sys.argv[1:])
home.mkdir(parents=True,exist_ok=True,mode=0o700)
app=home/'app';app.mkdir(exist_ok=True)
# Install source only; preserve all collected data and settings on upgrades.
for name in ('workledger','extensions','docs','scripts'):
    source=root/name;target=app/name
    if source.resolve()==target.resolve():continue
    temp=app/(name+'.installing')
    if temp.exists():shutil.rmtree(temp)
    shutil.copytree(source,temp,ignore=shutil.ignore_patterns('__pycache__','*.pyc','node_modules'))
    if target.exists():shutil.rmtree(target)
    temp.rename(target)
bin=Path.home()/'.local/bin';bin.mkdir(parents=True,exist_ok=True)
command=bin/'workledger'
command.write_text('#!/bin/sh\nexport PYTHONPATH='+shlex.quote(str(app))+'${PYTHONPATH:+:$PYTHONPATH}\nexec '+shlex.quote(str(python))+' -m workledger "$@"\n')
command.chmod(0o755)
# User-facing Finder launcher; a copied shell entrypoint doesn't require Apple signing.
launchers=Path.home()/'Applications/WorkLedger';launchers.mkdir(parents=True,exist_ok=True)
for title,args in [('打开控制面板','ui'),('生成今日简报','report --open')]:
    p=launchers/(title+'.command')
    p.write_text('#!/bin/sh\nexec '+shlex.quote(str(command))+' --home '+shlex.quote(str(home))+' '+args+'\n')
    p.chmod(0o755)
print('安装完成：',command,'\nFinder 入口：',launchers)
PY
"$HOME/.local/bin/workledger" --home "$HOME_DIR" init
if [ "$(uname -s)" = Darwin ]; then
  "$HOME/.local/bin/workledger" --home "$HOME_DIR" service install --executable "$HOME/.local/bin/workledger"
  "$HOME/.local/bin/workledger" --home "$HOME_DIR" ui
else
  echo "当前不是 macOS；核心已安装，但未注册 launchd 或打开 Finder。"
fi
