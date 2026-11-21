# Dual stack: MarshalOSS + Datashare

Same host can run both planes.

| Role | Product | Default URL |
|------|---------|-------------|
| MCP / hybrid RAG / Bates ingest | **MarshalOSS** | `http://<host>:7340/mcp` |
| File open URLs | MarshalOSS | `http://<host>:7340/` |
| Human review (browse, keyword, stars, tags) | **Datashare** | `http://<host>:8100/` |

## Review plane

Compose + index scripts live in [`review/`](../review/README.md).

```bash
cd review
cp .env.example .env
docker compose up -d
./scripts/create-and-index.sh <project> <subdir-under-data>
```

FreeEed / OpenContracts were earlier experiments; Datashare is the supported review plane.

## License split

- **MarshalOSS** (this repo): Apache-2.0  
- **Datashare** (upstream image / any fork of its code): AGPL-3.0 — if you modify Datashare and offer it as a network service, you must provide corresponding source.
