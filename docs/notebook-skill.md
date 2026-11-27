---
name: marshal-notebook
description: >-
  Marshal notebook + Cowork skills. Use when working the case wiki, search,
  or MCP tools. Call notebook_skill (no name) for the full skill list under
  skills/; call with name to load one recipe.
disable-model-invocation: false
---

# Marshal skills

All playbooks live in the repo folder **`skills/`** (one place).

## MCP

| Call | Result |
|------|--------|
| `notebook_skill` | **Skill list** (every `skills/*.md` except README) |
| `notebook_skill` with `name` | Full Markdown (`cowork`, `cross-examination`, `timeline`, …) |
| `get_instructions` | Short pointer; `full=true` → `cowork` playbook |

Same files are mirrored in the wiki at `Meta/Skills/*`.

## Core loop (see `cowork`)

`search` / `ask` → `get_file` (page if needed) → `notebook_upsert` with `[[doc:]]`. Discovery is **read-only**. Writable: notebook, `user_note`, `user_tags`.
