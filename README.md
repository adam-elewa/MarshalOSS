# Marshal

Use **Claude Cowork** or **Perplexity Computer** to review and analyze criminal defense discovery: PDFs (including bad or missing OCR), emails, bodycam and surveillance video, radio and call audio, photos, forensic dumps, and similar production material.

Marshal runs on your computer. It extracts text, builds a searchable index, and exposes the case over MCP. You ask questions in Cowork or Computer, so query cost stays close to a normal subscription instead of enterprise e-discovery AI rates. It is meant to be usable on **CJA appointments, public defender work, and smaller private cases**.

Ingest runs once on your machine (local OCR first; paid APIs mainly for bodycam/audio and the odd bad PDF). After that, analysis is just the subscription. Simply put: a **medium-size criminal case** can cost about **$20 one time** to ingest, then about **$20/month** for Claude Pro to analyze it with frontier models (Opus-class and the like). See [Benchmarks and projected cost](#benchmarks-and-projected-cost).

License: AGPL-3.0 (see [License](#license)). Private: [github.com/adam-elewa/MarshalOSS](https://github.com/adam-elewa/MarshalOSS).

## Objective and scope

No commercial platform today offers serious search over a large, messy discovery corpus at a price that makes sense for most criminal matters. Enterprise tools are built for civil megacases; criminal productions are often just as complex — and the budget is not.

A typical production is a mix of PDFs (often with bad or missing OCR), email, bodycam and surveillance video, radio and call audio, photos, and phone dumps. Even a smaller case can run to hundreds of gigabytes, with file types that ordinary tools skip or charge heavily to process.

Marshal closes that gap: ingest and search on your machine (or a private host), ask questions through Cowork or Computer, and keep lasting notes in a notebook. Local tools first (Docling, ffmpeg, SQLite); paid APIs only when local extraction is wrong or empty.

Cowork (and tools like it) can attach **connectors** beside Marshal MCP so analysis, motions, and legal writing stay grounded in the production *and* in outside sources you already use. A practical defense stack:

| Connector | Role |
|---|---|
| **Marshal (MCP)** | Search the case dump; cite real files; update the notebook |
| **Outlook / email** | Pull counsel and discovery email into the same thread |
| **CourtListener** | Opinions, dockets, and citation check without leaving chat |
| **PACER / RECAP** | Federal filings and docket pulls when you need the record |
| **Westlaw / Lexis** (or similar) | Secondary authority and Shepardizing when your firm already has access |
| **Legal data tools** (e.g. Legal Data Hunter–style research) | Statutes, rules, and targeted research beside the facts |

**Why it matters:** one workspace can draft a motion or theory memo that cites Bates from the dump, quotes the email chain, and checks case law — instead of hopping between Relativity-class review, Word, PACER, and a research terminal. Marshal supplies the discovery ground truth; the connectors supply court record and authority.

**Hosting:** run it where you like. Do not put the port on the open internet without auth; prefer **Tailscale** or a **Cloudflare Tunnel**. Helpers are in [`tools/networking/`](tools/networking/README.md).

## What you get

In plain terms, Marshal does six jobs:

1. **Ingest the dump** — Turn a messy production folder into searchable text (OCR, ASR, captions, entities/tags) with a confidence manifest so you know what worked.
2. **Ask over MCP** — Connect Claude Cowork or Perplexity Computer; search and cite real files without building a custom chat product.
3. **Review in the browser** — Case Overview and Document Explorer (categories / folders, hybrid search, side preview).
4. **Build defense viewers** — Purpose-built screens for discovery problems generic review tools ignore. Live today: a multi-camera **Cameras** viewer (synced bodycam/dashcam video, transcript, and timeline), plus **forensics** tools to view and understand phone/CSLI material — starting with **Cellebrite messages** and **CSLI** maps, with more forensic views planned.
5. **Write lasting notes** — Keep timelines, people, cross outlines, and theory in a blank Markdown notebook (not chat). Link pages to each other and to discovery files that open beside the note.
6. **Reuse defense skills** — Short playbooks in [`skills/`](skills/) (timeline, cross-examination, and the like) tell the AI how to build those notes the same way each time. The idea is repeatable structure, ideally improved over time by practicing defense attorneys — not one-off chat prompts.

## Benchmarks and projected cost

API spend and wall-clock time depend on **how much audio/video** is in the dump more than raw gigabytes. PDFs and images are mostly local CPU; bodycam, radio, and call audio drive the OpenAI bill and a large share of wait time.

Always run `marshal estimate "/path/to/case"` on the actual tree before you spend. Numbers below are **balanced-profile ballparks** (HQ ASR on, OCR escalate on, visual frames on, Voyage embeddings on) for mixed criminal discovery — PDFs, photos, bodycam/surveillance video, and call/radio audio.

### Reference workstation

Ingest timing below assumes a dedicated private host roughly like this (not a laptop left idle overnight only):

| Resource | Spec used for projections |
|---|---|
| CPU | Intel Core i7-14700 (20 cores / 28 threads) |
| RAM | 32 GB |
| Storage | Multi-terabyte local case volume (SSD/HDD array) |
| Parallelism | `--workers 4`–`8` (I/O + API bound; more workers help until the network or OpenAI rate limits) |
| GPU | Optional for local Whisper; default ASR is OpenAI API, so GPU is not required for the balanced profile |

Local Docling OCR and ffmpeg dominate CPU. HQ transcription is **API-bound** (upload + model time), so wall-clock does not equal audio duration.

### Mixed-discovery size bands

| Band | Rough production shape | Projected API cost (full ingest) | Projected wall-clock |
|---|---|---|---|
| **Small** | ~2–10 GB · dozens of files · **&lt;2 hours** A/V · a few hundred PDF pages | **~$1–8** | **~30–90 minutes** |
| **Medium** | ~15–50 GB · ~50–200 ingestible files · **~5–15 hours** A/V · mixed PDFs/photos | **~$5–30** | **~2–8 hours** |
| **Large** | ~100 GB–1 TB+ · hundreds–thousands of files · **~40–200+ hours** A/V · heavy BWC | **~$40–250+** | **Overnight to ~2–4 days** |

**Cost breakdown (typical share of the API bill):**

| Stage | What drives it | Share of API $ |
|---|---|---|
| ASR (HQ stack) | Minutes of audio/video speech | **~60–85%** |
| Visual captions | Photos + sampled video frames (default every 5s, cap 48/file) | **~10–25%** |
| OCR escalate | Bad Docling pages only | **~1–5%** |
| Entities / tags | Once per file with text | **~1–3%** |
| Voyage embeddings | Chunk count / tokens | **~1–5%** |

Cowork / Computer chat while reviewing is **subscription usage**, not in the ingest table.

### Anchored example (measured estimate)

One completed mixed case on this class of host (~18 GB on disk, **83** ingestible files: ~41 PDFs, 15 videos, 6 audio, plus images; **~9 hours** of media):

| Stage | Ballpark API $ |
|---|---|
| ASR (HQ) | ~$3.90–$4.90 |
| Visual frames | ~$1.00–$1.90 |
| OCR escalate | ~$0.10–$0.35 |
| Entities | ~$0.05–$0.15 |
| Voyage embed | ~$0.05–$0.10 |
| **Total API** | **~$5–$7.50** |

Projected wall-clock for that shape with 4–8 workers: **about 2–5 hours** (Docling on documents in parallel; ASR waits on API). A PDF-heavy dump with little A/V finishes faster and cheaper; a bodycam-heavy dump scales almost linearly with media minutes.

### How to tighten the numbers for your case

```bash
uv run marshal knobs          # active cost/quality profile
uv run marshal estimate "/path/to/case"
uv run marshal estimate "/path/to/case" --mode retranscribe   # ASR-only redo
```

Economy profile (`asr.hq = false`, fewer visual frames) cuts API spend and often wall-clock; max-quality raises both. Details live in [`src/marshal_oss/knobs.toml`](src/marshal_oss/knobs.toml).

**Bottom line (same punchline as the intro):** a medium-size criminal case ≈ **$20 one-time** ingest + **$20/month** Claude Pro to analyze it with frontier models.

---

## How it fits together

```
  Production on disk
         │
         ▼
  Marshal ingest (local)  ──►  SQLite: text chunks + Voyage vectors + keyword index
                                         │
                ┌────────────────────────┴─────────────────────────┐
                ▼                                                  ▼
     MCP  /mcp :7340                                    Web UI + REST :7340
     Cowork / Computer                                  overview, explorer, notebook,
     (questions, drafting,                              custom viewers, search / facets
      Outlook, Word, …)
```

| Layer | Role | Cost |
|---|---|---|
| Marshal | Ingest, search, file links, MCP, native review UI | Mostly local compute; small API spend (see below) |
| Cowork / Computer | Questions, analysis, drafting, connectors | Subscription usage |

**Cost vs quality before you spend:** edit [`src/marshal_oss/knobs.toml`](src/marshal_oss/knobs.toml) (or copy to `~/.marshaloss/knobs.toml`), then run `marshal estimate "/path/to/case"` for a **full ingest** ballpark (ASR + OCR escalate + visual frames + entities + Voyage embeddings). Use `marshal knobs` to see what is active. ASR-only redos are `estimate --mode retranscribe`.

Marshal never answers questions itself. `search` / `ask` return text chunks and file links; Cowork or Computer writes the response.

---

## Covered file types

Default allowlist (override with `MARSHALOSS_INGEST_EXTENSIONS`):

| Kind | Extensions |
|---|---|
| Documents | `.pdf` `.docx` `.doc` `.pptx` `.ppt` `.xlsx` `.xls` `.csv` `.txt` `.md` `.html` `.htm` |
| Email | `.eml` `.msg` |
| Metadata (plain text) | `.xml` `.kml` `.st3` (e.g. camera recording logs, GPS tracks) |
| Images | `.png` `.jpg` `.jpeg` `.tif` `.tiff` `.bmp` `.webp` |
| Audio | `.mp3` `.wav` `.m4a` `.aac` `.ogg` `.flac` |
| Video | `.mp4` `.mov` `.avi` `.mkv` `.webm` |
| Other A/V (via ffmpeg) | `.wma` `.wmv` `.flv` `.asf` `.ts` `.m4v` `.mpeg` `.mpg` `.3gp` `.caf` `.dav` `.vob` `.h264` `.m2ts` `.mts` `.mxf` |

Zips are expanded automatically. If Docling or ffmpeg cannot open a file, convert it with the tool that produced it and re-ingest. PST/OST mail stores are not fully ingested as searchable text in v1 — see [docs/EXTENSIONS.md](docs/EXTENSIONS.md). Cellebrite `.ufdr` packages and CSLI exports are handled for the **Forensics** viewers (messages / map), not as a full substitute for a lab-grade phone exam (see [Note on forensic review](#note-on-forensic-review)).

---

## Ingest pipeline

`marshal ingest` turns a folder of discovery into searchable text and vectors, and records what worked. Files run in parallel (`--workers`). There are no file-size limits.

```
  1. PREPARE    unzip, hash every file, guess Bates, write manifest      local, $0
        │
        ▼  (each file, in parallel)
  2. EXTRACT    Docling OCR/parse locally; audio/video → OpenAI          local + A/V API
                transcription (speakers, line times)
        │
        ▼
  3. QUALITY    score the text; re-run with paid AI only if it's bad     API only on failure
        │
        ▼
  4. VISUAL     vision model describes photos and video frames           cheap API
        │
        ▼
  5. ENTITIES   per-file list of people/places + topic tags              cheap API
        │
        ▼
  6. INDEX      Voyage embeddings + SQLite keyword index; update manifest  Voyage API
```

**1. Prepare.** Expands zips, hashes every file (SHA-256), guesses Bates numbers from paths, and writes a **production manifest** — a list of everything produced. This is your "did we get everything?" check before any OCR runs.

**2. Extract.** [Docling](https://github.com/docling-project/docling) runs on your machine and turns documents, email, and images into text. Audio and video use the **HQ transcription stack** (ported from legacy Marshal): OpenAI diarize finds speech windows, each window is re-transcribed with `gpt-4o-transcribe` + logprobs and a BWC prompt, and borderline windows are verified with `whisper-1`. **ffmpeg** pulls the audio track. Set `MARSHALOSS_ASR_HQ=false` to fall back to a single `gpt-4o-transcribe-diarize` pass. Without an OpenAI key, or with `ASR_PROVIDER=local`, Docling's local Whisper is used instead. Videos are also classified as bodycam, dashcam, or other, with device, serial, and recording start where the filename or video metadata provides them. Re-run media ASR with `marshal retranscribe` (keeps visual frame captions).

**3. Quality gate.** Local checks score the output. Bad document OCR (empty, gibberish, mojibake) goes to an OpenAI or Anthropic vision model, capped at 25 pages per file by default. In local transcription mode, a bad transcript (empty, looping, junk like "thanks for watching") is re-run on OpenAI. If local extraction did fine, no API call is made.

**4. Visual index.** OCR doesn't tell you what's *in* a scene photo. A vision model writes a short factual description (clothing, vehicles, objects, who is where), which is indexed like any other text — so "red hoodie in the hallway" or "silver sedan" can match. Video gets the same treatment on sampled frames: every 5 seconds, up to 48 frames; longer videos get the 48 frames spread evenly across the whole recording. Each frame description keeps its time in the video. Images that are really document scans with plenty of OCR text are skipped.

**5. Entities and tags.** Search finds passages; entities answer a different question: *what names and topics does this file involve?* A cheap chat model reads each file once and produces a short cover sheet focused on **people, officers, organizations, and places** (noise like clock stamps and “Speaker A/B” is filtered). **Tags** are short topic labels (`bodycam`, `dui`, `use_of_force`, …), kept sparse. Person name variants are collapsed (e.g. “Jane Doe” / “Jane Q. Doe”). In the UI, clicking an entity or tag in a document opens every other file that shares it.

**6. Index.** Keyword search (SQLite FTS5) finds exact words but misses that "the booking" and "processed at central" mean the same thing. **Voyage** (`voyage-law-2`) converts each text chunk into a vector that represents its meaning. Searches run keyword, semantic, or hybrid (default, combines both). Voyage only powers search — it doesn't answer questions. Without a Voyage key, a weak local fallback is used; fine for testing, not real cases.

### API calls by stage

| Stage | Service | When | Rough cost |
|---|---|---|---|
| Prepare, Extract (documents) | none (local) | Always | $0 API; dominates runtime |
| Extract (audio/video) | HQ stack (diarize + `gpt-4o-transcribe` + whisper verify) | Every audio/video file | See ballparks below; HQ is ~1.2–2.5× a single diarize pass |
| Quality — documents | OpenAI or Anthropic vision | OCR looks bad | Only failures |
| Visual | OpenAI or Anthropic vision | Photos, video frames | Cheap |
| Entities | OpenAI mini or Anthropic Haiku | Each file with text | Cheap |
| Index + search | Voyage | Every chunk, every search query | Cents to low dollars per case |

**ASR is usually the largest API line** and scales with audio/video duration (OpenAI’s public estimate is on the order of ~$0.006/min per model pass; HQ is roughly 1.2–2.5× a single diarize pass depending on how much speech is present). Do not guess from another matter — run `marshal estimate` on **this** tree. Default `--mode ingest` covers the whole case (docs + images + A/V: ASR, OCR escalate, visual, entities, embeddings). Add `--mode retranscribe` for an ASR-only redo estimate.

### Ingest confidence

Each manifest row records `ingest_status` (`ready` / `error` / `skipped` / …), `quality_score` (0–1), `quality_reasons`, `chunk_count`, whether OCR/ASR was re-run, and the file's entities/tags. View it at `/api/manifest` and `/api/manifest/summary`, or on disk at `<root>/.marshaloss/ingest_confidence.json`.

---

## Services, API keys, and local tools

### Paid API services (used during ingest and search)

| Service | Key | What it is | Why Marshal needs it | Required? |
|---|---|---|---|---|
| [Voyage AI](https://www.voyageai.com/) ([docs](https://docs.voyageai.com/)) | `VOYAGE_API_KEY` | Embedding service. Turns text into vectors that represent meaning. `voyage-law-2` is tuned for legal text. | Semantic search: finding passages by meaning, not just exact words. Called for every text chunk at ingest and for every search query. | Strongly recommended. Without it, search falls back to a weak local method. |
| [OpenAI API](https://platform.openai.com/) ([keys](https://platform.openai.com/api-keys)) | `OPENAI_API_KEY` | API access to OpenAI models (billed separately from a ChatGPT subscription). | HQ ASR for bodycam (diarize → accurate re-transcribe → verify). `gpt-4o-mini` re-OCRs bad pages, describes photos and video frames, and extracts entities/tags. | Recommended. Without it, audio/video uses the weaker local Whisper model. |
| [Anthropic API](https://www.anthropic.com/api) ([console](https://console.anthropic.com/)) | `ANTHROPIC_API_KEY` | API access to Claude models (billed separately from a Claude subscription). | Claude Haiku can do OCR re-runs, photo/frame descriptions, and entities/tags instead of OpenAI. | Optional. An alternative to OpenAI for everything except transcription. |

If neither OpenAI nor Anthropic is configured, ingest still works. Audio/video is transcribed locally, poor OCR and transcripts are kept as-is, and photo descriptions and entities are skipped.

Keys can be set as environment variables or loaded from `~/.marshal/env.sh`.

### AI front end (subscriptions, not API keys)

| Service | What it is | Why |
|---|---|---|
| [Claude](https://claude.ai/) (Cowork / Desktop) | Anthropic's Claude app with agent features and MCP support | Where you ask questions, analyze, and draft against the case |
| [Perplexity](https://www.perplexity.ai/) (Computer) | Perplexity's agent product | Alternative front end with its own connectors |

Marshal does not need keys for these. They connect to Marshal over MCP.

### What leaves your machine

Confidentiality matters in defense work, so be aware of what each service receives:

| Service | Receives |
|---|---|
| Voyage | Extracted text chunks and search queries |
| OpenAI | The audio track of every audio/video file (for transcription) |
| OpenAI / Anthropic | Page images that failed OCR, photos and video frames, and extracted text for entity tagging |
| Cowork / Computer | Whatever search results and file content the assistant retrieves while you work |

Review each provider's data retention and training terms, and your jurisdiction's ethics guidance, before using them on client material. To keep everything local, set `MARSHALOSS_EMBED_BACKEND=hash`, `MARSHALOSS_ASR_PROVIDER=local`, `MARSHALOSS_OCR_ESCALATE=false`, `MARSHALOSS_ASR_ESCALATE=false`, `MARSHALOSS_VISUAL_ANALYZE=false`, and `MARSHALOSS_ENTITY_EXTRACT=false`. You lose semantic search and AI cleanup, but nothing is sent out during ingest.

### Local tools (free, run on your machine)

| Tool | What it is | Why |
|---|---|---|
| [Docling](https://github.com/docling-project/docling) | Open-source document conversion: OCR, parsing, speech-to-text | First-pass text extraction for every file |
| [ffmpeg](https://ffmpeg.org/) | Standard open-source audio/video toolkit | Converts other A/V formats for transcription and samples video frames. Install with your OS package manager and keep it updated. |
| [SQLite](https://www.sqlite.org/) | Embedded database (built into Python) | Stores text, vectors, and the keyword index |
| Python 3.12+ and [uv](https://docs.astral.sh/uv/) | Runtime and package manager | Runs Marshal |

---

## Quick start

Install the local tools above and set your API keys first.

```bash
cd ~/Projects/Marshal
uv sync

# Optional: manifest only, before extraction
uv run marshal prepare "/path/to/case" --slug my-case --name "My Case"

# Full ingest
uv run marshal ingest "/path/to/case" --slug my-case --name "My Case" --workers 8

# Cost/quality: knobs, then estimate full ingest (docs + images + A/V)
uv run marshal knobs
uv run marshal estimate "/path/to/case"

# Re-run HQ ASR on selected media (preserves visual captions)
uv run marshal estimate --slug my-case --mode retranscribe --path-contains Body
uv run marshal retranscribe --slug my-case --path-contains Body

# Serve (local default http://127.0.0.1:7340), or ./tools/networking/serve.sh
uv run marshal serve

# CLI search
uv run marshal search "bodycam booking" --mode hybrid --top-k 8
```

Endpoints: `/` (case review UI), `/health`, `/api/manifest`, `/api/files/categories`, `/mcp`.

### Connect Cowork / Claude Desktop

```json
"marshal": {
  "command": "npx",
  "args": ["-y", "mcp-remote", "http://<HOST>:7340/mcp", "--allow-http", "--transport", "http-only"]
}
```

Set `MARSHALOSS_PUBLIC_BASE` to the same base URL so file open/download links work.

| MCP tool | Purpose |
|---|---|
| `list_cases` / `get_active_case` / `set_active_case` | Case selection |
| `get_instructions` | Short Cowork playbook pointer (`skills/cowork.md`); `full=true` for the whole skill. Also `GET /api/mcp/instructions` |
| `browse_case_files` | Read-only directory listing with Bates and links |
| `search` | Read-only hybrid/keyword/semantic hits (structured chunks to cite) |
| `ask` | Same hybrid retrieval as `search`, plus a packed `context` string for the host LLM to answer |
| `get_file` | Read-only: metadata + extracted text, max 40,000 chars per call; `truncated` / `notice` / `next_offset_chars` say how to keep reading; optional chunk paging |
| `forensics_overview` / `csli_events` / `forensics_search` | Forensic sources in the case; CSLI rows (max 100 per call, paged); keyword search over CSLI + forensic file names |
| `cellebrite_chats` / `cellebrite_messages` / `cellebrite_search` | Read-only phone chats from `.ufdr` extractions: list threads, read one (paged), search message text across phones |
| `get_file_link` / `open_file_browser` | Read-only file / UI links |
| `set_file_note` / `set_file_hot_doc` / `list_flagged_files` / `set_file_tags` | **Writable annotations only** (SQLite meta — not the dump on disk). Notes + hot docs show in Explorer → Notes |
| `notebook_list` / `notebook_get` / `notebook_upsert` / `notebook_delete` | **Writable** case wiki pages |
| `notebook_skill` | **Lists** all skills in `skills/`; pass `name` to load one (mirrored at `Meta/Skills/*`) |
| `notebook_build_witness_pack` | Optional helper to seed wiki `Witnesses/*` pages (not discovery files) |

**Discovery is source of truth and read-only over MCP.** Tools cannot add, delete, or modify production files under the case root. Ingest remains a separate ops/CLI path. Editable surfaces: notebook, per-file `user_note`, per-file `hot_doc`, per-file `user_tags`.

---

## Human review (native web UI)

Case management and document review run in Marshal on `:7340` (`web/app.html`):

- **Cases** — pick a matter
- **Case Overview** — production health (manifest / OCR confidence), plus drillable **entities** and **tags** (letter jumplist, counts, matching docs, in-place preview)
- **Document Explorer** — **Categories** or **Native folders**; large hybrid search (matching files only, side preview, yellow hit highlights); Back + breadcrumbs when drilling; open a file’s entities/tags to jump to every doc that shares them
- **Notebook** — blank Markdown wiki with sidebar tree, Back + breadcrumbs, and stock top-level areas AI should prefer (`Home`, `Timeline`, `Witnesses`, `People`, `Cross`, `Theory`, `Motions`, `Discovery`, `Meta`). Git-backed recipes live in [`skills/`](skills/) (`cowork`, `cross-examination`, `timeline`, …), mirrored at `Meta/Skills/*`. MCP `notebook_skill` lists or loads them. `[[doc:]]` cites open a **side viewer** beside the page
- **Custom viewers** — defense-attuned tools in the case nav (**Cameras**, **Forensics**; see below)

Stack layout: [docs/DUAL_STACK.md](docs/DUAL_STACK.md).

---

## Defense tools (custom viewers)

Generic document review is not enough for every discovery problem. Marshal is meant to grow **custom viewers** tuned to defense needs — purpose-built screens on the same case data.

- **Built and served by Marshal.** The tool reads Marshal's own data (transcripts, frame descriptions, timestamps, file metadata, forensic exports), so it needs no separate processing and also works on its own in a browser.
- **Fed by ingest.** Where a tool needs special inputs (bodycam metadata, UFDR chat indexes, CSLI coordinates), ingest detects and stores them automatically.

| Viewer | What it does | Status |
|---|---|---|
| **Cameras** | Officer video (bodycam, dashcam, and related) with synced, clickable transcript and frame descriptions; cameras grouped by device/serial (or renamed officer filenames); timeline aligned by recording time, with per-camera clock offsets; transcript search | Live — case nav **Cameras** (`/api/bwc/index`) |
| **Forensics · Cellebrite** | Browse chats from Cellebrite `.ufdr` phone extractions (pick a phone, app, thread) | Live — case nav **Forensics** |
| **Forensics · CSLI** | Map of cell-site / CDR location events by phone and day (sectors, timeline, pins) | Live — case nav **Forensics** |

More forensic views are planned (same Forensics area). Ingest stores timed speech/visual segments plus camera type, device, serial, and recording start for bodycam. Axon exports usually have **serials, not officer names** in the filename; set `meta.media.officer` to override a lane label, or rename files like `Det Smith (1).mp4` when that convention is used.

---

## Configuration

### Cost / quality knobs (annotated)

**Start here to trade quality for spend.** The central knob file is [`src/marshal_oss/knobs.toml`](src/marshal_oss/knobs.toml) — written for humans and LLMs: each section says what the control does, how it moves **cost vs quality**, and which provider it hits. Shipped defaults are unchanged (`active_profile = "balanced"`).

```bash
# What knobs are active right now
uv run marshal knobs

# Full ingest estimate: ASR + OCR escalate + visual + entities + Voyage embeddings
uv run marshal estimate "/path/to/case"
uv run marshal estimate --slug my-case

# ASR-only (retranscribe) estimate — not the default
uv run marshal estimate --slug my-case --mode retranscribe --path-contains Body
```

To tweak: copy the file to `~/.marshaloss/knobs.toml` (or set `MARSHALOSS_KNOBS_FILE`), edit, then re-check with `knobs` / `estimate`. Profiles `economy` and `max_quality` are optional overlays. Environment variables still win.

**Default API providers:** OpenAI for ASR (required for HQ / diarize) and, when `provider = "auto"`, for OCR escalate / visual / entities. **Voyage** (`voyage-law-2`) for embeddings — not OpenAI. Anthropic is optional for non-ASR stages only.

Default `estimate` scans the **entire case tree** (or DB case): documents, images, audio, and video. `--mode retranscribe` narrows the bill to ASR only (visual captions are kept). Optional `--path-contains` (case-insensitive) limits which files are counted.

### Environment variables

Prefix `MARSHALOSS_` (kept for compatibility with existing installs; the product name is **Marshal**). Data still lives under `~/.marshaloss` and case caches under `<root>/.marshaloss` so they do not collide with older `~/.marshal` tooling.

| Variable | Default | Meaning |
|---|---|---|
| `DATA_DIR` | `~/.marshaloss` | SQLite + local data |
| `DB_PATH` | `$DATA_DIR/marshal_oss.db` | Override DB path |
| `KNOBS_FILE` | `~/.marshaloss/knobs.toml` if present | Override path to knobs TOML |
| `HOST` / `PORT` | `0.0.0.0` / `7340` | Bind address |
| `PUBLIC_BASE` | `http://127.0.0.1:7340` | Base URL for links (local, Tailscale, tunnel, …) |
| `INGEST_WORKERS` | `4` | Parallel files (`--workers` / `-j`) |
| `EMBED_BACKEND` / `EMBED_MODEL` | `voyage` / `voyage-law-2` | `voyage` or `hash` |
| `OCR_ESCALATE` | `true` | Vision OCR re-run when quality fails |
| `OCR_ESCALATE_PROVIDER` | `auto` | `auto` / `openai` / `anthropic` |
| `OCR_ESCALATE_MAX_PAGES` | `25` | Max AI OCR pages per file |
| `OCR_ESCALATE_MIN_SCORE` | `0.72` | Quality threshold |
| `ASR_PROVIDER` / `ASR_MODEL` | `openai` / `gpt-4o-transcribe-diarize` | Audio/video; `local` = Docling Whisper |
| `ASR_HQ` / `ASR_HQ_PROMPT` | `true` / BWC prompt | Legacy HQ stack on by default; set `false` for single-pass diarize |
| `ASR_ESCALATE` | `true` | In local mode, re-run poor transcripts on `ASR_MODEL` |
| `VISUAL_ANALYZE` | `true` | Photo / video frame descriptions |
| `VISUAL_FRAME_INTERVAL_SEC` / `VISUAL_MAX_FRAMES` | `5` / `48` | Video sampling; frames spread across long videos (`0` = no cap) |
| `ENTITY_EXTRACT` | `true` | Per-file entities/tags |
| `ENTITY_EXTRACT_SEGMENT_CHARS` | `14000` | Segment size for long files |
| `ENTITY_EXTRACT_MAX_CHARS` | `0` | `0` = all extracted text |
| `EXPAND_ZIPS` / `EXPAND_ZIP_MAX_DEPTH` | `true` / `2` | Unzip before ingest; nested depth |
| `INGEST_EXTENSIONS` | see above | Allowed suffixes |
| `BATES_PATTERN` | built-in | Bates regex |

Bare `VOYAGE_API_KEY`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY` are also accepted.

---

## Note on forensic review

Marshal can **ingest and surface** some phone and location material for counsel review — today mainly **Cellebrite messages** (from `.ufdr`) and **CSLI / tower dumps** on a map. The goal is faster, clearer review of those slices of a production, not a full digital-forensics lab product.

It will **never** substitute for nuanced forensic analysis of a device. A production UFED/UFDR export is not the whole phone; CSLI records are **serving-sector / tower events**, not GPS tracks of the handset. Hiring a qualified forensic analyst remains advisable when accuracy matters — for example to audit CSLI interpretation, recover artifacts the UFED file never captured, or challenge what the UI shows. Treat Marshal forensics views as a **starting point for review**, not a final expert opinion.

---

## Note for contributors

Contributions from practicing criminal defense attorneys are welcome. The most useful additions usually come from something that would have helped on a real case. Design decisions to follow:

**1. Cowork or Computer is the AI engine.** Questions and analysis belong in Claude Cowork or Perplexity Computer, not a custom chat product that spends API tokens on every query. Subscription usage is cheaper than per-query API calls over a large set, and users keep their connectors (Outlook, Word, calendars, CourtListener-style research, browsers) without this project reimplementing them. Marshal stays the ingest, search, and MCP layer.

**2. Wrap open-source tools; don't write parsers.** The project is not designed to maintain dozens of custom format libraries, especially for odd file types.

| Prefer | Avoid |
|---|---|
| Docling for documents, OCR, transcription | Homegrown PDF/Office/email parsers |
| ffmpeg for media conversion | Custom demuxers per vendor format |
| Extending the native Marshal review UI | A Relativity clone or third-party review dependency |
| Thin adapters around mature libraries | Format connectors only this project maintains |

If Docling or ffmpeg can't read a file, convert it upstream or add a thin adapter around an existing open project. Specialized defense viewers belong in this codebase; see [Defense tools](#defense-tools-specialized-viewers).

**3. Local first, unless a paid model is much better for little money.** Extract locally where local results are good, and call paid APIs when the local result is bad or missing. The exception is where a frontier model is far better at low cost: audio/video transcription goes to OpenAI by default because local Whisper misses names and words that matter.

**4. Defense workflow first.** Prefer features that help counsel find and trust material in a real production: Bates, manifests, ingest status/quality, searchable media.

---

## License

Marshal is licensed under the [GNU Affero General Public License v3.0](LICENSE) (AGPL-3.0-or-later).

In plain terms:

- **Free to use, study, and modify**, including in a law practice or public defender office.
- **Derivative works must stay open source** under the same license. You cannot fork this project into a closed-source product.
- **This includes hosted use.** If you run a modified version as a service for others (for example, hosting it for clients over the network), you must offer them the source code of your modified version.
- **Paid setup, hosting, and support are allowed**, as long as the software itself stays AGPL.

Contributions are accepted under the same license: by submitting a pull request, you agree your contribution is licensed under AGPL-3.0-or-later.

This is a plain-language summary, not legal advice. The [LICENSE](LICENSE) file controls.
