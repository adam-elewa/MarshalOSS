# MarshalOSS

Defense-discovery **ingest + hybrid search + MCP** stack.

**Docling** turns case files into text (PDF/OCR, Office, email, images, audio/video ASR).  
**MarshalOSS** stores Bates/path metadata, chunks + embeddings, serves Tailscale file URLs, and exposes tools to Claude Desktop / other MCP clients.

It is **not** a Relativity-style review/produce UI (use FreeEed or similar for coding). It is **not** a full LLM judge that re-OCRs failed pages with GPT/Claude vision.

---

## What it does

| Capability | Status |
|---|---|
| Multi-format ingest via Docling | Yes |
| Bates labels from path/filename regex | Yes |
| SQLite FTS5 keyword search | Yes |
| Semantic / hybrid search (RRF fuse) | Yes (Voyage or offline hash) |
| MCP tools for agents (`search`, `ask`, …) | Yes |
| Case file browser + open/download URLs | Yes |
| Dedup skip by content hash | Yes |
| Human review codes / production | No — see [docs/DUAL_STACK.md](docs/DUAL_STACK.md) |
| PST / Cellebrite UFDR workers | Not in v1 — [docs/EXTENSIONS.md](docs/EXTENSIONS.md) |
| AI OCR escalation on low confidence / garbage text | **Yes** (OpenAI or Anthropic vision) |

### Ingest pipeline

1. Walk case root for allowed extensions  
2. Skip unchanged files (`sha256` + `status=ready`) and oversized files  
3. Extract Bates from relative path  
4. **Docling** `DocumentConverter` → `HybridChunker` (page/heading provenance when available)  
5. Embed chunk texts → store in SQLite (`files`, `chunks`, `embeddings`, `chunks_fts`)  

### Search modes

- `keyword` — FTS5  
- `semantic` — cosine over stored embeddings  
- `hybrid` (default) — Reciprocal Rank Fusion of both  

### MCP tools

| Tool | Purpose |
|---|---|
| `list_cases` / `get_active_case` / `set_active_case` | Case selection |
| `browse_case_files` | Directory listing with Bates + URLs |
| `search` | hybrid / keyword / semantic hits + snippets + `open_url` |
| `ask` | Retrieve top hybrid chunks as RAG context (**client LLM** answers) |
| `get_file_link` / `open_file_browser` | Tailscale links |

`ask` does **not** call an LLM inside MarshalOSS — Claude (or another MCP host) reasons over the returned context.

---

## AI / API dependencies

### APIs

| Dependency | Role | Required? |
|---|---|---|
| **`VOYAGE_API_KEY`** | Voyage embeddings (`voyage-law-2`) | **Recommended** for semantic search. Without it → local **hash** embedder (weak). |
| **`OPENAI_API_KEY`** or **`ANTHROPIC_API_KEY`** | Vision LLM OCR when quality gates fire | **Recommended** if you want AI OCR escalation. Without either key, escalation is skipped (Docling text kept). |

```bash
export VOYAGE_API_KEY="…"
export OPENAI_API_KEY="…"          # preferred default for AI OCR (gpt-4o-mini)
# or
export ANTHROPIC_API_KEY="…"       # claude-haiku by default

# optional overrides
export MARSHALOSS_EMBED_BACKEND=voyage
export MARSHALOSS_OCR_ESCALATE=true
export MARSHALOSS_OCR_ESCALATE_PROVIDER=auto   # auto | openai | anthropic
export MARSHALOSS_OCR_ESCALATE_OPENAI_MODEL=gpt-4o-mini
export MARSHALOSS_OCR_ESCALATE_MAX_PAGES=25
```

Keys are also pulled from `~/.marshal/env.sh` when unset.

### Local (always)

| Dependency | Role |
|---|---|
| **Docling** (`docling[asr]`) | First-pass parse/OCR/ASR (local models; may download weights) |
| **pypdfium2** + **Pillow** | Render PDF/image pages for AI OCR |
| **Python 3.12+**, **uv** | Runtime |

### Not used for extraction by default

| API | Used? |
|---|---|
| OpenAI / Anthropic **chat** for RAG answers | **No** inside MarshalOSS — Claude Desktop (MCP client) reasons over `ask`/`search` hits |
| Whisper cloud / Google Vision / Azure DI | **No** |

---

## AI OCR escalation (quality gates)

First pass is always **Docling**. Then MarshalOSS scores the result and may escalate **only suspicious pages/files** to a vision model.

### What triggers escalation

| Signal | Example |
|---|---|
| Docling grades | `mean_grade` / `low_grade` in `{poor, fair}`; low `ocr_score` |
| Sparse text | Near-empty extract; very low chars/page or chars/MB on a large scan |
| Garbage OCR | Replacement chars (`�`), box-drawing junk, mojibake (`Ã©`, `â€™`) |
| Token salad | High ratio of symbol-only / consonant-hash tokens |
| Low letter density | Extract that barely looks like language |
| Docling hard-fail | PDF/image → try whole-doc AI OCR when Docling throws |

Fair Docling grade alone with otherwise healthy text does **not** escalate.

### Escalation behavior

1. Score Docling text + confidence (`quality.py`)  
2. If flagged and an API key exists → render bad pages (or whole doc if globally sparse) to PNG  
3. Vision OCR (`gpt-4o-mini` or Claude Haiku by default)  
4. Replace overlapping page chunks (or full doc) and embed the new text  
5. Store audit JSON on `files.meta` (`quality_score`, `reasons`, `escalated`, `ai_ocr_pages`, …)

Disable: `MARSHALOSS_OCR_ESCALATE=false`.  
Cost control: `MARSHALOSS_OCR_ESCALATE_MAX_PAGES` (default 25).

---

## Supported file types (default)

```
.pdf .docx .doc .pptx .ppt .xlsx .xls .csv .txt .md
.html .htm .eml .msg
.png .jpg .jpeg .tif .tiff .bmp .webp
.mp3 .wav .m4a .aac .ogg .flac
.mp4 .mov .avi .mkv .webm
```

Override with `MARSHALOSS_INGEST_EXTENSIONS`. Default max file size: **500 MB** (`MARSHALOSS_MAX_FILE_BYTES`).

**Not in v1:** PST/OST explosion, Cellebrite/UFDR — design notes in [docs/EXTENSIONS.md](docs/EXTENSIONS.md).

---

## Architecture

```
Case folder on disk
        │
        ▼
   Docling extract ──► chunks (+ pages/headings when available)
        │
        ▼
   Embedder (Voyage or hash)
        │
        ▼
   SQLite (~/.marshaloss/marshal_oss.db)
        │
        ├── FTS5 keyword
        ├── vector cosine
        └── hybrid RRF
        │
        ├── REST  /api/search  /api/files/list  /browser  /files/…
        └── MCP   /mcp  (streamable HTTP)
```

Data dir default: `~/.marshaloss/` (does not share the old `~/.marshal-lite` DB — re-ingest after rename, or point `MARSHALOSS_DB_PATH` / copy the DB).

---

## Quick start

```bash
export PATH="$HOME/.local/bin:$PATH"
cd ~/Projects/MarshalOSS

# Voyage key (recommended)
# source ~/.marshal/env.sh
# export VOYAGE_API_KEY=…

uv sync

uv run marshaloss ingest "/path/to/case/root" \
  --slug washington-jersey-city \
  --name "Washington v. Jersey City" \
  --limit 5

export MARSHALOSS_PUBLIC_BASE="http://$(tailscale ip -4):7340"
uv run marshaloss serve
```

| Surface | URL |
|---|---|
| Home | `http://<host>:7340/` |
| Browser | `http://<host>:7340/browser` |
| Health | `http://<host>:7340/health` |
| MCP | `http://<host>:7340/mcp` |

CLI search:

```bash
uv run marshaloss search "bodycam booking" --mode hybrid --top-k 8
```

### Claude Desktop (`mcp-remote`)

```json
"marshaloss": {
  "command": "npx",
  "args": [
    "-y",
    "mcp-remote",
    "http://<TAILSCALE_IP>:7340/mcp",
    "--allow-http",
    "--transport",
    "http-only"
  ]
}
```

---

## Configuration (env)

Prefix: `MARSHALOSS_`

| Variable | Default | Meaning |
|---|---|---|
| `DATA_DIR` | `~/.marshaloss` | SQLite + local data |
| `DB_PATH` | `$DATA_DIR/marshal_oss.db` | Override DB path |
| `HOST` / `PORT` | `0.0.0.0` / `7340` | Bind address |
| `PUBLIC_BASE` | (set to Tailscale URL) | Links returned to MCP/UI |
| `EMBED_BACKEND` | `voyage` | `voyage` or `hash` |
| `EMBED_MODEL` | `voyage-law-2` | Voyage model id |
| `VOYAGE_API_KEY` | — | Also accepts bare `VOYAGE_API_KEY` |
| `OCR_ESCALATE` | `true` | Enable AI vision OCR on quality fails |
| `OCR_ESCALATE_PROVIDER` | `auto` | `auto` / `openai` / `anthropic` |
| `OCR_ESCALATE_OPENAI_MODEL` | `gpt-4o-mini` | Vision model for OpenAI path |
| `OCR_ESCALATE_ANTHROPIC_MODEL` | `claude-haiku-4-5-…` | Vision model for Anthropic path |
| `OCR_ESCALATE_MAX_PAGES` | `25` | Cap pages sent to AI OCR per file |
| `OCR_ESCALATE_MIN_SCORE` | `0.72` | Escalate when score below this (with reasons) |
| `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` | — | Vision OCR credentials |
| `INGEST_EXTENSIONS` | (see above) | Comma-separated suffixes |
| `MAX_FILE_BYTES` | `524288000` | Skip larger files |
| `BATES_PATTERN` | built-in regex | Override Bates extraction |

---

## Dual stack (optional)

| Role | Tool |
|---|---|
| Semantic / MCP RAG | **MarshalOSS** |
| Human coding / produce | **FreeEed** (or other review UI) |

Details: [docs/DUAL_STACK.md](docs/DUAL_STACK.md).

---

## Repo

Private: [github.com/adam-elewa/MarshalOSS](https://github.com/adam-elewa/MarshalOSS)

MIT-oriented greenfield; Docling/Voyage are third-party — follow their licenses and API terms.
