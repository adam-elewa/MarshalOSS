# Marshal extension points

Ingest workers should write into the same SQLite schema (`files`, `chunks`, `embeddings`, `chunks_fts`) so MCP/search/UI stay unchanged.

## PST / OST (not in v1)

Suggested worker:
1. `readpst` or `libratom` → explode to `.eml` / attachments under a staging folder
2. Run normal Docling ingest on the exploded tree
3. Set `files.rel_path` to a stable path under the case root (or a `pst/` prefix)

## Cellebrite / UFDR

Chats decoded in a UFDR's `report.xml` are readable today through the Forensics tab and the MCP tools
`cellebrite_chats`, `cellebrite_messages` and `cellebrite_search` (read straight from the UFDR cache, not the
document index). Other UFDR artifacts (call logs, locations, media, deleted items) are not surfaced. To put them
in the document index, a suggested worker:
1. Process with **IPED** (or UFED reader) offline
2. Export text items + metadata to JSONL
3. Insert rows into `files` / `chunks` with:
   - `bates` if available, else device/path label
   - `rel_path` pointing at exported natives if served from disk
4. Embed chunk texts with the same `Embedder`

No MCP tool changes required if `case_id` + chunk text + `rel_path` are populated.
