# Networking helpers (optional)

MarshalOSS does **not** own how you expose the server. Run it on localhost, a LAN, Tailscale, a Cloudflare Tunnel, a VPN, or any other path you trust. These scripts are optional helpers only.

## Recommended for remote access

Do not put `:7340` on the public internet without auth. Prefer:

| Option | Notes |
|---|---|
| **Local only** | `http://127.0.0.1:7340` — fine for one machine |
| **Tailscale** | Private mesh; MCP clients use the Tailscale IP/hostname |
| **Cloudflare Tunnel** | Outbound tunnel; no open inbound ports |

Set `MARSHALOSS_PUBLIC_BASE` to whatever URL clients (Cowork / Computer / browser) should use for open/download links.

## Scripts

| Script | Purpose |
|---|---|
| [`public_base.sh`](public_base.sh) | Print a suggested base URL (env → Cloudflare → Tailscale → localhost) |
| [`serve.sh`](serve.sh) | Start `marshaloss serve` with that base URL if unset |

Examples:

```bash
# Local
./tools/networking/serve.sh

# Explicit
export MARSHALOSS_PUBLIC_BASE="https://marshal.example.com"
./tools/networking/serve.sh

# Cloudflare Tunnel URL (if you already run cloudflared separately)
export CLOUDFLARE_TUNNEL_URL="https://marshal.yourdomain.com"
./tools/networking/serve.sh
```

Tailscale / Cloudflare install and auth stay outside this project.
