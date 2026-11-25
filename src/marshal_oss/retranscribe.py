"""Force re-ASR for media files (HQ stack), preserving visual frame chunks."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any, Callable

from .asr_escalate import ai_transcribe_file, use_ai_asr
from .config import Settings
from .db import Case, Database
from .embed import Embedder
from .media_ffmpeg import is_media_path

LogFn = Callable[[str], None]


def _parse_meta(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            loaded = json.loads(raw)
            return loaded if isinstance(loaded, dict) else {}
        except Exception:
            return {}
    return {}


def _is_visual_chunk(ch: dict[str, Any]) -> bool:
    meta = ch.get("meta") if isinstance(ch.get("meta"), dict) else {}
    if meta.get("source") == "visual" or meta.get("kind") in ("video_frame", "image"):
        return True
    heading = str(ch.get("heading") or "")
    if heading.startswith("Visual @"):
        return True
    for seg in ch.get("segments") or []:
        if isinstance(seg, dict) and (seg.get("kind") or "").lower() == "visual":
            return True
    return False


def _load_existing_chunks(db: Database, case_id: int, rel_path: str) -> list[dict[str, Any]]:
    detail = db.get_file_detail(case_id, rel_path)
    if not detail:
        return []
    out: list[dict[str, Any]] = []
    for ch in detail.get("chunks") or []:
        item = {
            "text": ch.get("text") or "",
            "page_start": ch.get("page_start"),
            "page_end": ch.get("page_end"),
            "heading": ch.get("heading"),
            "bates": ch.get("bates"),
            "start_sec": ch.get("start_sec"),
            "end_sec": ch.get("end_sec"),
            "segments": [],
            "meta": {},
        }
        out.append(item)
    # Rebuild segment lists from media_segments by time overlap is heavy;
    # instead pull all media_segments and attach visuals by kind.
    file_id = detail["id"]
    segs = db.list_media_segments(int(file_id))
    visual_segs = [s for s in segs if (s.get("kind") or "").lower() == "visual"]
    # Put each visual segment on its own chunk (matches ingest visual shape)
    visual_chunks: list[dict[str, Any]] = []
    for s in visual_segs:
        visual_chunks.append(
            {
                "text": s.get("text") or "",
                "heading": f"Visual @{(s.get('start_sec') or 0):.0f}s",
                "bates": detail.get("bates"),
                "start_sec": s.get("start_sec"),
                "end_sec": s.get("end_sec"),
                "segments": [
                    {
                        "kind": "visual",
                        "start": float(s.get("start_sec") or 0),
                        "end": float(s.get("end_sec") or 0),
                        "text": s.get("text") or "",
                    }
                ],
                "meta": {"source": "visual", "kind": "video_frame"},
            }
        )
    # Prefer reconstructed visual chunks; ignore old speech chunks
    return visual_chunks


def retranscribe_case_media(
    db: Database,
    settings: Settings,
    embedder: Embedder,
    case: Case,
    *,
    path_contains: str | None = None,
    kinds: set[str] | None = None,
    limit: int | None = None,
    artifact_root: Path | None = None,
    log: LogFn | None = None,
) -> dict[str, Any]:
    """Re-run ASR for ready media files; keep existing visual frame captions."""
    _log = log or (lambda _m: None)
    if not use_ai_asr(settings) and not getattr(settings, "asr_hq", False):
        # HQ still needs OpenAI; use_ai_asr checks provider+key
        pass
    if not use_ai_asr(settings):
        raise RuntimeError("OpenAI ASR required (set OPENAI_API_KEY and asr_provider=openai)")

    kinds = kinds or {"bodycam", "dashcam"}
    root = Path(case.files_root)
    files = db.list_files_with_meta(case.id)
    targets: list[dict[str, Any]] = []
    for f in files:
        if (f.get("status") or "") != "ready":
            continue
        name = f.get("name") or ""
        rel = f.get("rel_path") or ""
        if path_contains and path_contains.lower() not in rel.lower() and path_contains.lower() not in name.lower():
            continue
        if not is_media_path(name):
            continue
        media = (f.get("meta") or {}).get("media") if isinstance(f.get("meta"), dict) else {}
        kind = (media.get("kind") if isinstance(media, dict) else None) or ""
        if kinds and kind not in kinds and "axon" not in name.lower():
            continue
        targets.append(f)
        if limit is not None and len(targets) >= limit:
            break

    stats = {"matched": len(targets), "ok": 0, "errors": 0, "chunks": 0}
    lock = threading.Lock()

    for f in targets:
        rel = f["rel_path"]
        path = root / rel
        if not path.is_file():
            _log(f"[yellow]missing[/yellow] {rel}")
            stats["errors"] += 1
            continue
        _log(f"[cyan]retranscribe[/cyan] {rel} (hq={settings.asr_hq})")
        try:
            visual_chunks = _load_existing_chunks(db, case.id, rel)
            art = None
            if artifact_root is not None:
                art = Path(artifact_root) / case.slug / Path(rel).stem
                art.mkdir(parents=True, exist_ok=True)
            speech_chunks = ai_transcribe_file(
                path, settings, bates=f.get("bates"), log=_log, artifact_dir=art
            )
            chunks = list(speech_chunks) + list(visual_chunks)
            texts = [c["text"] for c in chunks]
            vectors = embedder.embed_documents(texts) if texts else []
            emb_pairs = [(v, embedder.model_id) for v in vectors]

            meta = dict(f.get("meta") or {}) if isinstance(f.get("meta"), dict) else _parse_meta(f.get("meta"))
            meta["source"] = "hq:gpt-4o-transcribe" if settings.asr_hq else settings.asr_model
            meta["asr_hq"] = bool(settings.asr_hq)
            meta["retranscribed"] = True
            row = db.get_file(case.id, rel)
            sha = row["sha256"] if row else None
            size = int((row["size"] if row else None) or f.get("size") or path.stat().st_size)
            mtime = float(row["mtime"]) if row and row["mtime"] is not None else path.stat().st_mtime
            with lock:
                file_id = db.upsert_file(
                    case.id,
                    rel,
                    f.get("name") or path.name,
                    f.get("bates") if f.get("bates") is not None else (row["bates"] if row else None),
                    f.get("mime") or (row["mime"] if row else None),
                    size,
                    mtime,
                    sha,
                    status="ready",
                    meta=meta,
                )
                n = db.replace_chunks(file_id, case.id, rel, chunks, emb_pairs)
            stats["ok"] += 1
            stats["chunks"] += n
            _log(f"[green]ok[/green] {rel}: {n} chunks ({len(speech_chunks)} speech + {len(visual_chunks)} visual)")
        except Exception as e:
            stats["errors"] += 1
            _log(f"[red]error[/red] {rel}: {e}")
    return stats
