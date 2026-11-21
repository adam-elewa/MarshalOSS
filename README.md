# MarshalOSS

Defense-discovery **ingest + hybrid search + MCP** stack.

**Docling** turns case files into text (PDF/OCR, Office, email, images, audio/video ASR).  
**MarshalOSS** stores Bates/path metadata, chunks + embeddings, serves Tailscale file URLs, and exposes tools to Claude Desktop / other MCP clients.

Human review (browse, keyword, stars/tags) is the **Datashare** plane in [`review/`](review/README.md). It is **not** a full LLM judge that re-OCRs failed pages with GPT/Claude vision.

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
| Human review (browse / keyword / tags) | **Datashare sidecar** — [review/](review/README.md), [docs/DUAL_STACK.md](docs/DUAL_STACK.md) |
| PST / Cellebrite UFDR workers | Not in v1 — [docs/EXTENSIONS.md](docs/EXTENSIONS.md) |
| AI OCR escalation on low confidence / garbage text | **Yes** (OpenAI or Anthropic vision) |
| ASR escalation on poor Docling audio/video transcripts | **Yes** (ffmpeg + OpenAI `gpt-4o-transcribe`) |
| Visual captions for images + sampled video frames | **Yes** (late pass; same vision keys as OCR escalate) |

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

# ASR escalate (audio/video) — needs ffmpeg on PATH + OPENAI_API_KEY
export MARSHALOSS_ASR_ESCALATE=true
export MARSHALOSS_ASR_ESCALATE_MODEL=gpt-4o-transcribe
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
| Google Vision / Azure DI | **No** |
| OpenAI Whisper / `gpt-4o-transcribe` | **Only on ASR escalate** (poor Docling A/V transcript) |

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

## ASR escalation (audio / video)

First pass is **Docling local ASR**. Then MarshalOSS scores the transcript and may escalate **suspicious media** to OpenAI (same idea as OCR escalate).

### What triggers ASR escalate

| Signal | Example |
|---|---|
| Near-empty transcript | Tiny/empty extract from a large/long clip |
| Hallucination phrases | “thanks for watching”, subscribe spam, music-only junk |
| Repetitive / looped text | ASR stuck repeating a n-gram |
| Sparse vs duration / filesize | Few characters per minute of audio or per MB |

### Escalate behavior

1. Score Docling transcript (`score_transcript` in `quality.py`)  
2. If flagged and `OPENAI_API_KEY` is set → **ffmpeg** 16 kHz mono → **`gpt-4o-transcribe`** (chunked for long files)  
3. Replace the Docling transcript chunks and embed  
4. Store audit fields on `files.meta` (`escalated`, `escalate_kind=asr`, `quality_*`, …)

Requires **ffmpeg** / **ffprobe** on `PATH`.  
Disable: `MARSHALOSS_ASR_ESCALATE=false`.

This is a streamlined version of legacy Marshal’s HQ BWC pipeline (no diarize/verify loop in v1).

---

## Visual analyze (images + video frames)

Late ingest pass (after OCR/ASR). Makes photos and BWC/surveillance **visually searchable** without replacing transcript/OCR text.

| Input | Behavior |
|---|---|
| Still (`.jpg` `.png` `.tif` …) | Vision caption if OCR text is thin (`< visual_skip_if_ocr_chars`) |
| Video | ffmpeg samples a frame every N seconds → vision caption per frame (`Visual @m:ss`) |

Captions are embedded + FTS’d like any other chunk. Uses the same OpenAI/Anthropic vision keys as OCR escalate.

```bash
export MARSHALOSS_VISUAL_ANALYZE=true
export MARSHALOSS_VISUAL_FRAME_INTERVAL_SEC=5
export MARSHALOSS_VISUAL_MAX_FRAMES=48   # ~4 min coverage at 5s
```

Disable: `MARSHALOSS_VISUAL_ANALYZE=false`.

---

## Prepare workflow (unzip → hashed production manifest)

First step of ingestion — what was produced, before Docling:

```bash
uv run marshaloss prepare "/path/to/case/root" \
  --slug washington-jersey-city \
  --name "Washington v. Jersey City"
```

1. Expand production `*.zip` → sibling `<name>__unzipped/` (stdlib `zipfile`, zip-slip safe)  
2. Walk **every** file, full **SHA-256**, Bates guess, kind (`evidence` / `unzipped` / `container` / `loadfile` / `junk`)  
3. Write UX-ready artifacts:
   - `<root>/.marshaloss/manifest.jsonl` — one record per file  
   - `<root>/.marshaloss/manifest_summary.json` — counts / bytes / by_ext / by_kind  
4. Upsert SQLite `production_manifest` + serve via `/api/manifest` and `/api/manifest/summary`

`ingest` runs **prepare** automatically first. Disable zip expand: `MARSHALOSS_EXPAND_ZIPS=false`.

### Ingest confidence (covered + quality)

After ingest, each manifest row gets:

| Field | Meaning |
|---|---|
| `ingest_status` | `ready` / `error` / `skipped` / `pending` / `skipped_not_covered` |
| `quality_score` | 0–1 extraction confidence |
| `quality_reasons` | why score is low |
| `chunk_count` | searchable chunks written |
| `escalated` | OCR/ASR escalate fired |
| `entities` / `tags` | optional file-level AI extract |

UX endpoints: `/api/manifest`, `/api/manifest/summary` (includes `ingest_complete`, `avg_quality`, `low_quality_count`).  
Disk: `<root>/.marshaloss/ingest_confidence.json`.

---

## Entities & tags (full-file, cheap models)

**Not** per-chunk search indexing. Last pass over **all extracted text** for each file (`gpt-4o-mini` / Haiku):

1. Fits in one window → single call with **canonicalization**  
   (`Jones` / `D. Jones` / `David Jones` → `canonical: "David Jones"`, `aliases: [...]`)
2. Longer → map over ~14k-char segments → one **reduce** that merges + canonicalizes

Stored on `files.meta` / manifest as `{type, canonical, aliases, note}` + `tags`.

Cost (mini/Haiku): short files ≈ one call; a long PDF ≈ map_segments + 1. David Jones–scale still typically **well under $1** unless many huge transcripts. Set `MARSHALOSS_ENTITY_EXTRACT_MAX_CHARS` only if you need a safety ceiling (`0` = all text).

```bash
export MARSHALOSS_ENTITY_EXTRACT=true
# export MARSHALOSS_ENTITY_EXTRACT=false
# export MARSHALOSS_ENTITY_EXTRACT_SEGMENT_CHARS=14000
# export MARSHALOSS_ENTITY_EXTRACT_MAX_CHARS=0
```

---

## Media bridge (ffmpeg, not custom demuxers)

Docling ASR natively covers wav/mp3/m4a/… and mp4/mov/mkv/…. Large productions also ship **surveillance/DVR** types. MarshalOSS does **not** invent parsers — it uses **ffmpeg/ffprobe** (already required for ASR escalate):

1. ffprobe must open the file  
2. ffmpeg → 16 kHz mono WAV  
3. Docling ASR (or ASR escalate) on that WAV  

| Bridge (ffmpeg) | Examples |
|---|---|
| Yes | `.dav` `.wmv` `.wma` `.flv` `.asf` `.ts` `.m4v` `.mpeg` `.3gp` `.caf` `.vob` `.h264` |
| No (vendor / skip) | `.av3` `.g64x` `.v22` `.v21` `.vac` — need vendor players |

---

## Supported file types (default)

```
.pdf .docx .doc .pptx .ppt .xlsx .xls .csv .txt .md
.html .htm .eml .msg
.png .jpg .jpeg .tif .tiff .bmp .webp
.mp3 .wav .m4a .aac .ogg .flac
.mp4 .mov .avi .mkv .webm
.wma .wmv .flv .asf .ts .m4v .mpeg .mpg .3gp .caf .dav .vob .h264 .m2ts .mts .mxf
```

Override with `MARSHALOSS_INGEST_EXTENSIONS`. Default max file size: **500 MB** (`MARSHALOSS_MAX_FILE_BYTES`).

**Not in v1:** PST/OST explosion, Cellebrite/UFDR, proprietary jail-call containers ffmpeg can’t probe — design notes in [docs/EXTENSIONS.md](docs/EXTENSIONS.md).

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

# Parallel file ingest (Docling/ffmpeg/API). Default 4; tune to hardware:
#   export MARSHALOSS_INGEST_WORKERS=8   # Gideon-ish
#   export MARSHALOSS_INGEST_WORKERS=2   # laptop / rate-limit friendly
uv run marshaloss ingest "/path/to/case/root" \
  --slug washington-jersey-city \
  --name "Washington v. Jersey City" \
  --workers 8 \
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
