#!/usr/bin/env bash
# Deprecated path — use tools/networking/serve.sh
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
exec "${ROOT}/tools/networking/serve.sh" "$@"
