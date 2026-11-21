from __future__ import annotations

import hashlib
import mimetypes
from pathlib import Path
from typing import Any, Callable

from rich.console import Console

from .bates import extract_bates
from .config import Settings
from .db import Database
from .embed import Embedder
from .escalate import ai_ocr_file, resolve_ai_ocr_client
from .quality import (
    extract_docling_confidence,
    join_chunk_text,
    score_extraction,
)

console = Console()


def file_sha256(path: Path, max_bytes: int = 64 * 1024 * 1024) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        remaining = max_bytes
        while remaining > 0:
            chunk = f.read(min(1024 * 1024, remaining))
            if not chunk:
                break
            h.update(chunk)
            remaining -= len(chunk)
    return h.hexdigest()


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

    chunks: list[dict[str, Any]] = []
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
            if meta is not None:
                headings = list(getattr(meta, "headings", None) or [])
                for item in getattr(meta, "doc_items", None) or []:
                    for prov in getattr(item, "prov", None) or []:
                        page = getattr(prov, "page_no", None)
                        if page is not None:
                            pages_found.append(int(page))
            pages_found = sorted(set(pages_found))
            chunks.append(
                {
                    "text": (text or "").strip(),
                    "page_start": pages_found[0] if pages_found else None,
                    "page_end": pages_found[-1] if pages_found else None,
                    "heading": " / ".join(str(h) for h in headings) if headings else None,
                    "bates": None,
                }
            )
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
    """Score extraction; if poor, replace/supplement with vision-LLM OCR."""
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


def ingest_case(
    db: Database,
    settings: Settings,
    embedder: Embedder,
    *,
    slug: str,
    display_name: str,
    files_root: Path,
    limit: int | None = None,
    progress: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    root = files_root.resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"files_root not found: {root}")

    case = db.upsert_case(slug, display_name, str(root))
    db.set_active_case(case.id)

    paths = iter_case_files(root, settings.allowed_extensions)
    if limit is not None:
        paths = paths[:limit]

    stats = {
        "files": 0,
        "chunks": 0,
        "errors": 0,
        "skipped": 0,
        "escalated": 0,
        "escalate_skipped_no_key": 0,
    }
    log = progress or (lambda m: console.print(m))

    for path in paths:
        rel = str(path.relative_to(root)).replace("\\", "/")
        try:
            st = path.stat()
            if st.st_size > settings.max_file_bytes:
                db.upsert_file(
                    case.id,
                    rel,
                    path.name,
                    extract_bates(rel, settings.bates_pattern),
                    mimetypes.guess_type(path.name)[0],
                    st.st_size,
                    st.st_mtime,
                    None,
                    status="skipped",
                    error=f"too large ({st.st_size})",
                )
                stats["skipped"] += 1
                continue

            sha = file_sha256(path)
            existing = db.get_file(case.id, rel)
            if (
                existing
                and existing["sha256"] == sha
                and existing["status"] == "ready"
            ):
                stats["skipped"] += 1
                continue

            bates = extract_bates(rel, settings.bates_pattern)
            mime = mimetypes.guess_type(path.name)[0]
            log(f"[cyan]ingest[/cyan] {rel}")

            meta: dict[str, Any] = {}
            try:
                chunks, confidence = docling_extract(path)
            except Exception as e:
                if path.suffix.lower() in {".txt", ".md", ".csv", ".log"}:
                    chunks = _chunk_plain_text(
                        path.read_text(encoding="utf-8", errors="replace"),
                        bates,
                    )
                    confidence = {}
                    meta["source"] = "raw_text_fallback"
                    meta["docling_error"] = str(e)[:500]
                else:
                    # Last chance: AI OCR if enabled and renderable
                    if settings.ocr_escalate and path.suffix.lower() in {
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
                        stats["escalated"] += 1
                    else:
                        raise e
            else:
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
                    stats["escalated"] += 1
                if qmeta.get("escalate_skipped") == "no_api_key":
                    stats["escalate_skipped_no_key"] += 1

            for ch in chunks:
                if not ch.get("bates"):
                    ch["bates"] = bates

            texts = [c["text"] for c in chunks]
            vectors = embedder.embed_documents(texts) if texts else []
            emb_pairs = [(v, embedder.model_id) for v in vectors]

            file_id = db.upsert_file(
                case.id,
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
            n = db.replace_chunks(file_id, case.id, rel, chunks, emb_pairs)
            stats["files"] += 1
            stats["chunks"] += n
        except Exception as e:
            stats["errors"] += 1
            db.upsert_file(
                case.id,
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
            log(f"[red]error[/red] {rel}: {e}")

    return {"case": case, **stats}
