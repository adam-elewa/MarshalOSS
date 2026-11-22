# Review plane (Datashare)

Manual, human-led review for [MarshalOSS](../README.md): browse, keyword search, stars, tags.

Claude / Perplexity (via MarshalOSS MCP) stays the AI front end. **Do not reinvent a review UI in MarshalOSS.** Defense-specific review needs should be met by **extending or revising Datashare** (theme, tags, coding UX, auth, and so on).

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

## Theme

`plugins/marshal-theme` injects the Marshal minimal-white palette (warm off-white, forest accent) over Datashare. Mounted automatically via compose.

## Customize (extend Datashare, don’t rebuild it)

| Goal | How |
|------|-----|
| Themes / UI / tags / coding UX for defense | Fork or patch [ICIJ/datashare](https://github.com/ICIJ/datashare) (AGPL — share changes if you distribute/run a modified server) |
| Port / passwords / data path | `.env` |
| OCR on | Index with `--ocr true` (edit `create-and-index.sh`) |
| NER people/orgs | Add NLP stage after INDEX (see Datashare docs) |
| Multi-user auth | Switch compose to `--mode SERVER` + auth (see Datashare server docs) |

## Ops notes

- Do **not** commit case files under `data/` (gitignored).
- Default Postgres password is for local demos — change in `.env` before any shared host.
