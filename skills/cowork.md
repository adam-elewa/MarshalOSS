---
name: marshal-cowork
description: >-
  How Claude Cowork / Computer should use Marshal MCP for defense discovery:
  search, pull text, cite, notebook, annotations. Discovery dump is read-only.
---

# Marshal × Cowork / Computer

Marshal indexes the production and exposes it over MCP. **You** (Cowork) answer and draft. Do not invent Bates, paths, quotes, or times.

## Hard rules

- **Discovery files are source of truth and read-only.** No create / delete / modify of files under the case root via MCP.
- **Writable only:** notebook wiki pages, per-file `user_note`, per-file `hot_doc` (`set_file_hot_doc` / `list_flagged_files`), per-file `user_tags`, optional forensics map pins.
- **Cite real paths** from tool results: `[[doc:rel/path|Label]]` in the notebook.

## Default loop

1. `get_active_case` (or `list_cases` → `set_active_case`)
2. `search` for structured hits, or `ask` when you want a packed `context` string
3. On a useful hit, `get_file(path=…)` for metadata + a text window  
   - At most 40,000 characters per call. If `truncated` is true, `notice` says where you stopped; call again with `offset_chars=next_offset_chars` while `has_more`  
   - For long exhibits, `search` first and read only the part you need  
   - Optional `chunk_offset` / `chunk_limit` (≤50) for ingest chunks with page / time metadata  
   - `.ufdr` and `.zip` have no text of their own — the error says what to use instead
4. Draft the answer in chat; persist lasting work with `notebook_upsert` and real `[[doc:]]` cites
5. Optional: `set_file_note` / `set_file_hot_doc` / `set_file_tags` on exhibits you reviewed (`list_flagged_files` to see hot docs + notes)

## Forensics / CSLI (pull data — you analyze)

Do **not** wait for a specialized “near scene” tool. Pull rows and reason:

1. `forensics_overview` — what CSLI / UFDR sources exist  
2. `csli_events` — filter by `phone`, `day`, `start`/`end`, page with `offset`  
3. Optional `forensics_search` for keyword discovery; `map_pin_*` only if you want a map marker  

`csli_events` returns at most 100 rows per call — follow `next_offset` while `has_more`.

## Phone chats (Cellebrite UFDR)

`search` / `ask` do **not** cover phone messages. Use:

1. `cellebrite_chats` — conversations per phone (`path` from `forensics_overview`, or omit for all phones); filter by `app`, `query` (name / number), `date_from` / `date_to`
2. `cellebrite_messages(path, chat_id)` — the thread, oldest first, with sender and UTC + local time; page with `offset`
3. `cellebrite_search(query)` — words (all must match), `"exact phrase"`, or a phone number across every phone; open context with `cellebrite_messages(..., offset=max(0, index-10), limit=25)`

Dates are whole days in the case timezone (UTC if none is set — the response says which; pass `tz` to override). "Me" is the phone's owner. A UFDR holds only what Cellebrite decoded for that export, not the whole phone.

Respect `source_tz` (AT&T ReportAU is UTC). Coordinates are **serving tower/sector**, not handset GPS. Convert timezones, measure distances, compare to any address yourself.

## Notebook recipes (optional)

Call `notebook_skill` with no name to list every skill in `skills/`, or with `name` = `cowork` / `cross-examination` / `timeline`. Same files are mirrored in the wiki at `Meta/Skills/*`.

## Notebook IA (prefer this going forward)

The web Notebook sidebar **bolds top-level sections** and shows small icons for stock roots. Structure new case wikis this way so the UI stays clear:

| Top-level path | Use for | UI icon |
|----------------|---------|---------|
| `Home` | Case landing + links out | house |
| `Timeline` | Chronology index + day pages | clock |
| `Witnesses` | Witness hubs / packs | people |
| `People` | Person/entity hubs (exam prep) | person |
| `Cross` | Cross-exam outlines / binders | stand |
| `Theory` | Defense theory / elements | (bookmark) |
| `Motions` | Motion binders | (folder) |
| `Discovery` | Discovery disputes / logs | (stack) |
| `Meta` | Skills, playbooks, chrome | gear |

**Rules for AI scaffolding**

1. Prefer these **exact** top-level names (Title Case, single segment) so icons light up. Do not invent `timelines/`, `witness_folders/`, etc.
2. Put detail under children: `Timeline/2024-08-09 …`, `People/{Name}/Cross outline`, `Motions/Suppress/…`.
3. Always keep a short link from `Home` to each top-level area you create (append; don’t wipe human notes).
4. New one-off areas are fine (`Experts`, `Investigators`, …) — still use a single Title Case root; unknown roots render bold without an icon until the app map is extended.
5. Load `timeline` / `cross-examination` skills for recipes; both assume this IA.

## Anti-patterns

- Dumping every full file into context by default
- Writing “findings” with no `rel_path` / Bates from tools
- Trying to ingest or rewrite the production dump over MCP
