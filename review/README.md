# Review plane (Datashare)

Human **browse / keyword search / star / tag** sidecar for [MarshalOSS](../README.md).

Claude (via Marshal MCP) stays the ask-AI front end. This stack is for manual review.

Upstream: [ICIJ Datashare](https://github.com/ICIJ/datashare) (**AGPL-3.0**). This folder is compose + ops scripts only (Apache-2.0 with the rest of MarshalOSS).

## Quick start

```bash
cd review
cp .env.example .env
mkdir -p data/my-case
# copy documents into data/my-case/
docker compose up -d
./scripts/create-and-index.sh my-case my-case
```

UI: **http://localhost:8100/** (no login in `LOCAL` mode). Select project `my-case`, search.

Needs Docker. Elasticsearch **8.19.x** (Datashare 21.25 breaks on ES 7 for highlight).

## Customize

| Goal | How |
|------|-----|
| Themes / UI / tags UX | Fork [ICIJ/datashare](https://github.com/ICIJ/datashare) (AGPL — share your changes if you distribute/run modified server) |
| Port / passwords / data path | `.env` |
| OCR on | Index with `--ocr true` (edit `create-and-index.sh`) |
| NER people/orgs | Add NLP stage after INDEX (see Datashare docs) |
| Multi-user auth | Switch compose to `--mode SERVER` + auth (see Datashare server docs) |

## Ops notes

- Do **not** commit case files under `data/` (gitignored).
- Default Postgres password is for local demos — change in `.env` before any shared host.
