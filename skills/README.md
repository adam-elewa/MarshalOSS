# Marshal skills

Single git folder for Cowork / MCP playbooks and notebook recipes.

| Name | File | Role |
|------|------|------|
| `cowork` | [cowork.md](cowork.md) | Core MCP loop + **preferred Notebook IA / sidebar icons** |
| `cross-examination` | [cross-examination.md](cross-examination.md) | Wiki exam prep under `People` / `Cross` / `Witnesses` |
| `timeline` | [timeline.md](timeline.md) | Chronology under top-level `Timeline` |

## Notebook structure (all skills)

Prefer stock top-level roots so the web sidebar shows bold labels + icons:  
`Home`, `Timeline`, `Witnesses`, `People`, `Cross`, `Theory`, `Motions`, `Discovery`, `Meta`.  
See **Notebook IA** in [cowork.md](cowork.md).

## Load via MCP

- `notebook_skill` — **lists every skill in this folder**
- `notebook_skill` with `name` — full Markdown for that skill
- `get_instructions` / `GET /api/mcp/instructions` — short pointer; `full=1` loads `cowork`

Mirrored into each case wiki under `Meta/Skills/*` when the notebook opens.
