# MarshalOSS

Use **Claude Cowork** or **Perplexity Computer** to review and analyze criminal defense discovery: PDFs (including bad or missing OCR), emails, bodycam and surveillance video, radio and call audio, forensic dumps, and similar production material.

MarshalOSS runs on your computer. It extracts text, builds a searchable index, and exposes the case over MCP. You ask questions and do the analysis in Cowork or Computer. That keeps query cost closer to a normal subscription instead of paying enterprise e-discovery AI rates. It is meant to be usable on **CJA appointments, public defender work, and smaller private cases**.

The project is **hosting-agnostic** — run it locally or behind whatever network you prefer. Do **not** expose the port on the public internet without auth. Prefer a private path such as **Tailscale** or a **Cloudflare Tunnel**. Optional helpers for that are under [`tools/networking/`](tools/networking/README.md).

Apache-2.0. Private: [github.com/adam-elewa/MarshalOSS](https://github.com/adam-elewa/MarshalOSS).

## Objective and scope

Commercial tools that claim AI over a whole discovery set are usually priced for large civil matters. Criminal cases often have the same mess — volume and odd file types — without the same budget. A routine case can still include hundreds of gigabytes of audio, video, and forensic exports. Many file types are ignored by generic software, or billed heavily to process.

The project is for two things at low cost, without skipping paid models when local extraction is wrong or empty:

1. **AI search and analysis** over the production (MarshalOSS + Cowork / Computer)
2. **Basic human review** — browsing, tagging, and annotating discovery (via **Datashare**)

The project is not designed to rebuild Relativity. It wires together existing open-source pieces (Docling, Datashare, ffmpeg, SQLite/FTS, MCP) and adapts them for defense work: index locally; re-run OCR or transcription only when quality checks fail; Claude or Perplexity for questions, drafting, and connectors (Outlook, Word, CourtListener, and so on). Manual review tools (browse, tag, annotate) should be delivered by **extending or revising Datashare** for defense needs — not by inventing a new review product from scratch in this repo.

---

## Note for contributors

Contributions from practicing criminal defense attorneys are welcome. Useful additions usually come from something that would have helped on a real case.

Design decisions to follow:

### 1. Claude Cowork or Perplexity Computer for query and analysis

Questions and analysis belong in **Claude Cowork** or **Perplexity Computer**, not in a separate chat product that spends API tokens on every search and memo.

| Why | Detail |
|---|---|
| Cost | Subscription / Computer usage is usually cheaper than API calls for every query over a large set |
| Connectors | Outlook, Word, calendars, CourtListener-style research, browsers, and other MCP/Computer tools stay available without reimplementing them here |

MarshalOSS is meant to stay local ingest, search, and MCP context. Cowork or Computer asks questions, opens files, and drafts. The project is not designed to become another Relativity-style AI chatbot unless there is a concrete defense reason the host tool cannot do the job.

### 2. Prefer existing open-source tools over writing parsers

Especially for odd file types (DVR/surveillance containers, forensic dumps, mail stores, niche players): the project is not designed to invent and maintain dozens of custom format libraries.

| Prefer | Avoid |
|---|---|
| Docling (and similar) for documents, OCR, native A/V transcription | Homegrown PDF/Office/email parsers |
| ffmpeg / ffprobe for media conversion | Custom demuxers per vendor extension |
| Extend/revise **Datashare** for human-led review UX | A new review UI or Relativity clone in this repo |
| Small adapters around mature libraries | One-off file-type connectors only this project maintains |

If ffmpeg cannot open a file or Docling cannot read it, convert with the vendor tool first, or add a thin adapter around an existing open project — not a permanent MarshalOSS parser for every extension that appears in a production.

### 3. Local first, paid APIs when extract quality is bad

Run extraction locally when possible. Call paid vision, transcription, or embedding APIs when quality checks say the text is bad or missing. New features should follow the same rule: cheapest path that still produces usable work product.

### 4. Defense workflow first

Prefer features that help counsel find and trust material in a real production (Bates, manifests, ingest status/quality, searchable media) over chat demos that only work on a clean sample set.

Issues and PRs from defense counsel are fine even if rough: what the case needed, and which open tool to wrap instead of rewriting.

---

## How Cowork / Computer fits

```
  Production on disk
              │
              ▼
     MarshalOSS ingest (local)
       unzip → hashed manifest
       Docling OCR/ASR (+ reprocess when quality is bad)
       image/video captions · file entities/tags
       FTS5 + Voyage embeddings
              │
              ▼
     SQLite + HTTP/MCP  (:7340)
              │
       ┌──────┴──────┐
       ▼             ▼
 Claude Cowork /      Datashare (optional)
 Perplexity Computer  human review :8100
```

| Layer | Role | Cost |
|---|---|---|
| MarshalOSS | Ingest, search, file URLs, MCP | Mostly local compute; small API spend for embeddings and failed OCR/ASR |
| Claude Cowork / Perplexity Computer | Questions, analysis, drafting, connectors | Mostly subscription usage |
| Datashare (optional) | Browse / keyword / stars / tags | Open source (AGPL upstream) |

Practical points:

1. Docling does first-pass OCR/ASR locally. Only bad pages or clips go to paid vision / `gpt-4o-transcribe`.
2. MarshalOSS does not answer questions itself. `ask` / `search` return text chunks and links; Cowork or Computer writes the response.
3. Entities and tags are one cheap pass per file, not a paid call on every chunk.
4. Ingest can run in parallel (`--workers`); tune for your machine and API limits.
5. Point MCP at your MarshalOSS URL (`http://127.0.0.1:7340` locally, or your Tailscale / tunnel URL) so Cowork or Computer can search the case and use its other connectors.

**MCP config (Claude Desktop / Cowork-style hosts)**

```json
"marshaloss": {
  "command": "npx",
  "args": [
    "-y",
    "mcp-remote",
    "http://<HOST>:7340/mcp",
    "--allow-http",
    "--transport",
    "http-only"
  ]
}
```

Set `MARSHALOSS_PUBLIC_BASE` to the same base URL clients use so open/download links work. See [`tools/networking/`](tools/networking/README.md).

### MCP tools

| Tool | Purpose |
|---|---|
| `list_cases` / `get_active_case` / `set_active_case` | Case selection |
| `browse_case_files` | Directory listing + Bates + URLs |
| `search` | Keyword / semantic / hybrid hits + snippets + `open_url` |
| `ask` | Top hybrid chunks as context (the host model answers) |
| `get_file_link` / `open_file_browser` | File links via `PUBLIC_BASE` |

---

## Features

| Feature | Status |
|---|---|
| Multi-format ingest (Docling) | Yes |
| Zip expand + hashed production manifest | Yes |
| Ingest status / quality on the manifest | Yes |
| Bates from path/filename | Yes |
| SQLite FTS5 + Voyage (or offline hash) hybrid search | Yes |
| Parallel file workers (`--workers` / `INGEST_WORKERS`) | Yes |
| OCR reprocess (vision) when Docling text is bad | Yes |
| ASR reprocess (`gpt-4o-transcribe`) when A/V transcript is bad | Yes |
| ffmpeg media bridge (extra A/V → wav → ASR) | Yes |
| Captions for stills and sampled video frames | Yes |
| File-level entities + tags (with name aliases) | Yes |
| MCP for Cowork / Desktop | Yes |
| Case browser + open/download URLs | Yes |
| Human review (browse / keyword / stars / tags) | Datashare sidecar |
| PST / OST explosion, Cellebrite/UFDR workers | Not in v1 — [docs/EXTENSIONS.md](docs/EXTENSIONS.md) |

---

## Covered file types

Default allowlist (override with `MARSHALOSS_INGEST_EXTENSIONS`):

| Kind | Extensions |
|---|---|
| Documents | `.pdf` `.docx` `.doc` `.pptx` `.ppt` `.xlsx` `.xls` `.csv` `.txt` `.md` `.html` `.htm` |
| Email | `.eml` `.msg` |
| Images | `.png` `.jpg` `.jpeg` `.tif` `.tiff` `.bmp` `.webp` |
| Audio (Docling ASR) | `.mp3` `.wav` `.m4a` `.aac` `.ogg` `.flac` |
| Video (Docling ASR) | `.mp4` `.mov` `.avi` `.mkv` `.webm` |
| Other A/V via ffmpeg → wav → ASR | `.wma` `.wmv` `.flv` `.asf` `.ts` `.m4v` `.mpeg` `.mpg` `.3gp` `.caf` `.dav` `.vob` `.h264` `.m2ts` `.mts` `.mxf` |

Anything else is left off the allowlist on purpose. If Docling or ffmpeg cannot open a file, convert it with whatever tool produced it, then re-ingest.

---

## Ingest pipeline (what actually happens)

Think of ingest as: **turn a folder of discovery into searchable text + vectors**, then stamp a checklist so you know what worked. `marshaloss ingest` runs all of this. Files can run in parallel (`--workers`).

```
  Case folder on disk
           │
           ▼
  ┌─ 1. PREPARE (local, $0) ─────────────────────────────────────┐
  │  Unzip *.zip → sibling __unzipped/ folders                   │
  │  Hash every file (SHA-256), guess Bates from path            │
  │  Write production manifest (“what was produced”)             │
  └──────────────────────────────┬───────────────────────────────┘
                                 │
           for each allowlisted file (parallel workers)
                                 ▼
  ┌─ 2. EXTRACT TEXT (local Docling + ffmpeg, $0 API) ───────────┐
  │  PDFs / Office / email / images → Docling OCR/parse          │
  │  Common A/V → Docling ASR                                    │
  │  Other A/V → ffmpeg → wav → same ASR path                    │
  │  Output: text chunks (with page/time when available)         │
  └──────────────────────────────┬───────────────────────────────┘
                                 ▼
  ┌─ 3. QUALITY GATE (local score; API only if bad) ─────────────┐
  │  Docs: garbage/empty OCR? → OpenAI/Anthropic vision OCR      │
  │  A/V: empty/junk transcript? → OpenAI gpt-4o-transcribe      │
  │  Good enough Docling text? → keep it, no API call            │
  └──────────────────────────────┬───────────────────────────────┘
                                 ▼
  ┌─ 4. VISUAL CAPTIONS (API, cheap vision) ─────────────────────┐
  │  Photos with little OCR text → caption                       │
  │  Video → sample frames → caption each (“what’s on screen”)   │
  │  Captions become searchable chunks like any other text       │
  └──────────────────────────────┬───────────────────────────────┘
                                 ▼
  ┌─ 5. ENTITIES / TAGS (API, cheap chat model, per file) ───────┐
  │  One pass over that file’s full text → people/orgs + tags    │
  │  Merge aliases (e.g. short name → full name)                 │
  │  Stored on the file for filters / UX — not every chunk       │
  └──────────────────────────────┬───────────────────────────────┘
                                 ▼
  ┌─ 6. INDEX (Voyage API + local SQLite) ───────────────────────┐
  │  Every text chunk → Voyage embedding (voyage-law-2)          │
  │  Store: files, chunks, vectors, FTS5 keyword index           │
  │  Update manifest: ready/error, quality, entities, tags       │
  └──────────────────────────────┬───────────────────────────────┘
                                 ▼
                    Ready for search / MCP / Datashare
```

### Stage-by-stage (lay version)

**1. Prepare — inventory, no AI**  
Production folders often arrive as zips. MarshalOSS expands them, hashes every file, and builds a **manifest**: a flat list of what the state produced. That is your “did we get everything?” board before OCR even runs. No API keys needed.

**2. Extract — local Docling (and ffmpeg when needed)**  
[Docling](https://github.com/docling-project/docling) is an open-source document AI stack that runs **on your machine**. It turns PDFs, Office, email, images, and common audio/video into text.  
For A/V types Docling does not take natively, MarshalOSS calls **ffmpeg** (installed on the host, not shipped in this repo): convert to a plain WAV, then run the same transcription path.  
**Cost:** CPU/GPU time, not cloud tokens. This is the bulk of wall-clock time on a big case.

**3. Quality gate — pay only when the extract looks bad**  
Local heuristics score the text (empty, gibberish, “thanks for watching” junk ASR, etc.).  
- Bad **document/image** OCR → call a **vision** model (OpenAI or Anthropic, cheap tier by default) on the bad pages.  
- Bad **audio/video** transcript → call **OpenAI `gpt-4o-transcribe`**.  
If Docling did fine, **no API call**. That is how cost stays down without accepting garbage text.

**4. Visual captions — cheap vision so pictures are searchable**  
A still with almost no OCR, or a video that is mostly images, will not show up in keyword search. This step asks a vision model for a short caption (and samples video frames). Those captions are stored as text chunks so “red hoodie in the hallway” can hit later.

**5. Entities and tags — one cheap pass per file**  
A small chat model (`gpt-4o-mini` / Haiku) reads the file’s extracted text and returns people/orgs/places plus short tags, with name aliases merged. Used for simple filtering later. Not a paid call on every chunk.

**6. Voyage embeddings + SQLite — how search gets “smart”**  
Plain keyword search (SQLite **FTS5**) finds exact words. That misses “the booking” vs “processed at central.”  

**Voyage** (`voyage-law-2`, needs `VOYAGE_API_KEY`) turns each text chunk into a vector — a list of numbers that means “what this passage is about.” Those vectors are stored next to the chunks.  

When you (or Cowork) search:  
- **Keyword** → FTS5  
- **Semantic** → embed the query with Voyage, compare to stored vectors  
- **Hybrid (default)** → fuse both (RRF)  

So Voyage is **not** answering questions. It only builds/query the meaning index. **Claude Cowork / Perplexity Computer** still write the answer from the chunks MarshalOSS returns over MCP.  

If there is no Voyage key, MarshalOSS falls back to a weak local hash embedder — fine for smoke tests, not for real semantic search.

### API calls cheat sheet

| Stage | Who gets called | When | Why |
|---|---|---|---|
| Prepare | Nobody | Always | Inventory only |
| Extract | Nobody (Docling/ffmpeg local) | Always | Turn files into text |
| Quality | OpenAI or Anthropic vision | Doc OCR looks bad | Fix unreadable pages |
| Quality | OpenAI `gpt-4o-transcribe` | A/V transcript looks bad | Fix bad speech-to-text |
| Visual | OpenAI or Anthropic vision | Thin OCR / video frames | Make pictures searchable |
| Entities | OpenAI mini or Anthropic Haiku | Each file with enough text | Tags + people/org names |
| Index | **Voyage** | Every text chunk | Semantic search vectors |
| Ask / search (later) | Voyage (query embed) + **no** chat API in MarshalOSS | Each search | Host agent (Cowork) does the reasoning |

### Ingest confidence

After ingest, each manifest row gets status and quality so you can see what failed:

| Field | Meaning |
|---|---|
| `ingest_status` | `ready` / `error` / `skipped` / `pending` / `skipped_not_covered` |
| `quality_score` | 0–1 extraction confidence |
| `quality_reasons` | why score is low |
| `chunk_count` | searchable chunks |
| `escalated` | OCR or ASR reprocess ran |
| `entities` / `tags` | file-level extract |

API: `/api/manifest`, `/api/manifest/summary`.  
Disk: `<root>/.marshaloss/ingest_confidence.json`.

### Cost (rough)

| Pass | Money | Notes |
|---|---|---|
| Docling / ffmpeg | ~$0 API | Dominates **time** |
| Voyage | Every chunk (+ each search query) | Usually cents–low dollars per mid case |
| OCR / ASR reprocess | Only failures | Can dominate A/V-heavy cases if many clips fail |
| Visual + entities | Cheap models | Usually small vs ASR |

Runtime is often the bottleneck — use `--workers`. No per-file or per-zip size skip: large media is ingested like everything else.

---

## Quick start

```bash
export PATH="$HOME/.local/bin:$PATH"
cd ~/Projects/MarshalOSS
# source ~/.marshal/env.sh   # VOYAGE / OPENAI / ANTHROPIC

uv sync

# export MARSHALOSS_INGEST_WORKERS=8

uv run marshaloss ingest "/path/to/case/root" \
  --slug my-case \
  --name "My Case" \
  --workers 8

# Local (default PUBLIC_BASE is http://127.0.0.1:7340)
uv run marshaloss serve

# Or use optional networking helpers (Tailscale / Cloudflare Tunnel / explicit URL):
#   ./tools/networking/serve.sh
```

Manifest only (before Docling):

```bash
uv run marshaloss prepare "/path/to/case/root" \
  --slug my-case \
  --name "My Case"
```

| Surface | URL |
|---|---|
| Home / browser | `http://<host>:7340/` · `/browser` |
| Health | `http://<host>:7340/health` |
| Manifest | `http://<host>:7340/api/manifest` · `/api/manifest/summary` |
| MCP | `http://<host>:7340/mcp` |

```bash
uv run marshaloss search "bodycam booking" --mode hybrid --top-k 8
```

---

## Dependencies

| Dependency | Role | Required? |
|---|---|---|
| Docling (`docling[asr]`) | First-pass parse / OCR / ASR | Yes |
| ffmpeg / ffprobe | Media bridge, ASR reprocess, frame sample | Yes for A/V |
| `VOYAGE_API_KEY` | `voyage-law-2` embeddings | Recommended (else weak local hash) |
| `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` | OCR reprocess, captions, entities; OpenAI also ASR | Recommended |
| Python 3.12+, uv | Runtime | Yes |

Keys also load from `~/.marshal/env.sh` when unset.

MarshalOSS does not call a chat model to answer case questions; Cowork or Computer does that from retrieved text.

### OCR quality checks

Reprocess when Docling looks bad: poor grades, near-empty or sparse text, `�` / mojibake / junk symbols, or Docling hard-fail on PDF/image. A fair grade with normal text does not reprocess. Cap: `MARSHALOSS_OCR_ESCALATE_MAX_PAGES` (default 25).

### ASR quality checks

Reprocess when the transcript is empty, looping, sparse for the length/size, or full of junk phrases → ffmpeg → `gpt-4o-transcribe`.

### Visual analyze

Stills with little OCR text get a caption. Video: sample a frame every N seconds (default 5s, max 48) and caption those (`Visual @m:ss`).

---

## Dual stack (human review = Datashare)

The project is also designed to provide **basic review tools** for browsing, tagging, and annotating discovery. That plane is **Datashare**, not a second UI invented inside MarshalOSS.

| Role | Tool | Default |
|---|---|---|
| Search / MCP / Cowork or Computer | MarshalOSS | `:7340` |
| Browse / tag / annotate (human-led) | **Datashare** | `:8100` |

Grow defense-specific review features (coding UX, tags, annotation, privilege, layouts) as an **extension or revision of Datashare**. Do not reinvent a review platform in this repo.

Compose and ops scripts: [`review/`](review/README.md). Dual-stack notes: [docs/DUAL_STACK.md](docs/DUAL_STACK.md).  
Datashare upstream is AGPL-3.0; MarshalOSS in this repo is Apache-2.0.

---

## Configuration (`MARSHALOSS_` prefix)

| Variable | Default | Meaning |
|---|---|---|
| `DATA_DIR` | `~/.marshaloss` | SQLite + local data |
| `DB_PATH` | `$DATA_DIR/marshal_oss.db` | Override DB |
| `HOST` / `PORT` | `0.0.0.0` / `7340` | Bind |
| `PUBLIC_BASE` | `http://127.0.0.1:7340` | Base URL for MCP / open links (local, Tailscale, tunnel, …) |
| `INGEST_WORKERS` | `4` | Parallel files (`--workers` / `-j`) |
| `EMBED_BACKEND` | `voyage` | `voyage` or `hash` |
| `EMBED_MODEL` | `voyage-law-2` | Voyage model |
| `OCR_ESCALATE` | `true` | Vision OCR when quality fails |
| `OCR_ESCALATE_PROVIDER` | `auto` | `auto` / `openai` / `anthropic` |
| `OCR_ESCALATE_MAX_PAGES` | `25` | Cap AI OCR pages/file |
| `OCR_ESCALATE_MIN_SCORE` | `0.72` | Score gate |
| `ASR_ESCALATE` | `true` | Cloud ASR when transcript is bad |
| `ASR_ESCALATE_MODEL` | `gpt-4o-transcribe` | ASR model |
| `VISUAL_ANALYZE` | `true` | Image/frame captions |
| `VISUAL_FRAME_INTERVAL_SEC` | `5` | Video sample interval |
| `VISUAL_MAX_FRAMES` | `48` | Cap frames/video |
| `ENTITY_EXTRACT` | `true` | File-level entities/tags |
| `ENTITY_EXTRACT_SEGMENT_CHARS` | `14000` | Segment size for long files |
| `ENTITY_EXTRACT_MAX_CHARS` | `0` | `0` = all extracted text |
| `EXPAND_ZIPS` | `true` | Unzip before ingest |
| `EXPAND_ZIP_MAX_DEPTH` | `2` | Nested zip depth |
| `INGEST_EXTENSIONS` | (see above) | Allowed suffixes |
| `BATES_PATTERN` | built-in | Bates regex |

Also accept bare `VOYAGE_API_KEY`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`.

---

## After ingest (how you use it)

```
  SQLite (chunks + Voyage vectors + FTS5)
           │
           ├─ REST  /api/search  /api/manifest  /browser  /files/…
           ├─ MCP   /mcp  → Claude Cowork / Perplexity Computer
           └─ Datashare (optional) → browse / tag / annotate
```

Data dir: `~/.marshaloss/` (separate from legacy `~/.marshal-lite` — re-ingest or set `MARSHALOSS_DB_PATH`).

---

## What this is not

The project is not designed to replace Relativity. It covers ingest, searchable text, MCP for Cowork / Computer, and **basic browse / tag / annotate review through Datashare**. It is not a full enterprise review desk (privilege logs, redaction workflows, reviewer assignment as in Relativity). Those needs, if required, should still be met by extending Datashare — not by building a parallel product here.
