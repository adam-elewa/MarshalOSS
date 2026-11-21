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
| AI escalation when OCR fails | **No** (see below) |

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

### Required for good semantic search (recommended)

| Dependency | Role | Required? |
|---|---|---|
| **`VOYAGE_API_KEY`** | Voyage embeddings (`voyage-law-2` by default) | **Recommended.** Without it, MarshalOSS falls back to a local **hash** embedder (deterministic, weak semantic quality). |

Set via environment or `~/.marshal/env.sh` (auto-sourced by the CLI if `VOYAGE_API_KEY` is unset):

```bash
export VOYAGE_API_KEY="…"
# or
export MARSHALOSS_VOYAGE_API_KEY="…"
export MARSHALOSS_EMBED_BACKEND=voyage   # default
export MARSHALOSS_EMBED_MODEL=voyage-law-2
```

Offline / no-key mode:

```bash
export MARSHALOSS_EMBED_BACKEND=hash
```

### Local (no cloud API for OCR)

| Dependency | Role |
|---|---|
| **Docling** (`docling[asr]`) | Local parse/OCR/ASR. May download model weights on first run (layout, OCR engines, etc.). |
| **Python 3.12+**, **uv** | Runtime / package management |

Docling’s OCR is **local** (e.g. EasyOCR / Tesseract-class engines via Docling). MarshalOSS does **not** call OpenAI, Anthropic, Google Vision, or Azure Document Intelligence for extraction.

### Not required by MarshalOSS itself

| API | Used? |
|---|---|
| OpenAI / Anthropic / Gemini | **No** for ingest or search. Use them only as the **MCP client** (e.g. Claude Desktop) that calls `ask`/`search`. |
| Whisper / cloud ASR | **No** — ASR goes through Docling’s local ASR extras when audio/video are ingested. |

---

## OCR failure behavior (important)

MarshalOSS does **not** run an “AI escalation” ladder (no VLM second pass, no GPT-4o vision retry, no human-in-the-loop OCR queue).

On extract:

1. Docling convert + hybrid chunk  
2. If chunking fails → export markdown/text and plain-chunk  
3. If still empty → try markdown export again  
4. If Docling throws entirely → only `.txt` / `.md` / `.csv` / `.log` fall back to raw UTF-8 read; **all other types are marked `error`** and skipped for search  

So: **good Docling OCR succeeds → searchable; hard failures stay errors.** Improve bad OCR by fixing Docling settings/models upstream or re-processing natives — not via an in-app LLM OCR escalate.

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
