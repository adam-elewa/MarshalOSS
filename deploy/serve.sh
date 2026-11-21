#!/usr/bin/env bash
set -euo pipefail
export PATH="$HOME/.local/bin:$PATH"
cd "$HOME/Projects/MarshalOSS"
source "$HOME/.marshal/env.sh" 2>/dev/null || true
export MARSHALOSS_PUBLIC_BASE="${MARSHALOSS_PUBLIC_BASE:-http://100.126.162.84:7340}"
exec uv run marshaloss serve --host 0.0.0.0 --port 7340
