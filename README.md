# MarshalOSS

> **Goal:** Creatively deploy cutting-edge frontier AI models, at the lowest possible cost, to search and analyze the most complex and voluminous discovery on behalf of the poorest clients against the most well-resourced adversaries.

Use **Claude Cowork** or **Perplexity Computer** to review and analyze criminal defense discovery: PDFs (including bad or missing OCR), emails, bodycam and surveillance video, radio and call audio, photos, forensic dumps, and similar production material.

MarshalOSS runs on your computer. It extracts text, builds a searchable index, and exposes the case over MCP. You ask questions in Cowork or Computer, so query cost stays close to a normal subscription instead of enterprise e-discovery AI rates. It is meant to be usable on **CJA appointments, public defender work, and smaller private cases**.

License: AGPL-3.0 (see [License](#license)). Private: [github.com/adam-elewa/MarshalOSS](https://github.com/adam-elewa/MarshalOSS).

## Objective and scope

Commercial AI-over-discovery tools are priced for large civil matters. Criminal cases often have the same volume and odd file types without the budget — a routine case can include hundreds of gigabytes of audio, video, and forensic exports.

The project does two things at low cost, while still using paid models when local extraction is wrong or empty:

1. **AI search and analysis** over the production (MarshalOSS + Cowork / Computer)
2. **Basic human review** — browsing, tagging, and annotating (via **Datashare**)

It is not designed to rebuild Relativity. It wires together existing open-source tools (Docling, Datashare, ffmpeg, SQLite, MCP) and adapts them for defense work. It is not a full enterprise review desk (privilege logs, redaction workflows, reviewer assignment).

**Hosting:** the project is hosting-agnostic. Do not expose the port on the public internet without auth; prefer **Tailscale** or a **Cloudflare Tunnel**. Optional helpers are in [`tools/networking/`](tools/networking/README.md).

---

## How it fits together

```
  Production on disk
         │
         ▼
  MarshalOSS ingest (local)  ──►  SQLite: text chunks + Voyage vectors + keyword index
                                         │
                ┌────────────────────────┼─────────────────────────┐
                ▼                        ▼                         ▼
     MCP  /mcp :7340           REST / browser :7340        Datashare :8100
     Cowork / Computer         search, manifest, files     browse / tag / annotate
     (questions, drafting,
      Outlook, Word, …)
```

| Layer | Role | Cost |
|---|---|---|
| MarshalOSS | Ingest, search, file links, MCP | Mostly local compute; small API spend (see below) |
| Cowork / Computer | Questions, analysis, drafting, connectors | Subscription usage |
| Datashare | Human browse / tag / annotate | Open source (AGPL upstream) |

MarshalOSS never answers questions itself. `search` / `ask` return text chunks and file links; Cowork or Computer writes the response.

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

Zips are expanded automatically. If Docling or ffmpeg cannot open a file, convert it with the tool that produced it and re-ingest. PST/OST and Cellebrite/UFDR are not in v1 — see [docs/EXTENSIONS.md](docs/EXTENSIONS.md).

---

## Ingest pipeline

`marshaloss ingest` turns a folder of discovery into searchable text and vectors, and records what worked. Files run in parallel (`--workers`). There are no file-size limits.

```
  1. PREPARE    unzip, hash every file, guess Bates, write manifest      local, $0
        │
        ▼  (each file, in parallel)
  2. EXTRACT    Docling OCR/parse/transcribe; ffmpeg for other A/V       local, $0
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

**2. Extract.** [Docling](https://github.com/docling-project/docling) runs on your machine and turns documents, email, images, and common audio/video into text. For other A/V formats, **ffmpeg** (installed on the host, not part of this repo) converts to WAV first. Transcripts keep the start and end time of every line. Videos are also classified as bodycam, dashcam, or other, with device, serial, and recording start where the filename or video metadata provides them. This step costs compute time, not API money, and is most of the runtime on a large case.

**3. Quality gate.** Local checks score the output. Bad document OCR (empty, gibberish, mojibake) goes to an OpenAI or Anthropic vision model, capped at 25 pages per file by default. A bad transcript (empty, looping, junk like "thanks for watching") goes to OpenAI `gpt-4o-transcribe`. If Docling did fine, no API call is made.

**4. Visual index.** OCR doesn't tell you what's *in* a scene photo. A vision model writes a short factual description (clothing, vehicles, objects, who is where), which is indexed like any other text — so "red hoodie in the hallway" or "silver sedan" can match. Video gets the same treatment on sampled frames: every 5 seconds, up to 48 frames; longer videos get the 48 frames spread evenly across the whole recording. Each frame description keeps its time in the video. Images that are really document scans with plenty of OCR text are skipped.

**5. Entities and tags.** Search finds passages; entities answer a different question: *what names and topics does this file involve?* A cheap chat model reads each file once and produces a cover sheet:

- **Entities** — people, officers, organizations, places, dates, Bates-like IDs
- **Tags** — short topic labels (`bodycam`, `dui`, `use_of_force`, `medical`, …)

It merges name variants, so "Smith", "J. Smith", and "Jane Smith" become one entry. That helps when a production spells the same officer five ways, and lets you ask "which files mention Officer Rivera?" without crafting the perfect search query. Results are stored per file, not per chunk.

**6. Index.** Keyword search (SQLite FTS5) finds exact words but misses that "the booking" and "processed at central" mean the same thing. **Voyage** (`voyage-law-2`) converts each text chunk into a vector that represents its meaning. Searches run keyword, semantic, or hybrid (default, combines both). Voyage only powers search — it doesn't answer questions. Without a Voyage key, a weak local fallback is used; fine for testing, not real cases.

### API calls by stage

| Stage | Service | When | Rough cost |
|---|---|---|---|
| Prepare, Extract | none (local) | Always | $0 API; dominates runtime |
| Quality — documents | OpenAI or Anthropic vision | OCR looks bad | Only failures |
| Quality — A/V | OpenAI `gpt-4o-transcribe` | Transcript looks bad | Can dominate A/V-heavy cases |
| Visual | OpenAI or Anthropic vision | Photos, video frames | Cheap |
| Entities | OpenAI mini or Anthropic Haiku | Each file with text | Cheap |
| Index + search | Voyage | Every chunk, every search query | Cents to low dollars per case |

### Ingest confidence

Each manifest row records `ingest_status` (`ready` / `error` / `skipped` / …), `quality_score` (0–1), `quality_reasons`, `chunk_count`, whether OCR/ASR was re-run, and the file's entities/tags. View it at `/api/manifest` and `/api/manifest/summary`, or on disk at `<root>/.marshaloss/ingest_confidence.json`.

---

## Services, API keys, and local tools

### Paid API services (used during ingest and search)

| Service | Key | What it is | Why MarshalOSS needs it | Required? |
|---|---|---|---|---|
| [Voyage AI](https://www.voyageai.com/) ([docs](https://docs.voyageai.com/)) | `VOYAGE_API_KEY` | Embedding service. Turns text into vectors that represent meaning. `voyage-law-2` is tuned for legal text. | Semantic search: finding passages by meaning, not just exact words. Called for every text chunk at ingest and for every search query. | Strongly recommended. Without it, search falls back to a weak local method. |
| [OpenAI API](https://platform.openai.com/) ([keys](https://platform.openai.com/api-keys)) | `OPENAI_API_KEY` | API access to OpenAI models (billed separately from a ChatGPT subscription). | `gpt-4o-transcribe` re-transcribes bad audio/video. `gpt-4o-mini` re-OCRs bad pages, describes photos and video frames, and extracts entities/tags. | Recommended. It is the only option for transcription re-runs. |
| [Anthropic API](https://www.anthropic.com/api) ([console](https://console.anthropic.com/)) | `ANTHROPIC_API_KEY` | API access to Claude models (billed separately from a Claude subscription). | Claude Haiku can do OCR re-runs, photo/frame descriptions, and entities/tags instead of OpenAI. | Optional. An alternative to OpenAI for everything except transcription. |

If neither OpenAI nor Anthropic is configured, ingest still works. Poor OCR and transcripts are kept as-is, and photo descriptions and entities are skipped.

Keys can be set as environment variables or loaded from `~/.marshal/env.sh`.

### AI front end (subscriptions, not API keys)

| Service | What it is | Why |
|---|---|---|
| [Claude](https://claude.ai/) (Cowork / Desktop) | Anthropic's Claude app with agent features and MCP support | Where you ask questions, analyze, and draft against the case |
| [Perplexity](https://www.perplexity.ai/) (Computer) | Perplexity's agent product | Alternative front end with its own connectors |

MarshalOSS does not need keys for these. They connect to MarshalOSS over MCP.

### What leaves your machine

Confidentiality matters in defense work, so be aware of what each service receives:

| Service | Receives |
|---|---|
| Voyage | Extracted text chunks and search queries |
| OpenAI / Anthropic | Page images that failed OCR, audio that failed transcription, photos and video frames, and extracted text for entity tagging |
| Cowork / Computer | Whatever search results and file content the assistant retrieves while you work |

Review each provider's data retention and training terms, and your jurisdiction's ethics guidance, before using them on client material. To keep everything local, set `MARSHALOSS_EMBED_BACKEND=hash`, `MARSHALOSS_OCR_ESCALATE=false`, `MARSHALOSS_ASR_ESCALATE=false`, `MARSHALOSS_VISUAL_ANALYZE=false`, and `MARSHALOSS_ENTITY_EXTRACT=false`. You lose semantic search and AI cleanup, but nothing is sent out during ingest.

### Local tools (free, run on your machine)

| Tool | What it is | Why |
|---|---|---|
| [Docling](https://github.com/docling-project/docling) | Open-source document conversion: OCR, parsing, speech-to-text | First-pass text extraction for every file |
| [ffmpeg](https://ffmpeg.org/) | Standard open-source audio/video toolkit | Converts other A/V formats for transcription and samples video frames. Install with your OS package manager and keep it updated. |
| [SQLite](https://www.sqlite.org/) | Embedded database (built into Python) | Stores text, vectors, and the keyword index |
| [Datashare](https://github.com/ICIJ/datashare) | Open-source document review app from ICIJ (runs in Docker) | Human browse / tag / annotate |
| Python 3.12+ and [uv](https://docs.astral.sh/uv/) | Runtime and package manager | Runs MarshalOSS |

---

## Quick start

Install the local tools above and set your API keys first.

```bash
cd ~/Projects/MarshalOSS
uv sync

# Optional: manifest only, before extraction
uv run marshaloss prepare "/path/to/case" --slug my-case --name "My Case"

# Full ingest
uv run marshaloss ingest "/path/to/case" --slug my-case --name "My Case" --workers 8

# Serve (local default http://127.0.0.1:7340), or ./tools/networking/serve.sh
uv run marshaloss serve

# CLI search
uv run marshaloss search "bodycam booking" --mode hybrid --top-k 8
```

Endpoints: `/` and `/browser` (file browser), `/health`, `/api/manifest`, `/mcp`.

### Connect Cowork / Claude Desktop

```json
"marshaloss": {
  "command": "npx",
  "args": ["-y", "mcp-remote", "http://<HOST>:7340/mcp", "--allow-http", "--transport", "http-only"]
}
```

Set `MARSHALOSS_PUBLIC_BASE` to the same base URL so file open/download links work.

| MCP tool | Purpose |
|---|---|
| `list_cases` / `get_active_case` / `set_active_case` | Case selection |
| `browse_case_files` | Directory listing with Bates and links |
| `search` | Keyword / semantic / hybrid hits with snippets and links |
| `ask` | Top chunks as context; the host model answers |
| `get_file_link` / `open_file_browser` | File links |

---

## Human review (Datashare)

Browsing, tagging, and annotating are handled by [Datashare](https://github.com/ICIJ/datashare), running beside MarshalOSS on `:8100`. Defense-specific review features (coding, tags, annotation, privilege, layouts) should be built by **extending or revising Datashare**, not by creating a separate review UI in this repo.

Setup: [`review/`](review/README.md). Details: [docs/DUAL_STACK.md](docs/DUAL_STACK.md). Both Datashare and MarshalOSS are AGPL-3.0.

---

## Defense tools (specialized viewers)

Some defense work needs a purpose-built view that a general review app will never ship — for example, watching several officers' bodycam footage on one synced timeline. These tools follow one pattern:

- **Built and served by MarshalOSS.** The tool reads MarshalOSS's own data (transcripts, frame descriptions, timestamps, file metadata), so it needs no separate processing and also works on its own in a browser.
- **Shown inside Datashare.** A thin Datashare plugin adds the tool as its own view: a sidebar page for case-wide views, and a panel on the matching documents. Reviewers stay in one app.
- **Fed by ingest.** Where a tool needs special inputs (like knowing which videos are bodycam), ingest detects and stores them automatically.

| Tool | What it does | Status |
|---|---|---|
| **Bodycam viewer** | Video with a synced, clickable transcript and frame descriptions; videos grouped by officer; a multi-camera timeline aligned by recording time, with per-camera clock offsets; transcript search | Planned (porting from legacy Marshal) |

Ingest already stores what the bodycam viewer needs: start/end times for every transcript line and frame description, and each video's camera type, device, serial, and recording start. Remaining work: serve the viewer from MarshalOSS and add the Datashare plugin.

New tools should follow the same pattern. General browse/tag/annotate features still belong in Datashare itself.

---

## Configuration

Environment variables, prefix `MARSHALOSS_`:

| Variable | Default | Meaning |
|---|---|---|
| `DATA_DIR` | `~/.marshaloss` | SQLite + local data |
| `DB_PATH` | `$DATA_DIR/marshal_oss.db` | Override DB path |
| `HOST` / `PORT` | `0.0.0.0` / `7340` | Bind address |
| `PUBLIC_BASE` | `http://127.0.0.1:7340` | Base URL for links (local, Tailscale, tunnel, …) |
| `INGEST_WORKERS` | `4` | Parallel files (`--workers` / `-j`) |
| `EMBED_BACKEND` / `EMBED_MODEL` | `voyage` / `voyage-law-2` | `voyage` or `hash` |
| `OCR_ESCALATE` | `true` | Vision OCR re-run when quality fails |
| `OCR_ESCALATE_PROVIDER` | `auto` | `auto` / `openai` / `anthropic` |
| `OCR_ESCALATE_MAX_PAGES` | `25` | Max AI OCR pages per file |
| `OCR_ESCALATE_MIN_SCORE` | `0.72` | Quality threshold |
| `ASR_ESCALATE` / `ASR_ESCALATE_MODEL` | `true` / `gpt-4o-transcribe` | Transcription re-run |
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

## Note for contributors

Contributions from practicing criminal defense attorneys are welcome. The most useful additions usually come from something that would have helped on a real case. Design decisions to follow:

**1. Cowork or Computer is the AI engine.** Questions and analysis belong in Claude Cowork or Perplexity Computer, not a custom chat product that spends API tokens on every query. Subscription usage is cheaper than per-query API calls over a large set, and users keep their connectors (Outlook, Word, calendars, CourtListener-style research, browsers) without this project reimplementing them. MarshalOSS stays the ingest, search, and MCP layer.

**2. Wrap open-source tools; don't write parsers.** The project is not designed to maintain dozens of custom format libraries, especially for odd file types.

| Prefer | Avoid |
|---|---|
| Docling for documents, OCR, transcription | Homegrown PDF/Office/email parsers |
| ffmpeg for media conversion | Custom demuxers per vendor format |
| Extending Datashare for review UX | A new review UI or Relativity clone |
| Thin adapters around mature libraries | Format connectors only this project maintains |

If Docling or ffmpeg can't read a file, convert it upstream or add a thin adapter around an existing open project. The one exception to "extend Datashare" is specialized defense viewers; see [Defense tools](#defense-tools-specialized-viewers).

**3. Local first; pay only when quality is bad.** Extract locally where possible and call paid vision, transcription, or embedding APIs only when the result is bad or missing.

**4. Defense workflow first.** Prefer features that help counsel find and trust material in a real production: Bates, manifests, ingest status/quality, searchable media.

---

## License

MarshalOSS is licensed under the [GNU Affero General Public License v3.0](LICENSE) (AGPL-3.0-or-later).

In plain terms:

- **Free to use, study, and modify**, including in a law practice or public defender office.
- **Derivative works must stay open source** under the same license. You cannot fork this project into a closed-source product.
- **This includes hosted use.** If you run a modified version as a service for others (for example, hosting it for clients over the network), you must offer them the source code of your modified version.
- **Paid setup, hosting, and support are allowed**, as long as the software itself stays AGPL.

Contributions are accepted under the same license: by submitting a pull request, you agree your contribution is licensed under AGPL-3.0-or-later.

This is a plain-language summary, not legal advice. The [LICENSE](LICENSE) file controls.
