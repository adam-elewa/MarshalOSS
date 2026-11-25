# MarshalOSS skills

Single git folder for Cowork / MCP playbooks and notebook recipes.

| Name | File | Role |
|------|------|------|
| `cowork` | [cowork.md](cowork.md) | Core MCP loop: search → get_file → notebook; discovery read-only |
| `cross-examination` | [cross-examination.md](cross-examination.md) | Wiki exam prep by person/entity with `[[doc:]]` cites |
| `timeline` | [timeline.md](timeline.md) | Chronology with who / when / discovery cites |

## Load via MCP

- `notebook_skill` — **lists every skill in this folder**
- `notebook_skill` with `name` — full Markdown for that skill
- `get_instructions` / `GET /api/mcp/instructions` — short pointer; `full=1` loads `cowork`

Mirrored into each case wiki under `Meta/Skills/*` when the notebook opens.
