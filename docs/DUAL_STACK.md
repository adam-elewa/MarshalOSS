# Dual stack: MarshalOSS + FreeEed

Same Tailscale host (`gideon`, `100.126.162.84`).

| Role | Product | URL |
|------|---------|-----|
| MCP / hybrid RAG | **MarshalOSS** | `http://100.126.162.84:7340/mcp` |
| Browser (Bates + search) | MarshalOSS | `http://100.126.162.84:7340/` |
| Human review (labels, search, produce) | **FreeEed** | `http://100.126.162.84:8090/freeeedui/` |

## FreeEed login

- User: `admin`
- Password: `admin`

## Start FreeEed services

```bash
~/Projects/FreeEed/start-tailscale.sh
```

Pack lives at `~/Projects/FreeEed/freeeed_complete_pack`. OpenContracts is parked (`docker compose stop`) after the FreeEed pivot.

## Smoke case

Project `washington` processes OPIA transcripts from `/mnt/marshal/cases/Washington-Jersey City/OPIA-transcripts` (subset under `freeeed_complete_pack/data/washington-opia`).
