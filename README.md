# MarshalOSS

**AI-powered search and reasoning over criminal defense discovery** — locally, at a cost that fits CJA appointments, public defenders, and smaller private cases — without giving up frontier models where they matter.

Apache-2.0. Private: [github.com/adam-elewa/MarshalOSS](https://github.com/adam-elewa/MarshalOSS).

## Objective and scope

No commercial platform today offers serious AI reasoning over a large, messy discovery corpus at a price that makes sense for most criminal matters. Enterprise e-discovery AI is built for civil megacases; criminal productions are often just as complex — and the budget is not.

Criminal defense discovery is rarely a clean PDF binder. It is PDFs and Office files with poor or missing OCR, email dumps, body-worn and surveillance video, radio and call audio, and forensic exports. Even a “minor” case can mean **hundreds of gigabytes** of media and exotic file types that off-the-shelf tools ignore or bill by the gigabyte to touch.

**MarshalOSS exists to close that gap:** AI search and reasoning over large, varied criminal productions at the **lowest practical cost**, while still using **frontier models** for quality where local extraction is not enough.

The approach is not to reinvent e-discovery. It is to **glue and customize strong open-source projects** (Docling, Datashare, ffmpeg, SQLite/FTS, MCP) for how defense counsel actually work — then put **Claude Cowork** (or Claude Desktop) on top as the AI front end for questions, chase-downs, and analysis over the indexed case.

In practice that means: ingest and index on your machine or a private Tailscale box; escalate OCR/ASR only when quality gates fail; keep human review optional in Datashare; let Claude reason over retrieved context via MCP. The goal is to keep e-discovery and AI analysis **aligned with the economic limits of criminal practice** while still matching the volume and complexity of modern discovery.

---

## Note for contributors

**Practicing criminal defense attorneys are especially welcome.** The best features will come from people who live inside discovery every week — not from abstract platform design. If a workflow would have saved you hours on a real case, that is a good contribution.

When you build, please follow these design decisions:

### 1. Claude Cowork or Perplexity Computer as the core AI engine

Prefer **Claude Cowork** (or **Perplexity Computer**) as the place where query and analysis happen — not a custom chat UI that burns its own API tokens on every question.

| Why | Detail |
|---|---|
| **Cost** | Subscription / Computer-style usage is usually far cheaper than wiring every search, memo, and follow-up as raw API calls against a large corpus |
| **Quality** | Frontier reasoning stays in a product that is already optimized for agents, tools, and long sessions |
| **Ecosystem** | Users get a mature connector surface “for free”: Outlook / email, Microsoft Word, calendars, CourtListener-style legal research, browsers, and other MCP or Computer integrations — without MarshalOSS reinventing each one |

MarshalOSS should stay the **local ingest + search + MCP/context plane**. The AI engine on top asks questions, opens files, drafts, and reaches out to other tools. Do not grow MarshalOSS into a second Relativity AI chatbot unless there is a clear defense-specific reason the host agent cannot do it.

### 2. Prefer open source glue over building from scratch

Especially for **exotic file types** (DVR/surveillance containers, forensic dumps, mail stores, niche players): **do not** invent and maintain hundreds of custom parsers.

| Prefer | Avoid |
|---|---|
| **Docling** (and similar) for documents, OCR, native A/V ASR | Homegrown PDF/Office/email parsers |
| **ffmpeg / ffprobe** as the media bridge | Custom demuxers per vendor extension |
| Existing open review / index tools (e.g. Datashare) for human coding UX | Rewriting a full Relativity clone |
| Thin adapters that call mature libraries | “Just one more” file-type connector that only we own |

If ffmpeg cannot probe it or Docling cannot read it, the default path is **upstream conversion / vendor export**, or a **small, well-scoped adapter** around an existing open project — not a permanent MarshalOSS-owned library for every suffix that shows up in a production.

### 3. Cost and quality stay coupled

Local first; escalate to paid vision/ASR/embeddings only when quality gates say the extract is bad. New features should make the same tradeoff: cheapest path that still holds up in court-facing work product.

### 4. Defense workflow first

Bates, production manifests, ingest confidence, searchable media, and “can counsel actually find this tonight” beat generic RAG demos. Document the real case shape (Washington / Jones–class productions) when you add something.

PRs, issues, and design notes from the defense bar are encouraged — even rough ones. Say what the case needed and what open tool you would rather wrap than rewrite.

---

## Claude Cowork (or Perplexity Computer) as the front end

```
  Production on disk (Washington, David Jones, …)
              │
              ▼
     MarshalOSS ingest (local)
       unzip → hashed manifest
       Docling OCR/ASR (+ escalate when bad)
       visual captions · file entities/tags
       FTS5 + Voyage embeddings
              │
              ▼
     SQLite + HTTP/MCP  (:7340)
              │
       ┌──────┴──────┐
       ▼             ▼
 Claude Cowork /      Datashare (optional)
 Perplexity Computer  human review :8100
 (AI engine + connectors)
```

| Layer | What it is | What you pay |
|---|---|---|
| **MarshalOSS** | Local ingest, hybrid search, file URLs, MCP tools | Mostly CPU/GPU time; small API $ for embeddings + escalate + entities |
| **Claude Cowork / Perplexity Computer** | Query, analysis, drafting + connectors (Outlook, Word, CourtListener, …) | Subscription-style usage — not per-query discovery-platform AI pricing |
| **Datashare** (optional) | Relativity-ish human review beside the case | Free/open; AGPL upstream |

**Why this is cheap and simple**

1. **Local first** — Docling does OCR/ASR on-box; only *bad* pages/clips escalate to mini/Haiku/`gpt-4o-transcribe`.
2. **No LLM answers inside MarshalOSS** — `ask` / `search` return chunks + links; Cowork / Computer writes the memo.
3. **File-level tags/entities** — one cheap pass per file (canonical names), not per-chunk Sonnet.
4. **Parallel ingest** — tune workers to your hardware (`-j 8` on Gideon, `-j 2` on a laptop).
5. **Agent is the add-on** — counsel works in Cowork or Computer; point MCP at Tailscale `:7340` and the case is AI-ready, with their existing connectors.

**Cowork / Desktop MCP config**

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

Set `MARSHALOSS_PUBLIC_BASE` to that same Tailscale base so open/download links work from Cowork.

### MCP tools Claude uses

| Tool | Purpose |
|---|---|
| `list_cases` / `get_active_case` / `set_active_case` | Case selection |
| `browse_case_files` | Directory listing + Bates + URLs |
| `search` | Hybrid / keyword / semantic hits + snippets + `open_url` |
| `ask` | Top hybrid chunks as RAG context (**Claude** answers) |
| `get_file_link` / `open_file_browser` | Tailscale file links |

---

## What you get (capability map)

| Capability | Status |
|---|---|
| Multi-format ingest (Docling) | Yes |
| Zip expand + **hashed production manifest** | Yes |
| **Ingest confidence** (status, quality, low-quality board) | Yes |
| Bates from path/filename | Yes |
| SQLite FTS5 + Voyage (or offline hash) hybrid search | Yes |
| Parallel file workers (`--workers` / `INGEST_WORKERS`) | Yes |
| OCR escalate (vision) on garbage/sparse Docling | Yes |
| ASR escalate (`gpt-4o-transcribe`) on bad A/V | Yes |
| ffmpeg **media bridge** (DVR-ish types → wav → ASR) | Yes |
| Visual captions (stills + sampled video frames) | Yes |
| File-level AI **entities + tags** (canonical aliases) | Yes |
| MCP for Claude Cowork / Desktop | Yes |
| Case browser + open/download URLs | Yes |
| Human review (browse / keyword / stars / tags) | **Datashare** sidecar |
| PST / Cellebrite UFDR / exotic vendor DVR | Not v1 — [docs/EXTENSIONS.md](docs/EXTENSIONS.md) |

Good stopping point for **Washington**- and **David Jones**-class productions (PDF/Office/images/standard A/V). Proprietary jail/DVR containers ffmpeg can’t probe still need vendor export upstream.

---

## End-to-end ingest pipeline

```
1. prepare   Expand *.zip → <name>__unzipped/
             Walk every file → full SHA-256 + Bates + kind
             Write .marshaloss/manifest.jsonl + summary
             Upsert production_manifest (UX “what was produced”)

2. extract   Docling DocumentConverter → HybridChunker
             Media: native Docling A/V, else ffmpeg → wav → ASR

3. quality   Score OCR/transcript; escalate only when gates fire
             OCR → vision mini/Haiku · ASR → gpt-4o-transcribe

4. visual    Late pass: image captions + video frame samples

5. entities  File-level mini/Haiku → canonical entities + tags
             (short = 1 call; long = map segments → reduce merge)

6. index     Embed → SQLite files / chunks / embeddings / FTS5
             Stamp manifest: ingest_status, quality_*, entities, tags
             Write ingest_confidence.json for the board
```

`marshaloss ingest` runs **prepare** automatically first.

### Ingest confidence

| Field | Meaning |
|---|---|
| `ingest_status` | `ready` / `error` / `skipped` / `pending` / `skipped_not_covered` |
| `quality_score` | 0–1 extraction confidence |
| `quality_reasons` | why score is low |
| `chunk_count` | searchable chunks |
| `escalated` | OCR or ASR escalate fired |
| `entities` / `tags` | file-level AI extract |

API: `/api/manifest`, `/api/manifest/summary` (`ingest_complete`, `avg_quality`, `low_quality_count`, …).  
Disk: `<root>/.marshaloss/ingest_confidence.json`.

### Entities & tags (keep it simple)

Last pass over **full extracted text** per file (`gpt-4o-mini` / Haiku):

- One window → single call with canonicalization  
  (`Jones` / `D. Jones` / `David Jones` → `canonical: "David Jones"`, `aliases: […]`)
- Longer → map ~14k-char segments → one reduce that merges aliases  

Stored on `files.meta` / manifest. Not per-chunk tagging (that stays future / use-case driven).

### Media bridge (no custom demuxers)

| ffmpeg can probe | Examples |
|---|---|
| Bridged → wav → ASR | `.dav` `.wmv` `.wma` `.flv` `.asf` `.ts` `.m4v` `.mpeg` `.3gp` `.caf` `.vob` `.h264` … |
| Skip (vendor) | `.av3` `.g64x` `.v22` `.vac` — convert upstream |

### Supported extensions (default)

```
.pdf .docx .doc .pptx .ppt .xlsx .xls .csv .txt .md
.html .htm .eml .msg
.png .jpg .jpeg .tif .tiff .bmp .webp
.mp3 .wav .m4a .aac .ogg .flac
.mp4 .mov .avi .mkv .webm
.wma .wmv .flv .asf .ts .m4v .mpeg .mpg .3gp .caf .dav .vob .h264 .m2ts .mts .mxf
```

Override: `MARSHALOSS_INGEST_EXTENSIONS`. Max size default **500 MB** (`MARSHALOSS_MAX_FILE_BYTES`).

---

## Cost model (typical)

| Pass | When it spends $ | Ballpark |
|---|---|---|
| Docling | Local | ~$0 API |
| Voyage embeddings | Every chunk | cents–low dollars per mid case |
| OCR / ASR escalate | Only quality fails | dominate A/V-heavy cases if many escalate |
| Visual captions | Stills + sampled frames | mini/Haiku vision |
| Entities/tags | 1+ cheap calls per file | often **≪ $1** on DJ-scale |

David Jones–class (~80 ingestible / ~17 GB mostly A/V): often **~$2–9** API all-in if escalate stays selective; wall clock is the bigger constraint — use **`--workers`**.

---

## Quick start

```bash
export PATH="$HOME/.local/bin:$PATH"
cd ~/Projects/MarshalOSS
# source ~/.marshal/env.sh   # VOYAGE / OPENAI / ANTHROPIC

uv sync

# Parallelism: hardware + API limits
#   export MARSHALOSS_INGEST_WORKERS=8

uv run marshaloss ingest "/path/to/case/root" \
  --slug washington-jersey-city \
  --name "Washington v. Jersey City" \
  --workers 8

export MARSHALOSS_PUBLIC_BASE="http://$(tailscale ip -4):7340"
uv run marshaloss serve
```

Prepare only (manifest before Docling):

```bash
uv run marshaloss prepare "/path/to/case/root" \
  --slug washington-jersey-city \
  --name "Washington v. Jersey City"
```

| Surface | URL |
|---|---|
| Home / browser | `http://<host>:7340/` · `/browser` |
| Health | `http://<host>:7340/health` |
| Manifest | `http://<host>:7340/api/manifest` · `/api/manifest/summary` |
| MCP (Cowork) | `http://<host>:7340/mcp` |

```bash
uv run marshaloss search "bodycam booking" --mode hybrid --top-k 8
```

---

## AI / local dependencies

| Dependency | Role | Required? |
|---|---|---|
| **Docling** (`docling[asr]`) | First-pass parse / OCR / ASR | Yes |
| **ffmpeg / ffprobe** | Media bridge + ASR escalate + frame sample | Yes for A/V |
| **`VOYAGE_API_KEY`** | `voyage-law-2` embeddings | Recommended (else weak local hash) |
| **`OPENAI_API_KEY`** / **`ANTHROPIC_API_KEY`** | OCR escalate, visual, entities; OpenAI also ASR | Recommended |
| Python **3.12+**, **uv** | Runtime | Yes |

Keys also load from `~/.marshal/env.sh` when unset.

**Not** used for RAG answers inside MarshalOSS — Claude Cowork reasons over retrieved context.

### Quality gates (OCR)

Escalate when Docling looks bad: poor grades, near-empty/sparse extract, `�` / mojibake / symbol salad, hard Docling fail on PDF/image. Fair grade + healthy text does **not** escalate. Cap: `MARSHALOSS_OCR_ESCALATE_MAX_PAGES` (default 25).

### Quality gates (ASR)

Escalate when transcript looks empty, hallucinated (“thanks for watching”), looped, or sparse vs duration/size → ffmpeg → `gpt-4o-transcribe`.

### Visual analyze

Stills with thin OCR → caption. Video → frame every N seconds (default 5s, max 48) → caption chunks (`Visual @m:ss`), embedded like text.

---

## Dual stack (optional human review)

| Role | Tool | Default |
|---|---|---|
| Semantic / MCP / Cowork RAG | **MarshalOSS** | `:7340` |
| Human browse / keyword / stars / tags | **Datashare** | `:8100` |

See [docs/DUAL_STACK.md](docs/DUAL_STACK.md) and [review/README.md](review/README.md).  
Datashare upstream is AGPL-3.0; this repo’s MarshalOSS code is Apache-2.0.

---

## Configuration (`MARSHALOSS_` prefix)

| Variable | Default | Meaning |
|---|---|---|
| `DATA_DIR` | `~/.marshaloss` | SQLite + local data |
| `DB_PATH` | `$DATA_DIR/marshal_oss.db` | Override DB |
| `HOST` / `PORT` | `0.0.0.0` / `7340` | Bind |
| `PUBLIC_BASE` | (set Tailscale URL) | Links for MCP / UI |
| `INGEST_WORKERS` | `4` | Parallel files (`--workers` / `-j`) |
| `EMBED_BACKEND` | `voyage` | `voyage` or `hash` |
| `EMBED_MODEL` | `voyage-law-2` | Voyage model |
| `OCR_ESCALATE` | `true` | Vision OCR on quality fails |
| `OCR_ESCALATE_PROVIDER` | `auto` | `auto` / `openai` / `anthropic` |
| `OCR_ESCALATE_MAX_PAGES` | `25` | Cap AI OCR pages/file |
| `OCR_ESCALATE_MIN_SCORE` | `0.72` | Score gate |
| `ASR_ESCALATE` | `true` | Cloud ASR on bad transcripts |
| `ASR_ESCALATE_MODEL` | `gpt-4o-transcribe` | ASR model |
| `VISUAL_ANALYZE` | `true` | Image/frame captions |
| `VISUAL_FRAME_INTERVAL_SEC` | `5` | Video sample interval |
| `VISUAL_MAX_FRAMES` | `48` | Cap frames/video |
| `ENTITY_EXTRACT` | `true` | File-level entities/tags |
| `ENTITY_EXTRACT_SEGMENT_CHARS` | `14000` | Map window for long files |
| `ENTITY_EXTRACT_MAX_CHARS` | `0` | `0` = all extracted text |
| `EXPAND_ZIPS` | `true` | Unzip before ingest |
| `EXPAND_ZIP_MAX_DEPTH` | `2` | Nested zip depth |
| `INGEST_EXTENSIONS` | (see above) | Allowed suffixes |
| `MAX_FILE_BYTES` | `524288000` | Skip larger |
| `BATES_PATTERN` | built-in | Bates regex |

Also accept bare `VOYAGE_API_KEY`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`.

---

## Architecture (search)

```
Case folder
    → prepare (manifest) → extract → escalate → visual → entities
    → Embedder (Voyage | hash)
    → SQLite
         ├── FTS5 keyword
         ├── vector cosine
         └── hybrid RRF
    → REST /api/search · /api/manifest · /browser · /files/…
    → MCP  /mcp  → Claude Cowork
```

Data dir: `~/.marshaloss/` (does not share legacy `~/.marshal-lite` — re-ingest or point `MARSHALOSS_DB_PATH`).

---

## Not Relativity (and that’s fine)

MarshalOSS + Cowork aims at **ingest confidence, searchable text, and AI Q&A over the production** — not coding panels, privilege logs, redaction workflows, or reviewer assignment. Use Datashare (or your review tool) for human coding; grow those surfaces from this ingest base when the use case demands it.
