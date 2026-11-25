---
name: cross-examination
description: >-
  Build Notebook cross-examination pages organized by person/entity, with
  direct [[doc:]] cites and links into discovery. Use when the user asks for
  cross outlines, witness folders, impeachment binders, or exam prep in the wiki.
---

# Skill: Cross-examination (by person / entity)

The Notebook is a **blank wiki**. This recipe only scaffolds when the user wants cross-exam prep. Prefer a tree keyed by **people and entities**, not a dump of every document.

## Goal

One area of the wiki where each important **person** (or entity) has:

1. A short hub page (who they are, role, mention weight)
2. A **Cross outline** with questions
3. Every question or claim tied to a real discovery file via `[[doc:rel/path|label]]`

In the UI, clicking a `[[doc:]]` link opens that file in a **side viewer** next to the notebook.

## MCP workflow

1. `notebook_list` — see what already exists; do not duplicate trees.
2. `search` / `ask` / `browse_case_files` / entity facets — gather **verified** `rel_path` values and Bates-like labels.
3. Upsert an index, e.g. `Cross/` or `Witnesses/` (ask the user which name they prefer).
4. For each person/entity worth examining:
   - `People/{Name}` or `Witnesses/{Name}` — hub + list of linked docs
   - `People/{Name}/Cross outline` — checklist questions with `[[doc:]]` beside each item
5. Append a short link from `Home` to the new index (do not wipe human notes).
6. Optional helper: `notebook_build_witness_pack` can seed a stock `Witnesses/*` tree from person entities; still verify paths and rewrite outlines to match the user’s theory.

## Page shape (example)

```markdown
# Cross outline — Jane Doe

Role: civilian witness / 911 caller (confirm from discovery).

## Themes
- Prior inconsistent statement
- Opportunity to observe
- Bias / relationship

## Questions
1. [ ] You told Officer X that … → [[doc:path/to/report.pdf|Arrest report p.3]]
2. [ ] On bodycam you said … → [[doc:path/to/clip.mp4|Bodycam 15:22]]
```

## Rules

- **Organize by entity/person first**, then by theme under that person.
- **Never invent** file paths, Bates, timestamps, or quotes — pull from search/chunks.
- Prefer many small linked pages over one giant outline.
- Keep human edits when refreshing: read with `notebook_get`, merge, then upsert.
- Skip junk names (`Speaker A`, bare ranks) unless the user wants them.
- Deduplicate the same person under one canonical spelling (first + last).
