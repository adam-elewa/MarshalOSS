# Dual stack: MarshalOSS + Datashare

Same host can run both planes.

| Role | Product | Default URL |
|------|---------|-------------|
| MCP / hybrid search / Bates ingest | **MarshalOSS** | `http://<host>:7340/mcp` |
| File open URLs | MarshalOSS | `http://<host>:7340/` |
| Manual, human-led review | **Datashare** | `http://<host>:8100/` |

## Review plane

The stack is also meant to provide **basic review tools**: browsing, tagging, and annotating discovery. That work goes through **Datashare**. Defense-specific needs (coding UX, tags, annotation, privilege, layouts) should be met by **extending or revising Datashare**, not by building a second review product inside MarshalOSS.

Compose + index scripts live in [`review/`](../review/README.md).

```bash
cd review
cp .env.example .env
docker compose up -d
./scripts/create-and-index.sh <project> <subdir-under-data>
```

FreeEed / OpenContracts were earlier experiments; Datashare is the supported review plane.

## License

Both are AGPL-3.0:

- **MarshalOSS** (this repo): AGPL-3.0-or-later
- **Datashare** (upstream image / any fork of its code): AGPL-3.0

If you modify either and offer it as a network service, you must provide the corresponding source.
