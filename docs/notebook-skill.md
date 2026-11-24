---
name: marshal-notebook
description: >-
  Build and edit the MarshalOSS case Notebook — a dynamic Markdown wiki for
  defense prep (witnesses, timeline, theory, motions, anything). Use when the
  user mentions notebook, wiki, witness folders, cross outline, timeline,
  defense theory, court binder, or asks Cowork/Computer to create or revise
  case notebook pages via MCP.
disable-model-invocation: false
---

# MarshalOSS Notebook (dynamic case wiki)

The Notebook is a **blank wiki**. Do not assume a fixed IA. Humans and AI invent folders/pages as the case needs: witnesses, timeline, defense theory, discovery disputes, experts, motions — anything.

Storage is case-scoped Markdown pages via MarshalOSS MCP (or the web UI). You shape the tree; the app only stores and renders it.

## When this skill applies

- “Make witness folders / cross outlines”
- “Build a timeline / defense theory / binder section”
- “Update the notebook with …”
- Any request to organize case thinking as linked pages

## MCP tools (preferred)

| Tool | Use |
|------|-----|
| `notebook_list` | See existing paths before inventing duplicates |
| `notebook_get` | Read a page (always read before large edits) |
| `notebook_upsert` | Create/overwrite a page (`path`, `title`, `body`) |
| `notebook_delete` | Remove a page |
| `notebook_build_witness_pack` | **Optional recipe only** — scaffolds Witnesses/* from person entities. Prefer hand-crafted structure when the user wants something custom. |
| `search` / `ask` / `browse_case_files` / `get_file_link` | Ground pages in the production |

Also: `notebook_skill` returns this playbook text if you need to re-load it mid-session.

## Wiki link syntax (required)

Use these in Markdown bodies so the UI can jump:

- Document in the production: `[[doc:relative/path.pdf\|Label]]`
- Another notebook page: `[[page:Folder/Page title\|Label]]`

Never invent Bates or paths — take them from `search`, `ask`, `browse_case_files`, or entity/file facet results.

## How to build (every time)

1. **`notebook_list`** — see what already exists.
2. **Decide structure from the user request**, not from this skill’s examples. Examples below are inspiration only.
3. **Upsert an index page** for the new area (e.g. `Timeline`, `Theory`, `Witnesses`) with links to children.
4. **Upsert child pages** with real `[[doc:…]]` links from search/entity hits.
5. **Update `Home`** with a short link to the new area (append; don’t wipe human notes).
6. Tell the user the paths you created and how to open Notebook in the UI.

## Dynamic structure examples (not mandatory)

User: “witness folders and cross outlines”  
→ `Witnesses/` index + `Witnesses/{Name}/` + `Witnesses/{Name}/Cross outline` with docs linked inline under questions.

User: “case timeline”  
→ `Timeline` (chrono list) + optional `Timeline/YYYY-MM-DD …` deep pages; link exhibits.

User: “defense theory”  
→ `Theory` with elements, facts supporting/undermining, linked discovery.

User: “motion to suppress binder”  
→ `Motions/Suppress/` with facts, law bullets, exhibit list.

Reuse `notebook_build_witness_pack` only when the user wants the stock witness recipe quickly.

## Editing rules

- Paths are hierarchical strings: `Area/Subarea/Page` (no leading slash).
- Prefer **upsert** of specific pages over rewriting the whole wiki.
- Preserve human prose when refreshing AI sections — read first; merge.
- Cross outlines and theories stay editable stubs: checkboxes, questions, tables OK.
- Deduplicate people (same first+last) before creating witness pages.
- Skip junk names (`Speaker A`, bare ranks) unless the user wants them.

## UI

MarshalOSS web: `#/cases/{id}?view=notebook&page=…`  
Humans edit the same Markdown; AI should not fight the editor — write clean Markdown.

## Anti-patterns

- Hard-coding only witnesses forever — the wiki must stay open-ended.
- Giant one-page dumps — use a tree and indexes.
- Links without verifying the file path exists in the case.
- Recreating the full skill prompt in chat — load this skill / `notebook_skill` instead.
