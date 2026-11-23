# Review plane (Datashare)

Basic human review for [MarshalOSS](../README.md): **browse, tag, and annotate** discovery (keyword search, stars, tags, and related UX).

Claude / Perplexity (via MarshalOSS MCP) stays the AI front end. This project is designed to ship those basic review tools **through Datashare**, not a new UI inside MarshalOSS. Defense-specific needs should be met by **extending or revising Datashare** (theme, tags, annotation, coding UX, auth, and so on).

Upstream: [ICIJ Datashare](https://github.com/ICIJ/datashare) (**AGPL-3.0**). This folder is compose + ops scripts only (AGPL-3.0 with the rest of MarshalOSS).

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


## Sync MarshalOSS text into Datashare

After indexing a case folder in Datashare, push MarshalOSS extracted text, tags, and entities into the Datashare index so search/browse uses the defense pipeline (not empty OCR):

```bash
python3 review/scripts/sync-from-marshaloss.py \
  --slug david-jones \
  --project david-jones \
  --es-via-docker datashare-elasticsearch-1
```

Match is by relative path under the project. Documents only in Datashare (player junk, uncovered formats) are left alone.

## Ops notes

- Do **not** commit case files under `data/` (gitignored).
- Default Postgres password is for local demos — change in `.env` before any shared host.
