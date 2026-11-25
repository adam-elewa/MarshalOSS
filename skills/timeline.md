---
name: timeline
description: >-
  Build a Notebook timeline with direct discovery cites, organized with an
  emphasis on people/entities involved in each event. Use for chronology,
  incident reconstruction, or “what happened when” wiki sections.
---

# Skill: Timeline (chrono + entity)

Build a **case chronology** in the wiki. Every timed fact should cite discovery. Prefer noting **who** (person/entity) is involved so the timeline stays usable next to cross-exam pages.

## Goal

- A top-level `Timeline` index (chronological)
- Optional deep pages `Timeline/YYYY-MM-DD short title` for busy days
- Each bullet: **when · who · what · `[[doc:…]]` cite**
- Cross-links to person hubs when they exist (`[[page:People/Name|Name]]`)

In the UI, `[[doc:]]` opens the production file beside the notebook.

## MCP workflow

1. `notebook_list` — avoid colliding with an existing Timeline.
2. Pull times and actors from `search` / `ask` / media segments / reports — **verify** paths.
3. Upsert `Timeline` as the index (sorted ascending by time).
4. For dense spans, upsert child pages and link them from the index.
5. Where a person recurs, link their hub page; do not paste their whole cross outline into the timeline.
6. Append a Home link to `Timeline`.

## Page shape (example)

```markdown
# Timeline

| When | Who | What | Source |
|------|-----|------|--------|
| 2024-08-09 15:05 | Officer A | Arrives on scene | [[doc:path/body.mp4\|Bodycam start]] |
| 2024-08-09 15:22 | Jane Doe | Statement at door | [[doc:path/report.pdf\|Report]] |

## By person
- [[page:People/Jane Doe|Jane Doe]] — see cross outline for exam use
```

Deep page:

```markdown
# 2024-08-09 — Arrival

## 15:05–15:20
- Officer A bodycam rolling → [[doc:…|…]]
- Radio: … → [[doc:…|…]]

## Entities touched
- [[page:People/Officer A|Officer A]]
- [[page:People/Jane Doe|Jane Doe]]
```

## Rules

- **Time + entity + cite** on every material fact.
- Unknown times: use `_undated_` section at the bottom; still require a doc link.
- Do not invent clock times from filenames unless the file metadata/transcript supports them.
- Prefer tables or tight bullets; link out for long quotes.
- When refreshing, merge — do not erase human chronology notes.
