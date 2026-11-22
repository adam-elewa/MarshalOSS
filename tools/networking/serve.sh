#!/usr/bin/env bash
# Optional: start MarshalOSS with a resolved PUBLIC_BASE.
# Networking/VPN/tunnel setup is outside this repo — see README.md in this folder.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "${ROOT}"

export PATH="${HOME}/.local/bin:${PATH}"
# shellcheck disable=SC1091
source "${HOME}/.marshal/env.sh" 2>/dev/null || true

HOST="${MARSHALOSS_HOST:-0.0.0.0}"
PORT="${MARSHALOSS_PORT:-7340}"
export MARSHALOSS_PORT="${PORT}"

if [[ -z "${MARSHALOSS_PUBLIC_BASE:-}" ]]; then
  # shellcheck disable=SC1091
  export MARSHALOSS_PUBLIC_BASE="$("$(dirname "$0")/public_base.sh")"
fi

echo "PUBLIC_BASE=${MARSHALOSS_PUBLIC_BASE}"
echo "Listening on ${HOST}:${PORT}"
exec uv run marshaloss serve --host "${HOST}" --port "${PORT}"
