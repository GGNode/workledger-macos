#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
bash scripts/publish-github.sh
