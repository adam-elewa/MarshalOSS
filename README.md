# MarshalOSS

Greenfield defense-discovery stack: **Docling** owns file-type/OCR/ASR maintenance; MarshalOSS owns case/Bates metadata, hybrid search, Tailscale file URLs, and **MCP** for Claude Desktop / Perplexity Computer.

## What it covers well

- PDF (text + OCR), Office/Excel, email (eml/msg), images, audio, video (via Docling)
- Bates labels derived from paths/filenames
- Fast keyword (FTS5) + semantic (Voyage or offline hash) hybrid search
- MCP tools: `search`, `ask`, `browse_case_files`, `get_file_link`, …
- Basic web browser with Bates + search

## What stays custom / later

- PST explosion, Cellebrite/UFDR — see [docs/EXTENSIONS.md](docs/EXTENSIONS.md)
- Full review/production/redaction workflows — human review via [FreeEed dual stack](docs/DUAL_STACK.md)

## Quick start (Gideon)

```bash
export PATH="$HOME/.local/bin:$PATH"
cd ~/Projects/MarshalOSS

# optional: source Voyage key
# source ~/.marshal/env.sh

# Smoke ingest (first N files)
uv run marshaloss ingest "/mnt/marshal/cases/Washington-Jersey City/OPIA-transcripts" \
  --slug washington-jersey-city \
  --name "Washington v. Jersey City" \
  --limit 5

# Or full case root (long)
# uv run marshaloss ingest "/mnt/marshal/cases/Washington-Jersey City" --slug washington-jersey-city

export MARSHALOSS_PUBLIC_BASE="http://$(tailscale ip -4):7340"
uv run marshaloss serve
```

Claude Desktop / mcp-remote:

```json
"marshaloss": {
  "command": "npx",
  "args": ["-y", "mcp-remote", "http://GIDEN_TAILSCALE_IP:7340/mcp", "--allow-http", "--transport", "http-only"]
}
```

Browser: `http://GIDEN_TAILSCALE_IP:7340/browser`
