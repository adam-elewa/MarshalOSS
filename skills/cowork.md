---
name: marshaloss-cowork
description: >-
  How Claude Cowork / Computer should use MarshalOSS MCP for defense discovery:
  search, pull text, cite, notebook, annotations. Discovery dump is read-only.
---

# MarshalOSS × Cowork / Computer

MarshalOSS indexes the production and exposes it over MCP. **You** (Cowork) answer and draft. Do not invent Bates, paths, quotes, or times.

## Hard rules

- **Discovery files are source of truth and read-only.** No create / delete / modify of files under the case root via MCP.
- **Writable only:** notebook wiki pages, per-file `user_note`, per-file `user_tags`.
- **Cite real paths** from tool results: `[[doc:rel/path|Label]]` in the notebook.

## Default loop

1. `get_active_case` (or `list_cases` → `set_active_case`)
2. `search` for structured hits, or `ask` when you want a packed `context` string
3. On a useful hit, `get_file(path=…)` for metadata + a text window  
   - Page with `offset_chars` while `has_more` if you need more  
   - Or `max_chars=0` for the remainder once  
   - Optional `chunk_offset` / `chunk_limit` for ingest chunks
4. Draft the answer in chat; persist lasting work with `notebook_upsert` and real `[[doc:]]` cites
5. Optional: `set_file_note` / `set_file_tags` on exhibits you reviewed

## Notebook recipes (optional)

Call `notebook_skill` with no name to list every skill in `skills/`, or with `name` = `cowork` / `cross-examination` / `timeline`. Same files are mirrored in the wiki at `Meta/Skills/*`.

## Anti-patterns

- Dumping every full file into context by default
- Writing “findings” with no `rel_path` / Bates from tools
- Trying to ingest or rewrite the production dump over MCP
