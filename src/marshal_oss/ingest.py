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
        # Skip giant marshal bundles / archives by extension policy already
        files.append(p)
    files.sort(key=lambda x: str(x).lower())
    return files


def _chunk_plain_text(text: str, bates: str | None, max_chars: int = 1800) -> list[dict[str, Any]]:
    text = (text or "").strip()
    if not text:
        return []
    chunks = []
    i = 0
    idx = 0
    while i < len(text):
        piece = text[i : i + max_chars]
        # break on paragraph if possible
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
        idx += 1
    return [c for c in chunks if c["text"]]


def docling_extract(path: Path) -> list[dict[str, Any]]:
    """Use Docling to convert a file into text chunks with page provenance when available."""
    from docling.chunking import HybridChunker
    from docling.document_converter import DocumentConverter

    converter = DocumentConverter()
    result = converter.convert(str(path))
    doc = result.document

    chunks: list[dict[str, Any]] = []
    try:
        chunker = HybridChunker()
        for ch in chunker.chunk(dl_doc=doc):
            text = chunker.contextualize(ch) if hasattr(chunker, "contextualize") else (getattr(ch, "text", None) or "")
            meta = getattr(ch, "meta", None)
            pages: list[int] = []
            headings: list[str] = []
            if meta is not None:
                headings = list(getattr(meta, "headings", None) or [])
                for item in getattr(meta, "doc_items", None) or []:
                    for prov in getattr(item, "prov", None) or []:
                        page = getattr(prov, "page_no", None)
                        if page is not None:
                            pages.append(int(page))
            pages = sorted(set(pages))
            chunks.append(
                {
                    "text": (text or "").strip(),
                    "page_start": pages[0] if pages else None,
                    "page_end": pages[-1] if pages else None,
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

    return [c for c in chunks if c.get("text")]


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

    stats = {"files": 0, "chunks": 0, "errors": 0, "skipped": 0}
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

            try:
                chunks = docling_extract(path)
            except Exception as e:
                # Last-resort: raw text read for .txt/.md/.csv
                if path.suffix.lower() in {".txt", ".md", ".csv", ".log"}:
                    chunks = _chunk_plain_text(
                        path.read_text(encoding="utf-8", errors="replace"),
                        bates,
                    )
                else:
                    raise e

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
