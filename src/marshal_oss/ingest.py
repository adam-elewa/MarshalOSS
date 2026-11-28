from __future__ import annotations

import mimetypes
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Callable

from rich.console import Console

from .asr_escalate import ai_transcribe_file, resolve_asr_client, use_ai_asr
from .bates import extract_bates
from .config import Settings
from .db import Database
from .embed import Embedder
from .escalate import ai_ocr_file, resolve_ai_ocr_client
from .hashing import sha256_file
from .manifest import prepare_production
from .media_ffmpeg import as_docling_media, is_docling_native_media, media_duration_sec
from .media_info import describe_media, fmt_clock
from .quality import (
    extract_docling_confidence,
    is_media_path,
    join_chunk_text,
    score_extraction,
    score_transcript,
)
from .entities import extract_entities_for_chunks, resolve_entity_client
from .forensics_ufdr import ingest_ufdr_forensics
from .visual import is_image_path, visual_analyze_file

console = Console()


def file_sha256(path: Path, max_bytes: int | None = None) -> str:
    """Full-file SHA-256 by default (aligns with production manifest)."""
    return sha256_file(path, max_bytes=max_bytes)


def iter_case_files(root: Path, allowed: set[str]) -> list[Path]:
    files: list[Path] = []
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        if p.name.startswith("."):
            continue
        if p.suffix.lower() not in allowed:
            continue
        files.append(p)
    files.sort(key=lambda x: str(x).lower())
    return files


_PLAIN_TEXT_SUFFIXES = {".txt", ".md", ".csv", ".log"}
# Docling has no reader for these; read them as plain text without trying it.
_RAW_TEXT_ONLY_SUFFIXES = {".xml", ".kml", ".st3"}


def _chunk_plain_text(text: str, bates: str | None, max_chars: int = 1800) -> list[dict[str, Any]]:
    text = (text or "").strip()
    if not text:
        return []
    chunks = []
    i = 0
    while i < len(text):
        piece = text[i : i + max_chars]
        if len(piece) == max_chars:
            cut = piece.rfind("\n\n")
            if cut > max_chars // 3:
                piece = piece[:cut]
        chunks.append(
            {
                "text": piece.strip(),
                "page_start": None,
                "page_end": None,
                "heading": None,
                "bates": bates,
            }
        )
        i += max(len(piece), 1)
    return [c for c in chunks if c["text"]]


# Docling's HybridChunker re-tokenizes an oversize table while splitting it,
# which is effectively quadratic and never finishes on sheets with thousands
# of rows. Spreadsheets, and any doc whose tables are this large, get chunked
# by row group here instead, with the header row repeated.
_SPREADSHEET_SUFFIXES = {".xlsx", ".xls", ".xlsm", ".csv", ".tsv", ".ods"}
_BIG_TABLE_CELLS = 5000
_SPREADSHEET_ENTITY_CHARS = 28000


def _table_rows(table: Any) -> list[list[str]]:
    grid = getattr(getattr(table, "data", None), "grid", None) or []
    rows = []
    for row in grid:
        cells = [" ".join(str(getattr(c, "text", "") or "").split()) for c in row]
        if any(cells):
            rows.append(cells)
    return rows


def _table_cell_count(doc: Any) -> int:
    n = 0
    for tb in getattr(doc, "tables", None) or []:
        data = getattr(tb, "data", None)
        n += int(getattr(data, "num_rows", 0) or 0) * int(getattr(data, "num_cols", 0) or 0)
    return n


def _chunk_tables(doc: Any, max_chars: int = 1800) -> list[dict[str, Any]]:
    """One chunk per group of rows; each chunk repeats the header row."""
    chunks: list[dict[str, Any]] = []
    tables = getattr(doc, "tables", None) or []
    for ti, tb in enumerate(tables, 1):
        rows = _table_rows(tb)
        if not rows:
            continue
        header, body = rows[0], rows[1:] or []
        head_line = " | ".join(header)
        label = f"Table {ti}" if len(tables) > 1 else "Table"
        page = None
        for prov in getattr(tb, "prov", None) or []:
            page = getattr(prov, "page_no", None)
            break

        def flush(lines: list[str], first: int, last: int) -> None:
            chunks.append(
                {
                    "text": head_line + "\n" + "\n".join(lines),
                    "page_start": page,
                    "page_end": page,
                    "heading": f"{label} rows {first}–{last}",
                    "bates": None,
                }
            )

        if not body:
            flush([], 1, 1)
            continue
        lines: list[str] = []
        size = len(head_line)
        first = 2  # spreadsheet-style row numbers; row 1 is the header
        for i, row in enumerate(body, 2):
            line = " | ".join(row)
            if lines and size + len(line) + 1 > max_chars:
                flush(lines, first, i - 1)
                lines, size, first = [], len(head_line), i
            lines.append(line)
            size += len(line) + 1
        if lines:
            flush(lines, first, first + len(lines) - 1)
    # Any non-table text in the doc (titles, notes) as plain chunks.
    other = "\n\n".join(
        (getattr(t, "text", "") or "").strip() for t in getattr(doc, "texts", None) or []
    )
    return _chunk_plain_text(other, None, max_chars) + chunks


def docling_extract(path: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Use Docling to convert a file into text chunks + confidence metadata."""
    from docling.chunking import HybridChunker
    from docling.document_converter import DocumentConverter

    converter = DocumentConverter()
    result = converter.convert(str(path))
    doc = result.document
    confidence = extract_docling_confidence(result)

    page_count = None
    try:
        pages = getattr(doc, "pages", None) or getattr(doc, "num_pages", None)
        if isinstance(pages, int):
            page_count = pages
        elif pages is not None and hasattr(pages, "__len__"):
            page_count = len(pages)
    except Exception:
        page_count = None
    if page_count is None and confidence.get("pages"):
        page_count = len(confidence["pages"])
    confidence["page_count"] = page_count

    # Big spreadsheets / huge tables: skip HybridChunker (hangs) and chunk by rows.
    if path.suffix.lower() in _SPREADSHEET_SUFFIXES or _table_cell_count(doc) > _BIG_TABLE_CELLS:
        return [c for c in _chunk_tables(doc) if c.get("text")], confidence

    chunks: list[dict[str, Any]] = []
    seen_items: set[str] = set()
    # Chunker doc_items are bare DocItems without text; look text up by self_ref.
    text_by_ref = {
        str(t.self_ref): (getattr(t, "text", "") or "").strip()
        for t in getattr(doc, "texts", None) or []
    }
    try:
        chunker = HybridChunker()
        for ch in chunker.chunk(dl_doc=doc):
            text = (
                chunker.contextualize(ch)
                if hasattr(chunker, "contextualize")
                else (getattr(ch, "text", None) or "")
            )
            meta = getattr(ch, "meta", None)
            pages_found: list[int] = []
            headings: list[str] = []
            segments: list[dict[str, Any]] = []
            if meta is not None:
                headings = list(getattr(meta, "headings", None) or [])
                for item in getattr(meta, "doc_items", None) or []:
                    for prov in getattr(item, "prov", None) or []:
                        page = getattr(prov, "page_no", None)
                        if page is not None:
                            pages_found.append(int(page))
                    ref = str(getattr(item, "self_ref", "") or "")
                    for src in getattr(item, "source", None) or []:
                        if getattr(src, "kind", None) != "track":
                            continue
                        seg = {
                            "start": float(src.start_time),
                            "end": float(src.end_time),
                            "text": text_by_ref.get(ref) or (getattr(item, "text", "") or "").strip(),
                            "speaker": getattr(src, "voice", None),
                        }
                        if ref and ref in seen_items:
                            seg["dup"] = True
                        segments.append(seg)
                    if ref:
                        seen_items.add(ref)
            pages_found = sorted(set(pages_found))
            chunk: dict[str, Any] = {
                "text": (text or "").strip(),
                "page_start": pages_found[0] if pages_found else None,
                "page_end": pages_found[-1] if pages_found else None,
                "heading": " / ".join(str(h) for h in headings) if headings else None,
                "bates": None,
            }
            if segments:
                chunk["start_sec"] = min(s["start"] for s in segments)
                chunk["end_sec"] = max(s["end"] for s in segments)
                chunk["segments"] = [s for s in segments if s["text"] and not s.get("dup")]
                if not chunk["heading"]:
                    chunk["heading"] = (
                        f"Transcript @{fmt_clock(chunk['start_sec'])}"
                        f"–{fmt_clock(chunk['end_sec'])}"
                    )
            chunks.append(chunk)
    except Exception:
        try:
            md = doc.export_to_markdown()
        except Exception:
            md = doc.export_to_text() if hasattr(doc, "export_to_text") else str(doc)
        chunks = _chunk_plain_text(md, None)

    if not any(c.get("text") for c in chunks):
        try:
            md = doc.export_to_markdown()
        except Exception:
            md = ""
        chunks = _chunk_plain_text(md, None)

    return [c for c in chunks if c.get("text")], confidence


_LOCAL_ASR_LOCK = threading.Lock()


def _docling_media(
    path: Path,
    rel: str,
    meta: dict[str, Any],
    delta: dict[str, int],
    log: Callable[[str], None],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Local Docling Whisper; non-native formats go through an ffmpeg WAV first.

    One at a time: two long transcriptions on the shared Whisper model in
    parallel ingest workers interleave and one fails ("Pipeline VideoPipeline
    failed"). Local Whisper already uses every CPU core, so this costs nothing.
    """
    with _LOCAL_ASR_LOCK:
        if is_docling_native_media(path):
            return docling_extract(path)
        with as_docling_media(path) as (media_path, bridge_meta):
            log(
                f"[cyan]media bridge[/cyan] {rel} "
                f"({bridge_meta.get('original_suffix')}→wav)"
            )
            chunks, confidence = docling_extract(media_path)
            meta.update(bridge_meta)
            delta["media_bridged"] = 1
        return chunks, confidence


def _maybe_asr_escalate(
    path: Path,
    chunks: list[dict[str, Any]],
    *,
    settings: Settings,
    file_size: int,
    bates: str | None,
    log: Callable[[str], None],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Score Docling ASR; if poor, replace with the OpenAI transcription model."""
    text = join_chunk_text(chunks)
    duration = media_duration_sec(path)
    report = score_transcript(text, file_size=file_size, duration_sec=duration)
    meta: dict[str, Any] = {
        "quality_score": report.score,
        "quality_reasons": report.reasons,
        "quality_metrics": report.metrics,
        "media_duration_sec": duration,
        "source": "docling_asr",
        "modality": "audio_video",
    }
    should = (
        settings.asr_escalate
        and report.escalate
        and report.score < settings.asr_escalate_min_score
    )
    hard = any(
        r.startswith(
            (
                "near_empty",
                "hallucination",
                "junk_only",
                "repetitive",
                "looped",
                "sparse_",
            )
        )
        for r in report.reasons
    )
    if not (settings.asr_escalate and (should or (report.escalate and hard))):
        return chunks, meta

    if resolve_asr_client(settings) is None:
        log(
            f"[yellow]asr escalate skipped[/yellow] {path.name}: no OPENAI_API_KEY "
            f"({report.summary()})"
        )
        meta["escalate_skipped"] = "no_api_key"
        meta["escalate_wanted"] = True
        return chunks, meta

    log(f"[yellow]asr quality[/yellow] {path.name}: {report.summary()}")
    try:
        ai_chunks = ai_transcribe_file(
            path,
            settings,
            bates=bates,
            duration_sec=duration,
            log=log,
        )
    except Exception as e:
        log(f"[red]asr escalate failed[/red] {path.name}: {e}")
        meta["escalate_error"] = str(e)[:500]
        meta["escalate_wanted"] = True
        return chunks, meta

    if not ai_chunks:
        meta["escalate_error"] = "asr_empty"
        return chunks, meta

    meta["source"] = settings.asr_model
    meta["escalated"] = True
    meta["escalate_kind"] = "asr"
    log(f"[green]asr escalate ok[/green] {path.name}: {len(join_chunk_text(ai_chunks))} chars")
    return ai_chunks, meta


def _maybe_ai_escalate(
    path: Path,
    chunks: list[dict[str, Any]],
    confidence: dict[str, Any],
    *,
    settings: Settings,
    file_size: int,
    bates: str | None,
    log: Callable[[str], None],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Score extraction; escalate A/V via ASR or docs via vision OCR."""
    if is_media_path(path):
        return _maybe_asr_escalate(
            path,
            chunks,
            settings=settings,
            file_size=file_size,
            bates=bates,
            log=log,
        )

    text = join_chunk_text(chunks)
    page_count = confidence.get("page_count")
    report = score_extraction(
        text,
        file_size=file_size,
        page_count=page_count if isinstance(page_count, int) else None,
        confidence=confidence,
    )
    meta: dict[str, Any] = {
        "quality_score": report.score,
        "quality_reasons": report.reasons,
        "quality_metrics": report.metrics,
        "docling_confidence": {
            k: confidence.get(k)
            for k in ("mean_grade", "low_grade", "ocr_score", "parse_score", "layout_score", "page_count")
        },
        "source": "docling",
    }

    # An image with no text is a scene photo, not a failed scan; the visual pass describes it.
    if is_image_path(path) and "near_empty_text" in report.reasons:
        meta["quality_score"] = None
        meta["quality_reasons"] = ["photo_no_text"]
        return chunks, meta

    should = (
        settings.ocr_escalate
        and report.escalate
        and report.score < settings.ocr_escalate_min_score
    )
    # Also escalate on hard reasons even if score formula is borderline
    hard = any(
        r.startswith(
            (
                "near_empty",
                "docling_mean_grade=poor",
                "docling_low_grade=poor",
                "weird_symbols",
                "sparse_chars_per_page",
                "sparse_vs_filesize",
                "mojibake",
            )
        )
        for r in report.reasons
    )
    if settings.ocr_escalate and (should or (report.escalate and hard)):
        if resolve_ai_ocr_client(settings) is None:
            log(
                f"[yellow]ai-ocr skipped[/yellow] {path.name}: no OPENAI/ANTHROPIC key "
                f"({report.summary()})"
            )
            meta["escalate_skipped"] = "no_api_key"
            meta["escalate_wanted"] = True
            return chunks, meta

        whole_doc = not report.bad_pages or report.score < 0.4 or any(
            r.startswith("near_empty") or r.startswith("sparse_") for r in report.reasons
        )
        log(f"[yellow]quality[/yellow] {path.name}: {report.summary()}")
        try:
            ai_chunks = ai_ocr_file(
                path,
                settings,
                bad_pages=report.bad_pages,
                whole_doc=whole_doc,
                bates=bates,
                log=log,
            )
        except Exception as e:
            log(f"[red]ai-ocr failed[/red] {path.name}: {e}")
            meta["escalate_error"] = str(e)[:500]
            meta["escalate_wanted"] = True
            return chunks, meta

        if not ai_chunks:
            meta["escalate_error"] = "ai_ocr_empty"
            return chunks, meta

        # Merge strategy: if whole-doc AI OCR, replace; if page-targeted, drop overlapping pages then append
        if whole_doc:
            merged = ai_chunks
        else:
            bad = set(report.bad_pages)
            kept = [
                c
                for c in chunks
                if not (
                    c.get("page_start") in bad
                    or c.get("page_end") in bad
                    or (
                        isinstance(c.get("page_start"), int)
                        and isinstance(c.get("page_end"), int)
                        and any(p >= c["page_start"] and p <= c["page_end"] for p in bad)
                    )
                )
            ]
            merged = kept + ai_chunks

        meta["source"] = "docling+ai_ocr" if not whole_doc else "ai_ocr"
        meta["escalated"] = True
        meta["escalate_page_count"] = len(ai_chunks)
        meta["escalate_whole_doc"] = whole_doc
        meta["ai_ocr_pages"] = [
            c.get("page_start") for c in ai_chunks if c.get("page_start") is not None
        ]
        log(
            f"[green]ai-ocr ok[/green] {path.name}: {len(ai_chunks)} page(s), "
            f"source={meta['source']}"
        )
        return merged, meta

    return chunks, meta


def _ingest_one_file(
    path: Path,
    *,
    root: Path,
    case_id: int,
    db: Database,
    settings: Settings,
    embedder: Embedder,
    db_lock: threading.Lock,
    embed_lock: threading.Lock,
    log: Callable[[str], None],
) -> dict[str, int]:
    """Process one file (CPU/API-heavy work parallel; SQLite writes under db_lock)."""
    delta = {
        "files": 0,
        "chunks": 0,
        "errors": 0,
        "skipped": 0,
        "escalated": 0,
        "escalate_skipped_no_key": 0,
        "media_bridged": 0,
        "visualized": 0,
        "entities": 0,
    }
    rel = str(path.relative_to(root)).replace("\\", "/")
    try:
        st = path.stat()
        sha = file_sha256(path)
        with db_lock:
            existing = db.get_file(case_id, rel)
        if (
            existing
            and existing["sha256"] == sha
            and existing["status"] == "ready"
        ):
            meta_existing = existing["meta"] if "meta" in existing.keys() else None
            if isinstance(meta_existing, str):
                try:
                    import json as _json

                    meta_existing = _json.loads(meta_existing)
                except Exception:
                    meta_existing = {}
            meta_existing = meta_existing if isinstance(meta_existing, dict) else {}
            with db_lock:
                # Keep the real chunk count; the updater's default (0) made every
                # unchanged file look empty after a re-run.
                n_existing = db.conn.execute(
                    "SELECT COUNT(*) FROM chunks WHERE file_id=?", (int(existing["id"]),)
                ).fetchone()[0]
                db.update_manifest_ingest(
                    case_id,
                    rel,
                    ingest_status="ready",
                    chunk_count=int(n_existing),
                    quality_score=meta_existing.get("quality_score")
                    if isinstance(meta_existing.get("quality_score"), (int, float))
                    else None,
                    quality_reasons=meta_existing.get("quality_reasons")
                    if isinstance(meta_existing.get("quality_reasons"), list)
                    else None,
                    file_id=int(existing["id"]),
                    escalated=bool(meta_existing.get("escalated")),
                    source=meta_existing.get("source")
                    if isinstance(meta_existing.get("source"), str)
                    else None,
                    entities=meta_existing.get("entities"),
                    tags=meta_existing.get("tags"),
                )
            delta["skipped"] = 1
            return delta

        bates = extract_bates(rel, settings.bates_pattern)
        mime = mimetypes.guess_type(path.name)[0]
        log(f"[cyan]ingest[/cyan] {rel}")

        meta: dict[str, Any] = {}
        ai_asr_done = False
        try:
            if path.suffix.lower() in _RAW_TEXT_ONLY_SUFFIXES:
                chunks = _chunk_plain_text(
                    path.read_text(encoding="utf-8", errors="replace"),
                    bates,
                )
                confidence = {}
                meta["source"] = "raw_text"
            elif is_media_path(path) and use_ai_asr(settings):
                try:
                    chunks = ai_transcribe_file(path, settings, bates=bates, log=log)
                    confidence = {}
                    meta.update(
                        {
                            "source": (
                                "hq:gpt-4o-transcribe"
                                if getattr(settings, "asr_hq", False)
                                else settings.asr_model
                            ),
                            "modality": "audio_video",
                            "asr_hq": bool(getattr(settings, "asr_hq", False)),
                        }
                    )
                    ai_asr_done = True
                except Exception as asr_err:
                    log(f"[yellow]openai transcription failed → local[/yellow] {rel}: {asr_err}")
                    meta["asr_error"] = str(asr_err)[:500]
                    chunks, confidence = _docling_media(path, rel, meta, delta, log)
            elif is_media_path(path):
                chunks, confidence = _docling_media(path, rel, meta, delta, log)
            else:
                chunks, confidence = docling_extract(path)
        except Exception as e:
            if path.suffix.lower() in _PLAIN_TEXT_SUFFIXES:
                chunks = _chunk_plain_text(
                    path.read_text(encoding="utf-8", errors="replace"),
                    bates,
                )
                confidence = {}
                meta["source"] = "raw_text_fallback"
                meta["docling_error"] = str(e)[:500]
            else:
                if (
                    is_media_path(path)
                    and not meta.get("asr_error")
                    and resolve_asr_client(settings)
                ):
                    log(f"[yellow]docling failed → asr escalate[/yellow] {rel}: {e}")
                    chunks = ai_transcribe_file(
                        path,
                        settings,
                        bates=bates,
                        log=log,
                    )
                    confidence = {}
                    meta = {
                        **meta,
                        "source": settings.asr_model,
                        "escalated": True,
                        "escalate_kind": "asr",
                        "docling_error": str(e)[:500],
                    }
                    delta["escalated"] = 1
                elif settings.ocr_escalate and path.suffix.lower() in {
                    ".pdf",
                    ".png",
                    ".jpg",
                    ".jpeg",
                    ".tif",
                    ".tiff",
                    ".bmp",
                    ".webp",
                }:
                    log(f"[yellow]docling failed → ai-ocr[/yellow] {rel}: {e}")
                    chunks = ai_ocr_file(
                        path,
                        settings,
                        bad_pages=[],
                        whole_doc=True,
                        bates=bates,
                        log=log,
                    )
                    confidence = {}
                    meta = {
                        "source": "ai_ocr",
                        "escalated": True,
                        "docling_error": str(e)[:500],
                    }
                    delta["escalated"] = 1
                else:
                    raise e
        else:
            if not ai_asr_done:
                chunks, qmeta = _maybe_ai_escalate(
                    path,
                    chunks,
                    confidence,
                    settings=settings,
                    file_size=st.st_size,
                    bates=bates,
                    log=log,
                )
                meta.update(qmeta)
                if qmeta.get("escalated"):
                    delta["escalated"] = 1
                if qmeta.get("escalate_skipped") == "no_api_key":
                    delta["escalate_skipped_no_key"] = 1

        if settings.visual_analyze:
            v_chunks, v_meta = visual_analyze_file(
                path,
                settings,
                existing_chunks=chunks,
                bates=bates,
                log=log,
            )
            meta.update({k: v for k, v in v_meta.items() if k.startswith("visual")})
            if v_chunks:
                chunks = list(chunks) + v_chunks
                delta["visualized"] = 1

        for ch in chunks:
            if not ch.get("bates"):
                ch["bates"] = bates

        media = describe_media(path, rel)
        if media:
            meta["media"] = media
            if media["kind"] in ("bodycam", "dashcam"):
                log(
                    f"[cyan]media[/cyan] {rel}: {media['kind']}"
                    f" ({', '.join(media['signals'])})"
                )

        # Simple file-level entities/tags (grow with use case later)
        if (
            settings.entity_extract
            and path.suffix.lower() not in _RAW_TEXT_ONLY_SUFFIXES
            and resolve_entity_client(settings)
        ):
            ent_chunks = chunks
            if path.suffix.lower() in _SPREADSHEET_SUFFIXES:
                # Row data (tower dumps, call logs) is thousands of near-identical
                # rows; mapping all of it costs one LLM call per segment for
                # little gain. The header + first rows carry the sheet's entities.
                ent_chunks, total = [], 0
                for c in chunks:
                    if total >= _SPREADSHEET_ENTITY_CHARS:
                        break
                    ent_chunks.append(c)
                    total += len(c.get("text") or "")
            ent = extract_entities_for_chunks(ent_chunks, settings, log=log)
            if len(ent_chunks) < len(chunks):
                ent["truncated"] = True
            if ent.get("entities") or ent.get("tags"):
                meta["entities"] = ent.get("entities") or []
                meta["tags"] = ent.get("tags") or []
                meta["entity_segments"] = ent.get("segments")
                meta["entity_chars"] = ent.get("chars")
                if ent.get("truncated"):
                    meta["entity_truncated"] = True
                delta["entities"] = 1
            elif ent.get("error"):
                meta["entity_error"] = ent["error"]
            elif ent.get("skipped"):
                meta["entity_skipped"] = ent["skipped"]
        if media and media["kind"] in ("bodycam", "dashcam"):
            tags = list(meta.get("tags") or [])
            if media["kind"] not in tags:
                meta["tags"] = [media["kind"], *tags]

        texts = [c["text"] for c in chunks]
        with embed_lock:
            vectors = embedder.embed_documents(texts) if texts else []
        emb_pairs = [(v, embedder.model_id) for v in vectors]

        with db_lock:
            file_id = db.upsert_file(
                case_id,
                rel,
                path.name,
                bates,
                mime,
                st.st_size,
                st.st_mtime,
                sha,
                status="ready",
                meta=meta,
            )
            n = db.replace_chunks(file_id, case_id, rel, chunks, emb_pairs)
            qscore = meta.get("quality_score")
            qscore_f = float(qscore) if isinstance(qscore, (int, float)) else None
            db.update_manifest_ingest(
                case_id,
                rel,
                ingest_status="ready",
                quality_score=qscore_f,
                quality_reasons=meta.get("quality_reasons")
                if isinstance(meta.get("quality_reasons"), list)
                else None,
                chunk_count=n,
                file_id=file_id,
                escalated=bool(meta.get("escalated")),
                source=meta.get("source") if isinstance(meta.get("source"), str) else None,
                entities=meta.get("entities"),
                tags=meta.get("tags"),
            )
        delta["files"] = 1
        delta["chunks"] = n
        return delta
    except Exception as e:
        delta["errors"] = 1
        with db_lock:
            db.upsert_file(
                case_id,
                rel,
                path.name,
                extract_bates(rel, settings.bates_pattern),
                mimetypes.guess_type(path.name)[0],
                path.stat().st_size if path.exists() else 0,
                path.stat().st_mtime if path.exists() else None,
                None,
                status="error",
                error=str(e)[:2000],
            )
            db.update_manifest_ingest(
                case_id,
                rel,
                ingest_status="error",
                error=str(e)[:2000],
            )
        log(f"[red]error[/red] {rel}: {e}")
        return delta


def ingest_case(
    db: Database,
    settings: Settings,
    embedder: Embedder,
    *,
    slug: str,
    display_name: str,
    files_root: Path,
    limit: int | None = None,
    workers: int | None = None,
    progress: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    root = files_root.resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"files_root not found: {root}")

    log_lock = threading.Lock()
    raw_log = progress or (lambda m: console.print(m))

    def log(msg: str) -> None:
        with log_lock:
            raw_log(msg)

    # Step 0–1: unzip + hashed production manifest (feeds UX / what-was-produced)
    prep = prepare_production(
        root,
        settings,
        db=db,
        slug=slug,
        display_name=display_name,
        expand=settings.expand_zips,
        log=log,
    )
    case = db.get_case_by_slug(slug)
    if not case:
        case = db.upsert_case(slug, display_name, str(root))
        db.set_active_case(case.id)

    paths = iter_case_files(root, settings.allowed_extensions)
    if limit is not None:
        paths = paths[:limit]

    n_workers = int(workers if workers is not None else settings.ingest_workers)
    n_workers = max(1, n_workers)
    if paths:
        n_workers = min(n_workers, len(paths))

    stats = {
        "files": 0,
        "chunks": 0,
        "errors": 0,
        "skipped": 0,
        "escalated": 0,
        "escalate_skipped_no_key": 0,
        "zip_archives": prep.zip_stats.get("archives", 0),
        "zip_extracted": prep.zip_stats.get("extracted", 0),
        "manifest_files": prep.summary.get("file_count", 0),
        "media_bridged": 0,
        "visualized": 0,
        "entities": 0,
        "workers": n_workers,
        "ufdr_found": 0,
        "ufdr_parsed": 0,
        "ufdr_cached": 0,
        "ufdr_errors": 0,
        "ufdr_chats": 0,
        "ufdr_messages": 0,
    }

    db_lock = threading.Lock()
    embed_lock = threading.Lock()
    log(f"[cyan]parallel[/cyan] ingest_workers={n_workers} files={len(paths)}")

    def _run(path: Path) -> dict[str, int]:
        return _ingest_one_file(
            path,
            root=root,
            case_id=case.id,
            db=db,
            settings=settings,
            embedder=embedder,
            db_lock=db_lock,
            embed_lock=embed_lock,
            log=log,
        )

    if n_workers == 1:
        deltas = [_run(p) for p in paths]
    else:
        deltas = []
        with ThreadPoolExecutor(max_workers=n_workers) as pool:
            futs = {pool.submit(_run, p): p for p in paths}
            for fut in as_completed(futs):
                deltas.append(fut.result())

    for d in deltas:
        for k, v in d.items():
            if k in stats:
                stats[k] += v

    # Refresh disk summary + confidence board for UX
    try:
        import json as _json

        rows = db.list_production_manifest(case.id, limit=100_000)
        summary_path = root / ".marshaloss" / "manifest_summary.json"
        summary = db.production_manifest_summary(case.id)
        summary.update(
            {
                "schema": "marshaloss.production_manifest.v1",
                "root": str(root),
                "slug": slug,
                "post_ingest": True,
            }
        )
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        summary_path.write_text(_json.dumps(summary, indent=2), encoding="utf-8")
        board = {
            "ingest_complete": summary.get("ingest_complete"),
            "by_status": summary.get("by_status"),
            "avg_quality": summary.get("avg_quality"),
            "low_quality_count": summary.get("low_quality_count"),
            "high_quality_count": summary.get("high_quality_count"),
            "ready_count": summary.get("ready_count"),
            "error_count": summary.get("error_count"),
            "not_covered_count": summary.get("not_covered_count"),
            "escalated_count": summary.get("escalated_count"),
            "low_quality_files": [
                {
                    "rel_path": r["rel_path"],
                    "quality_score": r.get("quality_score"),
                    "quality_reasons": r.get("quality_reasons"),
                    "source": r.get("source"),
                    "escalated": r.get("escalated"),
                }
                for r in rows
                if r.get("ingest_status") == "ready"
                and isinstance(r.get("quality_score"), (int, float))
                and float(r["quality_score"]) < 0.72
            ][:200],
            "errors": [
                {"rel_path": r["rel_path"], "error": r.get("error")}
                for r in rows
                if r.get("ingest_status") == "error"
            ][:200],
        }
        conf_path = root / ".marshaloss" / "ingest_confidence.json"
        conf_path.write_text(_json.dumps(board, indent=2), encoding="utf-8")
        stats["manifest_summary"] = summary_path.as_posix()
        stats["ingest_confidence"] = conf_path.as_posix()
    except Exception as e:
        log(f"[yellow]manifest summary refresh failed[/yellow]: {e}")

    # Cellebrite UFDR → Forensics cache (chats + contact directory)
    try:
        forensic = ingest_ufdr_forensics(root, progress=log)
        stats["ufdr_found"] = int(forensic.get("ufdr_found") or 0)
        stats["ufdr_parsed"] = int(forensic.get("ufdr_parsed") or 0)
        stats["ufdr_cached"] = int(forensic.get("ufdr_cached") or 0)
        stats["ufdr_errors"] = int(forensic.get("ufdr_errors") or 0)
        stats["ufdr_chats"] = int(forensic.get("chat_count") or 0)
        stats["ufdr_messages"] = int(forensic.get("message_count") or 0)
        if forensic.get("summary_path"):
            stats["forensics_summary"] = forensic["summary_path"]
    except Exception as e:
        stats["ufdr_errors"] = int(stats.get("ufdr_errors") or 0) + 1
        log(f"[yellow]forensics ingest failed[/yellow]: {e}")

    return {"case": case, **stats}
