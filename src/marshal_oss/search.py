from __future__ import annotations

import logging
from typing import Any, Literal
from urllib.parse import quote

from .config import Settings
from .db import Case, Database
from .embed import Embedder, cosine_topk

logger = logging.getLogger(__name__)

Mode = Literal["hybrid", "keyword", "semantic"]


def encode_rel_path(rel: str) -> str:
    return "/".join(quote(p, safe="") for p in rel.split("/") if p)


def file_urls(settings: Settings, case: Case, rel_path: str) -> dict[str, str]:
    enc = encode_rel_path(rel_path)
    raw = f"{settings.public_base.rstrip('/')}/files/{case.slug}/{enc}"
    return {
        "open_url": f"{settings.public_base.rstrip('/')}/view/{case.slug}/{enc}",
        "raw_url": raw,
        "download_url": f"{raw}?dl=1",
    }


def rrf_fuse(
    keyword_hits: list[dict[str, Any]],
    semantic_hits: list[tuple[float, dict[str, Any]]],
    k: int = 20,
    rrf_k: int = 60,
) -> list[dict[str, Any]]:
    scores: dict[int, float] = {}
    meta_by_id: dict[int, dict[str, Any]] = {}

    for rank, h in enumerate(keyword_hits):
        cid = int(h["chunk_id"])
        scores[cid] = scores.get(cid, 0.0) + 1.0 / (rrf_k + rank + 1)
        meta_by_id[cid] = {
            "chunk_id": cid,
            "text": h["text"],
            "bates": h.get("bates") or h.get("file_bates"),
            "page_start": h.get("page_start"),
            "page_end": h.get("page_end"),
            "heading": h.get("heading"),
            "start_sec": h.get("start_sec"),
            "end_sec": h.get("end_sec"),
            "rel_path": h["rel_path"],
            "name": h["name"],
            "file_id": h["file_id"],
            "keyword_rank": rank + 1,
        }

    for rank, (score, meta) in enumerate(semantic_hits):
        cid = int(meta["chunk_id"])
        scores[cid] = scores.get(cid, 0.0) + 1.0 / (rrf_k + rank + 1)
        base = meta_by_id.get(cid, {**meta, "chunk_id": cid})
        base["semantic_score"] = score
        base["semantic_rank"] = rank + 1
        meta_by_id[cid] = base

    ordered = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:k]
    out = []
    for cid, fused in ordered:
        m = dict(meta_by_id[cid])
        m["score"] = fused
        out.append(m)
    return out


def search_case(
    db: Database,
    settings: Settings,
    embedder: Embedder,
    case: Case,
    query: str,
    *,
    mode: Mode = "hybrid",
    top_k: int = 12,
) -> list[dict[str, Any]]:
    query = (query or "").strip()
    if not query:
        return []

    keyword_hits: list[dict[str, Any]] = []
    semantic_hits: list[tuple[float, dict[str, Any]]] = []

    if mode in ("hybrid", "keyword"):
        try:
            keyword_hits = db.fts_search(case.id, query, limit=max(top_k * 3, 30))
        except Exception as e:
            logger.warning("keyword search failed: %s", e)
            keyword_hits = []

    if mode in ("hybrid", "semantic"):
        try:
            qvec = embedder.embed_query(query)
            corpus = db.all_embeddings(case.id)
            semantic_hits = cosine_topk(qvec, corpus, k=max(top_k * 3, 30))
        except Exception as e:
            logger.warning("semantic search failed: %s", e)
            semantic_hits = []
            if mode == "semantic":
                return []

    if mode == "keyword":
        fused = [
            {
                "chunk_id": h["chunk_id"],
                "text": h["text"],
                "bates": h.get("bates") or h.get("file_bates"),
                "page_start": h.get("page_start"),
                "page_end": h.get("page_end"),
                "heading": h.get("heading"),
                "start_sec": h.get("start_sec"),
                "end_sec": h.get("end_sec"),
                "rel_path": h["rel_path"],
                "name": h["name"],
                "file_id": h["file_id"],
                "score": -float(h.get("score") or 0),
            }
            for h in keyword_hits[:top_k]
        ]
    elif mode == "semantic":
        fused = [
            {**meta, "score": score}
            for score, meta in semantic_hits[:top_k]
        ]
    else:
        fused = rrf_fuse(keyword_hits, semantic_hits, k=top_k)

    for hit in fused:
        hit.update(file_urls(settings, case, hit["rel_path"]))
        # Snippet
        text = hit.get("text") or ""
        hit["snippet"] = text[:500] + ("…" if len(text) > 500 else "")
    return fused
