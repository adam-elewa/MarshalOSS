#!/usr/bin/env bash
# Optional helper: suggest MARSHALOSS_PUBLIC_BASE for file/MCP links.
# Priority: existing env → CLOUDFLARE_TUNNEL_URL → Tailscale IPv4 → localhost.
# Deploy however you want; this project does not require any of these.
set -euo pipefail

port="${MARSHALOSS_PORT:-7340}"

if [[ -n "${MARSHALOSS_PUBLIC_BASE:-}" ]]; then
  printf '%s\n' "${MARSHALOSS_PUBLIC_BASE}"
  exit 0
fi

if [[ -n "${CLOUDFLARE_TUNNEL_URL:-}" ]]; then
  # Accept with or without scheme
  url="${CLOUDFLARE_TUNNEL_URL}"
  if [[ "${url}" != http://* && "${url}" != https://* ]]; then
    url="https://${url}"
  fi
  printf '%s\n' "${url%/}"
  exit 0
fi

if command -v tailscale >/dev/null 2>&1; then
  ts_ip="$(tailscale ip -4 2>/dev/null || true)"
  if [[ -n "${ts_ip}" ]]; then
    printf 'http://%s:%s\n' "${ts_ip}" "${port}"
    exit 0
  fi
fi

printf 'http://127.0.0.1:%s\n' "${port}"
