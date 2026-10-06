#!/bin/bash
set -euo pipefail
if [ -x "$HOME/.local/bin/workledger" ]; then
  exec "$HOME/.local/bin/workledger" report --open
else
  cd "$(dirname "$0")"
  exec bash scripts/install.sh
fi
