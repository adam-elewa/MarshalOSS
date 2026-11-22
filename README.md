# MarshalOSS

Use **Claude Cowork** or **Perplexity Computer** to review and analyze criminal defense discovery: PDFs (including bad or missing OCR), emails, bodycam and surveillance video, radio and call audio, forensic dumps, and similar production material.

MarshalOSS runs on your computer or a private Tailscale host. It extracts text, builds a searchable index, and exposes the case over MCP. You ask questions and do the analysis in Cowork or Computer. That keeps query cost closer to a normal subscription instead of paying enterprise e-discovery AI rates. It is meant to be usable on **CJA appointments, public defender work, and smaller private cases**.

Apache-2.0. Private: [github.com/adam-elewa/MarshalOSS](https://github.com/adam-elewa/MarshalOSS).

## Objective and scope

Commercial tools that claim AI over a whole discovery set are usually priced for large civil matters. Criminal cases often have the same mess — volume and odd file types — without the same budget. A routine case can still include hundreds of gigabytes of audio, video, and forensic exports. Many file types are ignored by generic software, or billed heavily to process.

MarshalOSS is for search and analysis over those productions at low cost, without skipping paid models when local extraction is wrong or empty.

We are not trying to rebuild Relativity. The point is to wire together existing open-source pieces (Docling, Datashare, ffmpeg, SQLite/FTS, MCP) and adapt them for defense work. Index locally. Re-run OCR or transcription only when quality checks fail. Use Datashare if you want a separate human review UI. Use Claude or Perplexity for questions, drafting, and their connectors (Outlook, Word, CourtListener, and so on).

---

## Note for contributors

Practicing criminal defense attorneys are welcome. Useful additions usually come from something that would have helped on a real case.

Please stick to these choices:

### 1. Claude Cowork or Perplexity Computer for query and analysis

Put questions and analysis in **Claude Cowork** or **Perplexity Computer**. Do not build a separate chat product that spends API tokens on every search and memo.

| Why | Detail |
|---|---|
| Cost | Subscription / Computer usage is usually cheaper than API calls for every query over a large set |
| Connectors | Outlook, Word, calendars, CourtListener-style research, browsers, and other MCP/Computer tools stay available without reimplementing them here |

MarshalOSS should remain local ingest, search, and MCP context. Cowork or Computer asks questions, opens files, and drafts. Do not turn this repo into another Relativity-style AI chatbot unless there is a concrete defense reason the host tool cannot do the job.

### 2. Prefer existing open-source tools over writing parsers

Especially for odd file types (DVR/surveillance containers, forensic dumps, mail stores, niche players): do not invent and maintain dozens of custom format libraries.

| Prefer | Avoid |
|---|---|
| Docling (and similar) for documents, OCR, native A/V transcription | Homegrown PDF/Office/email parsers |
| ffmpeg / ffprobe for media conversion | Custom demuxers per vendor extension |
| Existing review tools (e.g. Datashare) for human coding | Cloning Relativity in this repo |
| Small adapters around mature libraries | One-off file-type connectors only we maintain |

If ffmpeg cannot open a file or Docling cannot read it, convert with the vendor tool first, or add a thin adapter around an existing open project. Do not grow a permanent MarshalOSS parser for every extension that appears in a production.

### 3. Local first, paid APIs when extract quality is bad

Run extraction locally when possible. Call paid vision, transcription, or embedding APIs when quality checks say the text is bad or missing. New features should follow the same rule: cheapest path that still produces usable work product.

### 4. Defense workflow first

Prefer Bates, production manifests, ingest status/quality, and searchable media over demo-style chat features. When you add something, note the kind of production it is for (e.g. Washington- or Jones-scale sets).

Issues and PRs from defense counsel are fine even if rough. Say what the case needed and which open tool you would wrap instead of rewriting.

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
5. Point MCP at Tailscale `:7340` so Cowork or Computer can search the case and use its other connectors.

**MCP config (Claude Desktop / Cowork-style hosts)**

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

Set `MARSHALOSS_PUBLIC_BASE` to the same Tailscale base so open/download links work.

### MCP tools

| Tool | Purpose |
|---|---|
| `list_cases` / `get_active_case` / `set_active_case` | Case selection |
| `browse_case_files` | Directory listing + Bates + URLs |
| `search` | Keyword / semantic / hybrid hits + snippets + `open_url` |
| `ask` | Top hybrid chunks as context (the host model answers) |
| `get_file_link` / `open_file_browser` | Tailscale file links |

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
| ffmpeg media bridge (odd A/V → wav → ASR) | Yes |
| Captions for stills and sampled video frames | Yes |
| File-level entities + tags (with name aliases) | Yes |
| MCP for Cowork / Desktop | Yes |
| Case browser + open/download URLs | Yes |
| Human review (browse / keyword / stars / tags) | Datashare sidecar |
| PST / Cellebrite UFDR / vendor DVR ffmpeg cannot open | Not in v1 — [docs/EXTENSIONS.md](docs/EXTENSIONS.md) |

Adequate for Washington- and David Jones–type productions (PDF/Office/images/common A/V). Formats ffmpeg cannot probe still need vendor export first.

---

## Ingest pipeline

```
1. prepare   Expand *.zip → <name>__unzipped/
             Walk every file → full SHA-256 + Bates + kind
             Write .marshaloss/manifest.jsonl + summary
             Upsert production_manifest

2. extract   Docling DocumentConverter → HybridChunker
             Media: Docling A/V if supported, else ffmpeg → wav → ASR

3. quality   Score OCR/transcript; reprocess only when checks fail
             OCR → vision (mini/Haiku) · ASR → gpt-4o-transcribe

4. visual    Image captions + video frame samples

5. entities  File-level mini/Haiku → entities + tags
             (short file = 1 call; long = segments then merge)

6. index     Embed → SQLite files / chunks / embeddings / FTS5
             Update manifest: ingest_status, quality_*, entities, tags
             Write ingest_confidence.json
```

`marshaloss ingest` runs prepare first.

### Ingest confidence

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

### Entities and tags

One pass over the full extracted text per file (`gpt-4o-mini` / Haiku):

- Fits in one window → single call; merge variants (`Jones` / `David Jones` → canonical + aliases)
- Longer → map ~14k-char segments → one merge call

Stored on `files.meta` / the manifest. Not per-chunk tagging for now.

### Media bridge

| ffmpeg can open | Examples |
|---|---|
| Convert to wav → ASR | `.dav` `.wmv` `.wma` `.flv` `.asf` `.ts` `.m4v` `.mpeg` `.3gp` `.caf` `.vob` `.h264` … |
| Skip | `.av3` `.g64x` `.v22` `.vac` — convert with vendor tools first |

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

## Cost (rough)

| Pass | When it costs money | Ballpark |
|---|---|---|
| Docling | Local | ~$0 API |
| Voyage embeddings | Every chunk | cents to a few dollars on a mid-size case |
| OCR / ASR reprocess | Only when quality fails | Can dominate A/V-heavy cases if many files fail |
| Visual captions | Stills + sampled frames | cheap vision models |
| Entities/tags | One or more cheap calls per file | often under $1 on a Jones-scale set |

David Jones–class (~80 ingestible files / ~17 GB mostly A/V): often about **$2–9** API total if reprocess stays selective. Runtime is usually the bigger issue — use `--workers`.

---

## Quick start

```bash
export PATH="$HOME/.local/bin:$PATH"
cd ~/Projects/MarshalOSS
# source ~/.marshal/env.sh   # VOYAGE / OPENAI / ANTHROPIC

uv sync

# export MARSHALOSS_INGEST_WORKERS=8

uv run marshaloss ingest "/path/to/case/root" \
  --slug washington-jersey-city \
  --name "Washington v. Jersey City" \
  --workers 8

export MARSHALOSS_PUBLIC_BASE="http://$(tailscale ip -4):7340"
uv run marshaloss serve
```

Manifest only (before Docling):

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

## Dual stack (optional human review)

| Role | Tool | Default |
|---|---|---|
| Search / MCP / Cowork or Computer | MarshalOSS | `:7340` |
| Human browse / keyword / stars / tags | Datashare | `:8100` |

See [docs/DUAL_STACK.md](docs/DUAL_STACK.md) and [review/README.md](review/README.md).  
Datashare upstream is AGPL-3.0; MarshalOSS in this repo is Apache-2.0.

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
| `MAX_FILE_BYTES` | `524288000` | Skip larger |
| `BATES_PATTERN` | built-in | Bates regex |

Also accept bare `VOYAGE_API_KEY`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`.

---

## Architecture (search)

```
Case folder
    → prepare (manifest) → extract → reprocess → visual → entities
    → Embedder (Voyage | hash)
    → SQLite
         ├── FTS5 keyword
         ├── vector cosine
         └── hybrid RRF
    → REST /api/search · /api/manifest · /browser · /files/…
    → MCP  /mcp  → Cowork / Computer
```

Data dir: `~/.marshaloss/` (separate from legacy `~/.marshal-lite` — re-ingest or set `MARSHALOSS_DB_PATH`).

---

## What this is not

This is not Relativity. There are no coding panels, privilege logs, redaction workflows, or reviewer assignment here. MarshalOSS covers ingest status, searchable text, and MCP search/context for Cowork or Computer. Use Datashare (or another review tool) for human coding.
