# Forensics (MarshalOSS)

Thin, outsourced forensic viewers inside the Marshal web UI. Subsections only appear when a case has matching evidence.

## Cellebrite (UFDR)

Defense counsel usually receives a **Cellebrite Reader** package / `.ufdr` export. That file is a zip containing `report.xml` with chats Cellebrite already decoded (WhatsApp, SMS, iMessage, etc.).

Marshal does **not** reimplement Physical Analyzer. It uses **[ufedlib-py](https://pypi.org/project/ufedlib-py/)** (Python port of UFEDLib) to read the same `Chat` / message models from the UFDR. Content should match what Reader shows for artifacts included in that export.

| Need | Tool |
|------|------|
| Same chats as Reader for this UFDR | `ufedlib-py` (this tab) |
| Deeper recovery / full forensic suite | [IPED](https://github.com/sepinf-inc/IPED) outside Marshal |

**Ingest:** `marshaloss ingest` discovers `.ufdr` packages under the case root, parses chats + a contact directory (for name resolution), and writes a warm cache under `.marshaloss/forensics/ufdr/`. The Forensics tab then opens without a cold parse. Re-ingest reuses the cache when the UFDR bytes are unchanged.

Parsed chat indexes are also built on first open if ingest has not run yet.

## Cell Site Location Information (CSLI)

Landing uses a cell-tower hero card when CDR / tower-dump spreadsheets with coordinates are found. The viewer is a **map** (towers + sector wedges + call/location pings)—not the raw spreadsheet. Parsed geo cache lives under `.marshaloss/forensics/csli/`.

## Design rules

- Prefer existing open tools over custom parsers.
- Keep the Marshal UI thin: list sources → list chats → read messages.
- Never invent chat content; only surface what the UFDR report contains.
