# Stack layout

MarshalOSS is a single stack: ingest, search/MCP, and the native review UI on one host.

| Role | Surface | Default URL |
|------|---------|-------------|
| MCP for Cowork / Computer | MarshalOSS | `http://<host>:7340/mcp` |
| Case review UI | MarshalOSS web app | `http://<host>:7340/` |
| File open / download | MarshalOSS | `http://<host>:7340/files/…` · `/view/…` |
| Health / APIs | MarshalOSS | `http://<host>:7340/health` · `/api/…` |

Human browse, case overview, document categories, and the document viewer live in this repo (`web/app.html`). Specialized defense viewers (for example a synced bodycam timeline) should also be served from MarshalOSS, not a separate review product.

Do not expose the port on the public internet without auth; prefer Tailscale or a Cloudflare Tunnel. Optional helpers: [`tools/networking/`](../tools/networking/README.md).
