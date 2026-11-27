from __future__ import annotations

import mimetypes
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any
from urllib.parse import unquote

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from .bwc import apply_camera_label, build_bwc_index, label_suggestions, summarize_bwc_for_overview
from .categories import CATEGORY_DEFS, CATEGORY_LABELS, categorize_file_row
from .config import Settings, get_settings
from .db import Database
from .embed import Embedder
from .forensics_ufdr import (
    case_forensics_summary,
    chat_summaries,
    get_chat,
    parse_csli_map_for_case,
    parse_ufdr_chats_for_case,
    query_csli_events,
    search_forensics,
    ufedlib_available,
)
from .fsbrowse import list_dir
from .notebook import build_witness_pack, load_notebook_skill_text
from .search import encode_rel_path, file_urls, search_case

WEB_DIR = Path(__file__).resolve().parents[2] / "web"


def create_app(
    settings: Settings | None = None,
    db: Database | None = None,
    embedder: Embedder | None = None,
    lifespan: Callable[[FastAPI], AsyncIterator[None]] | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    db = db or Database(settings.database_path)
    embedder = embedder or Embedder(settings)

    app = FastAPI(title="Marshal", version="0.1.0", lifespan=lifespan)
    app.state.settings = settings
    app.state.db = db
    app.state.embedder = embedder

    if WEB_DIR.is_dir():
        app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")
        bwc_dir = WEB_DIR / "bwc"
        if bwc_dir.is_dir():
            app.mount("/bwc", StaticFiles(directory=str(bwc_dir)), name="bwc")

    def resolve_case(case_id: int | None = None, slug: str | None = None):
        if case_id is not None:
            c = db.get_case_by_id(case_id)
        elif slug:
            c = db.get_case_by_slug(slug)
        else:
            c = db.get_active_case()
        if not c:
            raise HTTPException(404, "case not found")
        return c

    @app.get("/health")
    def health() -> dict[str, Any]:
        active = db.get_active_case()
        return {
            "ok": True,
            "db": str(settings.database_path),
            "public_base": settings.public_base,
            "embed_backend": embedder.backend,
            "embed_model": embedder.model_id,
            "active": active.__dict__ if active else None,
            "cases": len(db.list_cases()),
        }

    @app.get("/api/cases")
    def api_cases() -> dict[str, Any]:
        active = db.get_active_case()
        return {
            "cases": [c.__dict__ for c in db.list_cases()],
            "active": active.__dict__ if active else None,
        }

    @app.post("/api/cases/{case_id}/activate")
    def api_activate(case_id: int) -> dict[str, Any]:
        c = db.set_active_case(case_id)
        return {"ok": True, "active": c.__dict__}

    @app.put("/api/cases/{case_id}")
    async def api_update_case(case_id: int, request: Request) -> dict[str, Any]:
        """Rename a case (display_name only; slug and files_root stay put)."""
        payload = await request.json()
        name = payload.get("display_name")
        if name is None:
            raise HTTPException(400, "display_name required")
        try:
            c = db.update_case_display_name(case_id, str(name))
        except ValueError as e:
            raise HTTPException(404 if "not found" in str(e) else 400, str(e)) from e
        return {"ok": True, "case": c.__dict__}

    @app.get("/api/cases/{case_id}/overview")
    def api_case_overview(case_id: int) -> dict[str, Any]:
        """Case-level stats + full production manifest for the review UI overview pane."""
        c = resolve_case(case_id=case_id)
        summary = db.production_manifest_summary(c.id)
        entities = db.case_entity_tag_stats(c.id, top_n=500)
        records = db.list_production_manifest(c.id, limit=10000, offset=0)
        # Compact rows for the overview table
        manifest = []
        for r in records:
            reasons = r.get("quality_reasons")
            if isinstance(reasons, list):
                reasons_s = "; ".join(str(x) for x in reasons[:6])
            elif reasons:
                reasons_s = str(reasons)
            else:
                reasons_s = ""
            status = r.get("ingest_status") or "unknown"
            processed = status == "ready"
            manifest.append(
                {
                    "rel_path": r.get("rel_path"),
                    "name": r.get("name"),
                    "ext": r.get("ext"),
                    "kind": r.get("kind"),
                    "size": r.get("size"),
                    "bates": r.get("bates"),
                    "ingest_status": status,
                    "processed": processed,
                    "quality_score": r.get("quality_score"),
                    "quality_reasons": reasons_s,
                    "chunk_count": r.get("chunk_count") or 0,
                    "escalated": bool(r.get("escalated")),
                    "ingestible": bool(r.get("ingestible")),
                    "error": r.get("error"),
                    "entity_count": len(r.get("entities") or [])
                    if isinstance(r.get("entities"), list)
                    else 0,
                    "tag_count": len(r.get("tags") or [])
                    if isinstance(r.get("tags"), list)
                    else 0,
                }
            )
        try:
            bwc = summarize_bwc_for_overview(db, c)
        except Exception:
            bwc = {
                "available": False,
                "video_count": 0,
                "bodycam_count": 0,
                "dashcam_count": 0,
                "camera_count": 0,
            }
        return {
            "case": c.__dict__,
            "summary": summary,
            "entities": entities,
            "manifest": manifest,
            "bwc": bwc,
        }

    @app.get("/api/files/by-facet")
    def api_files_by_facet(
        case_id: int | None = None,
        tag: str | None = None,
        entity: str | None = None,
        entity_type: str | None = None,
        limit: int = Query(500, ge=1, le=5000),
    ) -> dict[str, Any]:
        """Document Explorer subset filtered by tag and/or entity."""
        if not tag and not entity and not entity_type:
            raise HTTPException(400, "provide tag and/or entity / entity_type")
        c = resolve_case(case_id=case_id)
        rows = db.find_files_by_facet(
            c.id,
            tag=tag,
            entity=entity,
            entity_type=entity_type,
            limit=limit,
        )
        for row in rows:
            urls = file_urls(settings, c, row["rel_path"])
            row.update(urls)
            row["path"] = row["rel_path"]
        return {
            "case": c.__dict__,
            "filter": {
                "tag": tag,
                "entity": entity,
                "entity_type": entity_type,
            },
            "count": len(rows),
            "entries": rows,
        }

    @app.get("/api/files/list")
    def api_files_list(
        case_id: int | None = None,
        path: str = "",
    ) -> dict[str, Any]:
        c = resolve_case(case_id=case_id)
        try:
            return list_dir(db, settings, c, path)
        except FileNotFoundError as e:
            raise HTTPException(404, str(e)) from e
        except PermissionError as e:
            raise HTTPException(403, str(e)) from e

    @app.get("/api/files/categories")
    def api_files_categories(
        case_id: int | None = None,
        category: str | None = None,
    ) -> dict[str, Any]:
        """Auto document categories for Document Explorer (tag + name heuristics)."""
        c = resolve_case(case_id=case_id)
        files = db.list_files_with_meta(c.id)
        buckets: dict[str, list[dict[str, Any]]] = {cid: [] for cid, _ in CATEGORY_DEFS}
        for f in files:
            cid = categorize_file_row(f)
            if cid not in buckets:
                cid = "other"
            urls = file_urls(settings, c, f["rel_path"])
            buckets[cid].append(
                {
                    "type": "file",
                    "id": f["id"],
                    "path": f["rel_path"],
                    "rel_path": f["rel_path"],
                    "name": f["name"],
                    "bates": f.get("bates"),
                    "mime": f.get("mime"),
                    "size": f.get("size"),
                    "status": f.get("status"),
                    "category": cid,
                    "category_label": CATEGORY_LABELS.get(cid, cid),
                    "hot_doc": bool(f.get("hot_doc")),
                    "has_note": bool(f.get("has_note")),
                    **urls,
                }
            )

        if category:
            if category not in CATEGORY_LABELS:
                raise HTTPException(404, f"unknown category: {category}")
            entries = buckets.get(category) or []
            return {
                "case": c.__dict__,
                "mode": "category",
                "category": category,
                "category_label": CATEGORY_LABELS[category],
                "count": len(entries),
                "entries": entries,
                "categories": [
                    {
                        "id": cid,
                        "label": label,
                        "count": len(buckets[cid]),
                    }
                    for cid, label in CATEGORY_DEFS
                    if buckets[cid] or cid == category
                ],
            }

        # Root: category "folders"
        cat_entries = []
        for cid, label in CATEGORY_DEFS:
            n = len(buckets[cid])
            if n == 0:
                continue
            cat_entries.append(
                {
                    "type": "category",
                    "id": cid,
                    "path": cid,
                    "name": label,
                    "count": n,
                    "size": None,
                }
            )
        return {
            "case": c.__dict__,
            "mode": "categories",
            "category": None,
            "count": len(files),
            "entries": cat_entries,
            "categories": [
                {"id": cid, "label": label, "count": len(buckets[cid])}
                for cid, label in CATEGORY_DEFS
                if buckets[cid]
            ],
        }

    @app.get("/api/manifest")
    def api_manifest(
        case_id: int | None = None,
        kind: str | None = None,
        ingestible: bool | None = None,
        ingest_status: str | None = None,
        path: str = "",
        limit: int = Query(500, ge=1, le=10000),
        offset: int = Query(0, ge=0),
    ) -> dict[str, Any]:
        """Production inventory + ingest confidence for UX 'what was produced / ingested well'."""
        c = resolve_case(case_id=case_id)
        rows = db.list_production_manifest(
            c.id,
            kind=kind,
            ingestible=ingestible,
            ingest_status=ingest_status,
            prefix=path,
            limit=limit,
            offset=offset,
        )
        summary = db.production_manifest_summary(c.id)
        artifact = db.get_setting(f"manifest_path:{c.id}")
        return {
            "case": c.__dict__,
            "summary": summary,
            "summary_path": artifact,
            "limit": limit,
            "offset": offset,
            "records": rows,
        }

    @app.get("/api/manifest/summary")
    def api_manifest_summary(case_id: int | None = None) -> dict[str, Any]:
        c = resolve_case(case_id=case_id)
        summary = db.production_manifest_summary(c.id)
        artifact = db.get_setting(f"manifest_path:{c.id}")
        disk = None
        confidence = None
        import json

        if artifact and Path(artifact).is_file():
            try:
                disk = json.loads(Path(artifact).read_text(encoding="utf-8"))
            except Exception:
                disk = None
        conf_path = Path(c.files_root) / ".marshaloss" / "ingest_confidence.json"
        if conf_path.is_file():
            try:
                confidence = json.loads(conf_path.read_text(encoding="utf-8"))
            except Exception:
                confidence = None
        return {
            "case": c.__dict__,
            "summary": summary,
            "artifact": disk,
            "confidence": confidence,
        }

    @app.get("/api/search")
    def api_search(
        q: str = Query(...),
        case_id: int | None = None,
        mode: str = "hybrid",
        top_k: int = 12,
    ) -> dict[str, Any]:
        c = resolve_case(case_id=case_id)
        if mode not in ("hybrid", "keyword", "semantic"):
            raise HTTPException(400, "mode must be hybrid|keyword|semantic")
        hits = search_case(db, settings, embedder, c, q, mode=mode, top_k=top_k)  # type: ignore
        return {"case": c.__dict__, "query": q, "mode": mode, "hits": hits}

    @app.get("/api/forensics/summary")
    def api_forensics_summary(case_id: int | None = None) -> dict[str, Any]:
        """Which Forensics subsections exist for this case (UFDR / CSLI hints)."""
        c = resolve_case(case_id=case_id)
        summary = case_forensics_summary(c.files_root)
        return {"case": c.__dict__, **summary}

    @app.get("/api/forensics/cellebrite/chats")
    def api_forensics_cellebrite_chats(
        path: str = Query(..., min_length=1, description="UFDR path relative to case root"),
        case_id: int | None = None,
        refresh: bool = False,
    ) -> dict[str, Any]:
        """List chats from a Cellebrite UFDR (same decoded chats as Reader for this export)."""
        c = resolve_case(case_id=case_id)
        if not ufedlib_available():
            raise HTTPException(
                503,
                "ufedlib-py not installed on server (uv add ufedlib-py && uv sync)",
            )
        try:
            payload = parse_ufdr_chats_for_case(c.files_root, path, use_cache=not refresh)
        except FileNotFoundError:
            raise HTTPException(404, "UFDR not found") from None
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        except Exception as e:
            raise HTTPException(500, f"UFDR parse failed: {e}") from e
        return {
            "case": c.__dict__,
            "rel_path": payload.get("rel_path"),
            "name": payload.get("name"),
            "chat_count": payload.get("chat_count"),
            "message_count": payload.get("message_count"),
            "sources": payload.get("sources") or [],
            "owners": payload.get("owners") or [],
            "directory_size": payload.get("directory_size") or 0,
            "note": payload.get("note"),
            "parser": payload.get("parser"),
            "chats": chat_summaries(payload),
        }

    @app.get("/api/forensics/cellebrite/chat")
    def api_forensics_cellebrite_chat(
        path: str = Query(..., min_length=1),
        chat_id: str = Query(..., min_length=1),
        case_id: int | None = None,
    ) -> dict[str, Any]:
        """One chat thread with messages from a UFDR."""
        c = resolve_case(case_id=case_id)
        if not ufedlib_available():
            raise HTTPException(503, "ufedlib-py not installed on server")
        try:
            payload = parse_ufdr_chats_for_case(c.files_root, path, use_cache=True)
        except FileNotFoundError:
            raise HTTPException(404, "UFDR not found") from None
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        except Exception as e:
            raise HTTPException(500, f"UFDR parse failed: {e}") from e
        chat = get_chat(payload, chat_id)
        if not chat:
            raise HTTPException(404, "chat not found")
        return {"case": c.__dict__, "rel_path": path, "chat": chat}

    @app.get("/api/forensics/csli/map")
    def api_forensics_csli_map(
        case_id: int | None = None,
        refresh: bool = False,
    ) -> dict[str, Any]:
        """Map payload: cell towers + call/location pings from CSLI / tower-dump files."""
        c = resolve_case(case_id=case_id)
        try:
            payload = parse_csli_map_for_case(c.files_root, use_cache=not refresh)
        except Exception as e:
            raise HTTPException(500, f"CSLI map parse failed: {e}") from e
        pins = db.list_map_pins(c.id)
        phone_labels = db.get_csli_phone_labels(c.id)
        return {
            "case": c.__dict__,
            **payload,
            "pins": pins,
            "phone_labels": phone_labels,
        }

    @app.get("/api/forensics/search")
    def api_forensics_search(
        q: str = "",
        case_id: int | None = None,
        top_k: int = 25,
    ) -> dict[str, Any]:
        """Search forensic files + CSLI events + map pins."""
        c = resolve_case(case_id=case_id)
        pins = db.list_map_pins(c.id)
        try:
            result = search_forensics(c.files_root, q, top_k=top_k, map_pins=pins)
        except Exception as e:
            raise HTTPException(500, f"forensics search failed: {e}") from e
        return {"case": c.__dict__, **result}

    @app.get("/api/forensics/map/pins")
    def api_map_pins_list(
        case_id: int | None = None,
        phone: str | None = None,
    ) -> dict[str, Any]:
        c = resolve_case(case_id=case_id)
        return {"case": c.__dict__, "pins": db.list_map_pins(c.id, phone=phone)}

    @app.post("/api/forensics/map/pins")
    async def api_map_pins_add(request: Request) -> dict[str, Any]:
        payload = await request.json()
        case_id = payload.get("case_id")
        c = resolve_case(case_id=int(case_id) if case_id is not None else None)
        try:
            pin = db.add_map_pin(
                c.id,
                label=str(payload.get("label") or "Point of interest"),
                lat=payload.get("lat"),
                lon=payload.get("lon"),
                note=str(payload.get("note") or ""),
                phone=payload.get("phone"),
                source_path=payload.get("source_path") or payload.get("rel_path"),
                kind=str(payload.get("kind") or "user"),
                event_when=payload.get("event_when") or payload.get("when"),
            )
        except (TypeError, ValueError) as e:
            raise HTTPException(400, str(e)) from e
        return {"ok": True, "case": c.__dict__, "pin": pin}

    @app.patch("/api/forensics/map/pins")
    async def api_map_pins_update(request: Request) -> dict[str, Any]:
        payload = await request.json()
        case_id = payload.get("case_id")
        pin_id = payload.get("pin_id") or payload.get("id")
        if pin_id is None:
            raise HTTPException(400, "pin_id required")
        c = resolve_case(case_id=int(case_id) if case_id is not None else None)
        try:
            pin = db.update_map_pin(
                c.id,
                int(pin_id),
                label=payload.get("label"),
                note=payload.get("note"),
                event_when=payload.get("event_when") or payload.get("when"),
                lat=payload.get("lat"),
                lon=payload.get("lon"),
                phone=payload.get("phone"),
            )
        except (TypeError, ValueError) as e:
            raise HTTPException(400, str(e)) from e
        if not pin:
            raise HTTPException(404, "pin not found")
        return {"ok": True, "case": c.__dict__, "pin": pin}

    @app.delete("/api/forensics/map/pins")
    def api_map_pins_delete(
        pin_id: int,
        case_id: int | None = None,
    ) -> dict[str, Any]:
        c = resolve_case(case_id=case_id)
        ok = db.delete_map_pin(c.id, pin_id)
        if not ok:
            raise HTTPException(404, "pin not found")
        return {"ok": True, "id": pin_id}

    @app.put("/api/forensics/csli/phone-label")
    async def api_csli_phone_label(request: Request) -> dict[str, Any]:
        """Rename a CSLI phone for display (does not change source data)."""
        payload = await request.json()
        case_id = payload.get("case_id")
        phone = payload.get("phone")
        label = payload.get("label") or payload.get("name") or ""
        c = resolve_case(case_id=int(case_id) if case_id is not None else None)
        try:
            labels = db.set_csli_phone_label(c.id, str(phone or ""), str(label))
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        return {"ok": True, "case": c.__dict__, "phone_labels": labels}

    @app.get("/api/bwc/index")
    def api_bwc_index(case_id: int | None = None) -> dict[str, Any]:
        """Multi-cam bodycam/dashcam index (timed speech + visual segments)."""
        c = resolve_case(case_id=case_id)
        return build_bwc_index(db, c)

    @app.get("/api/bwc/label-suggestions")
    def api_bwc_label_suggestions(case_id: int | None = None) -> dict[str, Any]:
        """Person/officer entities for camera labeling."""
        c = resolve_case(case_id=case_id)
        return label_suggestions(db, c)

    @app.post("/api/bwc/label")
    async def api_bwc_label(request: Request) -> dict[str, Any]:
        """Rename a camera lane (meta.media.officer) for one clip or all from that camera."""
        payload = await request.json()
        case_id = payload.get("case_id")
        file_id = payload.get("file_id")
        label = payload.get("label")
        scope = payload.get("scope") or "camera"
        if file_id is None:
            raise HTTPException(400, "file_id required")
        c = resolve_case(case_id=int(case_id) if case_id is not None else None)
        try:
            result = apply_camera_label(
                db,
                c,
                file_id=int(file_id),
                label=None if label is None else str(label),
                scope=str(scope),
            )
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        # Return fresh index so the client can refresh lanes without a second round-trip.
        result["index"] = build_bwc_index(db, c)
        return result

    @app.get("/api/files/detail")
    def api_file_detail(
        path: str = Query(..., min_length=1),
        case_id: int | None = None,
    ) -> dict[str, Any]:
        """Document payload for the review UI: text, meta, entities, tags, URLs."""
        c = resolve_case(case_id=case_id)
        detail = db.get_file_detail(c.id, path)
        if not detail:
            raise HTTPException(404, "file not ingested (no DB row); browse disk via /api/files/list")
        urls = file_urls(settings, c, path)
        mime = detail.get("mime") or mimetypes.guess_type(detail.get("name") or path)[0]
        lower = (detail.get("name") or path).lower()
        kind = "other"
        if mime == "application/pdf" or lower.endswith(".pdf"):
            kind = "pdf"
        elif mime and mime.startswith("image/"):
            kind = "image"
        elif mime and mime.startswith("video/"):
            kind = "video"
        elif mime and mime.startswith("audio/"):
            kind = "audio"
        elif mime and (
            mime.startswith("text/")
            or lower.endswith((".txt", ".md", ".csv", ".log", ".json", ".xml", ".kml", ".st3"))
        ):
            kind = "text"
        entities = detail.get("entities") or []
        tags = detail.get("tags") or []
        ent_keys: list[tuple[str, str]] = []
        for e in entities:
            if isinstance(e, dict):
                et = str(e.get("type") or e.get("label") or "other").strip().lower() or "other"
                lab = str(
                    e.get("canonical")
                    or e.get("text")
                    or e.get("value")
                    or e.get("name")
                    or e.get("mention")
                    or ""
                ).strip()
            else:
                et, lab = "other", str(e).strip()
            if lab:
                ent_keys.append((et, lab))
        tag_labels: list[str] = []
        for t in tags:
            if isinstance(t, str):
                lab = t.strip()
            elif isinstance(t, dict):
                lab = str(t.get("name") or t.get("label") or t.get("tag") or "").strip()
            else:
                lab = str(t).strip()
            if lab:
                tag_labels.append(lab)
        facet_counts = db.facet_doc_counts_for_labels(
            c.id, entities=ent_keys, tags=tag_labels
        )
        return {
            "case": c.__dict__,
            "file": {
                "id": detail["id"],
                "rel_path": detail["rel_path"],
                "name": detail["name"],
                "bates": detail.get("bates"),
                "mime": mime,
                "size": detail.get("size"),
                "status": detail.get("status"),
                "error": detail.get("error"),
                "kind": kind,
                "meta": detail.get("meta") or {},
                "manifest": detail.get("manifest"),
                "entities": entities,
                "tags": tags,
                "facet_counts": facet_counts,
                "text": detail.get("text") or "",
                "chunk_count": len(detail.get("chunks") or []),
                "chunks": detail.get("chunks") or [],
                "hot_doc": bool(detail.get("hot_doc")),
                "user_note": detail.get("user_note") or "",
                "user_tags": detail.get("user_tags") or [],
                **urls,
            },
        }

    @app.get("/api/notebook/skills")
    def api_notebook_skills() -> dict[str, Any]:
        from .notebook import list_notebook_skills, notebook_skills_dir

        return {
            "ok": True,
            "dir": str(notebook_skills_dir()),
            "skills": list_notebook_skills(),
        }

    @app.get("/api/notebook/skill")
    def api_notebook_skill(name: str | None = None) -> dict[str, Any]:
        from .notebook import list_notebook_skills, load_notebook_skill_recipe

        if not name:
            return {
                "ok": True,
                "dir": "skills/",
                "overview": load_notebook_skill_text(strip_frontmatter=False),
                "skills": list_notebook_skills(),
                "instructions": (
                    "This is the skill list. Pass ?name=cowork | cross-examination | timeline "
                    "(or another skills/*.md stem) for full Markdown. Mirrored at Meta/Skills/*."
                ),
            }
        try:
            body = load_notebook_skill_recipe(name, strip_frontmatter=False)
        except FileNotFoundError as e:
            raise HTTPException(404, str(e)) from e
        return {
            "ok": True,
            "name": name,
            "wiki_path": f"Meta/Skills/{name}",
            "skill": body,
        }

    @app.get("/api/notebook/pages")
    def api_notebook_pages(case_id: int | None = None) -> dict[str, Any]:
        c = resolve_case(case_id=case_id)
        db.ensure_notebook_home(c.id, c.display_name)
        return {"case": c.__dict__, "pages": db.list_notebook_pages(c.id)}

    @app.get("/api/notebook/page")
    def api_notebook_get(
        path: str = Query(..., min_length=1),
        case_id: int | None = None,
    ) -> dict[str, Any]:
        c = resolve_case(case_id=case_id)
        page = db.get_notebook_page(c.id, path)
        if not page:
            raise HTTPException(404, "notebook page not found")
        return {"case": c.__dict__, "page": page}

    @app.put("/api/notebook/page")
    async def api_notebook_put(request: Request) -> dict[str, Any]:
        payload = await request.json()
        case_id = payload.get("case_id")
        path = payload.get("path") or ""
        title = payload.get("title") or ""
        body = payload.get("body")
        if body is None:
            body = ""
        c = resolve_case(case_id=int(case_id) if case_id is not None else None)
        try:
            page = db.upsert_notebook_page(c.id, str(path), str(title), str(body))
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        return {"ok": True, "case": c.__dict__, "page": page}

    @app.delete("/api/notebook/page")
    def api_notebook_delete(
        path: str = Query(..., min_length=1),
        case_id: int | None = None,
    ) -> dict[str, Any]:
        c = resolve_case(case_id=case_id)
        ok = db.delete_notebook_page(c.id, path)
        if not ok:
            raise HTTPException(404, "notebook page not found")
        return {"ok": True, "path": db.normalize_notebook_path(path)}

    @app.post("/api/notebook/witness-pack")
    async def api_notebook_witness_pack(request: Request) -> dict[str, Any]:
        payload = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}
        case_id = payload.get("case_id")
        min_count = int(payload.get("min_count") or 2)
        max_witnesses = int(payload.get("max_witnesses") or 40)
        c = resolve_case(case_id=int(case_id) if case_id is not None else None)
        result = build_witness_pack(
            db,
            settings,
            c,
            min_count=max(1, min_count),
            max_witnesses=max(1, min(max_witnesses, 100)),
        )
        return {"ok": True, **result}

    @app.get("/browser", response_class=HTMLResponse)
    def browser() -> HTMLResponse:
        html_path = WEB_DIR / "browser.html"
        if not html_path.exists():
            return HTMLResponse("<h1>browser.html missing</h1>", status_code=500)
        return HTMLResponse(html_path.read_text(encoding="utf-8"))

    @app.get("/app", response_class=HTMLResponse)
    @app.get("/", response_class=HTMLResponse)
    def root() -> HTMLResponse:
        html_path = WEB_DIR / "app.html"
        if not html_path.exists():
            return HTMLResponse(
                "<h1>Marshal</h1><p><a href=/browser>Legacy browser</a></p>",
                status_code=500,
            )
        return HTMLResponse(html_path.read_text(encoding="utf-8"))

    def _safe_file(slug: str, rel: str) -> tuple[Any, Path]:
        c = resolve_case(slug=slug)
        root = Path(c.files_root).resolve()
        parts = [unquote(p) for p in rel.split("/") if p]
        target = (root.joinpath(*parts)).resolve()
        if not str(target).startswith(str(root)):
            raise HTTPException(403, "path escapes root")
        if not target.is_file():
            raise HTTPException(404, "file not found")
        return c, target

    @app.get("/files/{slug}/{rel_path:path}")
    def serve_file(slug: str, rel_path: str, dl: int = 0) -> FileResponse:
        _c, target = _safe_file(slug, rel_path)
        mime = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        return FileResponse(
            path=str(target),
            media_type=mime,
            filename=target.name,
            content_disposition_type="attachment" if dl == 1 else "inline",
        )

    @app.get("/view/{slug}/{rel_path:path}")
    def view_file(slug: str, rel_path: str) -> Response:
        _c, target = _safe_file(slug, rel_path)
        mime = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        # Relative URLs so preview works over Tailscale / any host (not public_base localhost).
        enc = encode_rel_path("/".join(unquote(p) for p in rel_path.split("/") if p))
        raw_url = f"/files/{slug}/{enc}"
        download_url = f"{raw_url}?dl=1"
        name = target.name
        safe = name.replace("<", "").replace(">", "").replace('"', "")
        lower = name.lower()
        is_pdf = mime == "application/pdf" or lower.endswith(".pdf")
        is_image = mime.startswith("image/")
        is_text = mime.startswith("text/") or lower.endswith((".txt", ".md", ".csv", ".log", ".json", ".xml", ".kml", ".st3"))
        if is_pdf or is_image:
            pdf_src = f"{raw_url}#toolbar=0&navpanes=0&scrollbar=0&view=FitH"
            media = (
                f"<iframe src='{pdf_src}' title='{safe}'></iframe>"
                if is_pdf
                else f"<img src='{raw_url}' alt='{safe}'/>"
            )
            body = f"""<!doctype html><html><head><meta charset=utf-8><title>{safe}</title>
            <style>html,body{{margin:0;height:100%;background:#111;overflow:hidden}}
            iframe,img{{width:100%;height:100%;border:0;display:block}}
            img{{object-fit:contain}}</style></head>
            <body>{media}</body></html>"""
            return HTMLResponse(body)
        if is_text and target.stat().st_size < 2_000_000:
            text = target.read_text(encoding="utf-8", errors="replace")
            esc = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            return HTMLResponse(
                f"""<!doctype html><meta charset=utf-8><title>{safe}</title>
                <style>body{{margin:0;background:#111;color:#eee;font:13px/1.45 ui-monospace,Menlo,monospace}}
                header{{padding:8px 12px;background:#1b1b1b;font:14px system-ui}}
                a{{color:#9fdfc2}}pre{{padding:16px;white-space:pre-wrap}}</style>
                <header>{safe} · <a href="{download_url}">Download</a></header><pre>{esc}</pre>"""
            )
        return FileResponse(str(target), filename=name, content_disposition_type="attachment")

    # --- MCP JSON tools via simple HTTP helpers used by mcp_app ---
    @app.get("/api/mcp/instructions")
    def api_mcp_instructions(full: bool = False) -> dict[str, Any]:
        """Short pointer by default; ?full=1 returns the Cowork playbook Markdown."""
        from .notebook import load_cowork_playbook, playbook_pointer

        ptr = playbook_pointer(settings.public_base)
        if not full:
            return {
                "ok": True,
                "summary": (
                    "search/ask → get_file (page if needed) → notebook_upsert with [[doc:]]. "
                    "Forensics: forensics_overview / csli_events / forensics_search "
                    "(pull data; you analyze). Optional map_pin_*. "
                    "Discovery read-only; writable: notebook, user_note, hot_doc, user_tags, map pins."
                ),
                "playbook": ptr,
            }
        return {
            "ok": True,
            "playbook": ptr,
            "skill": load_cowork_playbook(strip_frontmatter=False),
        }

    @app.get("/api/mcp/tools")
    def mcp_tools_catalog() -> dict[str, Any]:
        from .notebook import playbook_pointer

        return {
            "discovery_writable": False,
            "notes": (
                "Discovery files on disk are read-only via MCP/API. "
                "Writable surfaces: notebook pages, file user_note, file hot_doc, file user_tags, "
                "forensics map pins (map_pin_add / map_pin_update / map_pin_delete / csli_phone_rename). "
                "list_flagged_files returns hot docs and docs with notes."
            ),
            "playbook": playbook_pointer(settings.public_base),
            "tools": [
                "list_cases",
                "set_active_case",
                "get_active_case",
                "get_instructions",
                "browse_case_files",
                "search",
                "ask",
                "get_file",
                "get_file_link",
                "open_file_browser",
                "set_file_note",
                "set_file_hot_doc",
                "list_flagged_files",
                "set_file_tags",
                "forensics_overview",
                "csli_events",
                "forensics_search",
                "map_pin_list",
                "map_pin_add",
                "map_pin_update",
                "map_pin_delete",
                "csli_phone_rename",
                "notebook_list",
                "notebook_get",
                "notebook_upsert",
                "notebook_delete",
                "notebook_skill",
                "notebook_build_witness_pack",
            ],
        }

    @app.get("/api/files/flagged")
    def api_files_flagged(case_id: int | None = None) -> dict[str, Any]:
        """Hot docs and documents with reviewer notes."""
        c = resolve_case(case_id=case_id)
        entries = []
        for f in db.list_flagged_files(c.id):
            urls = file_urls(settings, c, f["rel_path"])
            entries.append({**f, **urls})
        return {
            "case": c.__dict__,
            "mode": "notes",
            "count": len(entries),
            "entries": entries,
        }

    @app.put("/api/files/note")
    async def api_file_note(request: Request) -> dict[str, Any]:
        payload = await request.json()
        case_id = payload.get("case_id")
        path = str(payload.get("path") or "")
        note = payload.get("note")
        if note is None:
            note = ""
        c = resolve_case(case_id=int(case_id) if case_id is not None else None)
        try:
            out = db.set_file_user_note(c.id, path, str(note))
        except ValueError as e:
            raise HTTPException(404, str(e)) from e
        return {"ok": True, "case": c.__dict__, **out}

    @app.put("/api/files/tags")
    async def api_file_tags(request: Request) -> dict[str, Any]:
        payload = await request.json()
        case_id = payload.get("case_id")
        path = str(payload.get("path") or "")
        tags = payload.get("tags") or []
        if not isinstance(tags, list):
            raise HTTPException(400, "tags must be a list of strings")
        c = resolve_case(case_id=int(case_id) if case_id is not None else None)
        # Prefer meta.tags when `kind` is document; keep user_tags for MCP compatibility.
        kind = str(payload.get("kind") or "document").lower()
        try:
            if kind in ("user", "user_tags"):
                out = db.set_file_user_tags(c.id, path, [str(t) for t in tags])
            else:
                out = db.set_file_tags(c.id, path, [str(t) for t in tags])
        except ValueError as e:
            raise HTTPException(404, str(e)) from e
        return {"ok": True, "case": c.__dict__, **out}

    @app.put("/api/files/entities")
    async def api_file_entities(request: Request) -> dict[str, Any]:
        payload = await request.json()
        case_id = payload.get("case_id")
        path = str(payload.get("path") or "")
        entities = payload.get("entities") or []
        if not isinstance(entities, list):
            raise HTTPException(400, "entities must be a list")
        c = resolve_case(case_id=int(case_id) if case_id is not None else None)
        try:
            out = db.set_file_entities(c.id, path, entities)
        except ValueError as e:
            raise HTTPException(404, str(e)) from e
        return {"ok": True, "case": c.__dict__, **out}

    @app.put("/api/files/hot")
    async def api_file_hot(request: Request) -> dict[str, Any]:
        payload = await request.json()
        case_id = payload.get("case_id")
        path = str(payload.get("path") or "")
        hot = bool(payload.get("hot"))
        c = resolve_case(case_id=int(case_id) if case_id is not None else None)
        try:
            out = db.set_file_hot_doc(c.id, path, hot)
        except ValueError as e:
            raise HTTPException(404, str(e)) from e
        return {"ok": True, "case": c.__dict__, **out}

    return app


def build_mcp_server(settings: Settings, db: Database, embedder: Embedder):
    """
    FastMCP server sharing the same DB/search stack.

    Discovery dump on disk is **read-only** here (no create/delete/modify of
    production files). Writable via MCP: notebook pages, per-file user_note,
    per-file hot_doc, per-file user_tags, forensics map pins. Ingest remains a
    separate CLI/ops path.
    """
    from mcp.server.mcpserver import MCPServer

    mcp = MCPServer("marshal")

    def active_or(case_id: int | None = None):
        if case_id is not None:
            c = db.get_case_by_id(case_id)
        else:
            c = db.get_active_case()
        if not c:
            raise ValueError("No active case. Ingest a case first.")
        return c

    def resolve_under_root(case: Any, path: str) -> Path:
        root = Path(case.files_root).resolve()
        target = (root / path).resolve()
        if not str(target).startswith(str(root)):
            raise ValueError("path escapes case root")
        return target

    @mcp.tool()
    def list_cases() -> dict[str, Any]:
        """List Marshal cases."""
        return {"cases": [c.__dict__ for c in db.list_cases()], "active": getattr(db.get_active_case(), "__dict__", None)}

    @mcp.tool()
    def set_active_case(case_id: int) -> dict[str, Any]:
        """Set the active case for subsequent search/browse calls."""
        c = db.set_active_case(case_id)
        return {"ok": True, "active": c.__dict__}

    @mcp.tool()
    def get_active_case() -> dict[str, Any]:
        """Return the active case."""
        from .notebook import playbook_pointer

        c = db.get_active_case()
        return {
            "active": c.__dict__ if c else None,
            "playbook": playbook_pointer(settings.public_base),
        }

    @mcp.tool()
    def get_instructions(full: bool = False) -> dict[str, Any]:
        """Return a short Cowork playbook pointer, or full=True for the complete Markdown skill (skills/cowork.md). Prefer the pointer unless you need the full rules."""
        from .notebook import load_cowork_playbook, playbook_pointer

        ptr = playbook_pointer(settings.public_base)
        if not full:
            return {
                "ok": True,
                "summary": (
                    "search/ask → get_file (page if needed) → notebook_upsert with [[doc:]]. "
                    "Forensics: forensics_overview / csli_events / forensics_search "
                    "(pull data; you analyze). Optional map_pin_*. "
                    "Discovery read-only; writable: notebook, user_note, hot_doc, user_tags, map pins."
                ),
                "playbook": ptr,
            }
        return {
            "ok": True,
            "playbook": ptr,
            "skill": load_cowork_playbook(strip_frontmatter=False),
        }

    @mcp.tool()
    def browse_case_files(case_id: int | None = None, path: str = "") -> dict[str, Any]:
        """Read-only: list files/folders with Bates labels and open_url/download_url."""
        c = active_or(case_id)
        data = list_dir(db, settings, c, path)
        data["browser_url"] = f"{settings.public_base.rstrip('/')}/browser?case_id={c.id}"
        if path:
            data["browser_url"] += f"&path={encode_rel_path(path)}"
        return data

    @mcp.tool()
    def search(
        query: str,
        case_id: int | None = None,
        mode: str = "hybrid",
        top_k: int = 12,
    ) -> dict[str, Any]:
        """Read-only: hybrid/keyword/semantic search. Returns structured hits (Bates, snippet, rel_path, open_url). Prefer this when you need individual chunks to cite or push into the notebook."""
        from .notebook import playbook_pointer

        c = active_or(case_id)
        if mode not in ("hybrid", "keyword", "semantic"):
            raise ValueError("mode must be hybrid|keyword|semantic")
        hits = search_case(db, settings, embedder, c, query, mode=mode, top_k=top_k)  # type: ignore
        return {
            "case": c.__dict__,
            "query": query,
            "mode": mode,
            "hits": hits,
            "playbook": playbook_pointer(settings.public_base),
        }

    @mcp.tool()
    def ask(question: str, case_id: int | None = None, top_k: int = 8) -> dict[str, Any]:
        """Read-only convenience over search: same hybrid hits plus a single packed `context` string for the host LLM to answer. Use search when you need structured cites; use ask when drafting an answer from chunks."""
        from .notebook import playbook_pointer

        c = active_or(case_id)
        hits = search_case(db, settings, embedder, c, question, mode="hybrid", top_k=top_k)
        context = "\n\n".join(
            f"[{h.get('bates') or h.get('name')} | {h.get('rel_path')}]\n{h.get('snippet')}"
            for h in hits
        )
        return {
            "case": c.__dict__,
            "question": question,
            "hits": hits,
            "context": context,
            "instructions": (
                "Answer from context/hits. Cite rel_path with [[doc:rel_path|label]] "
                "when writing notebook pages. Discovery files are read-only."
            ),
            "playbook": playbook_pointer(settings.public_base),
        }

    @mcp.tool()
    def get_file(
        path: str,
        case_id: int | None = None,
        max_chars: int = 8000,
        offset_chars: int = 0,
        chunk_offset: int = 0,
        chunk_limit: int = 0,
    ) -> dict[str, Any]:
        """
        Read-only: metadata + extracted text for a discovery file (never dumps the binary).

        Defaults return a modest text window so you do not load a whole exhibit at once.
        To walk the full extracted text, loop with offset_chars += max_chars while has_more
        is true (or set max_chars=0 for one shot). Optional chunk_offset/chunk_limit pages
        ingest chunks (page/time metadata) instead of a flat string.
        """
        c = active_or(case_id)
        resolve_under_root(c, path)  # path-traversal guard; file may be DB-only if missing on disk
        detail = db.get_file_detail(c.id, path)
        if not detail:
            raise ValueError(f"file not found: {path}")
        full = detail.get("text") or ""
        total = len(full)
        start = max(0, int(offset_chars or 0))
        if max_chars is None or int(max_chars) < 0:
            raise ValueError("max_chars must be >= 0 (0 = remainder from offset)")
        limit = int(max_chars)
        if limit == 0:
            text = full[start:]
            next_off = None
            has_more = False
        else:
            text = full[start : start + limit]
            nxt = start + len(text)
            has_more = nxt < total
            next_off = nxt if has_more else None

        chunks_out: list[dict[str, Any]] | None = None
        chunks_has_more = False
        chunks_next: int | None = None
        all_chunks = detail.get("chunks") or []
        n_chunks = len(all_chunks)
        if int(chunk_limit or 0) > 0:
            c0 = max(0, int(chunk_offset or 0))
            c1 = c0 + int(chunk_limit)
            page = all_chunks[c0:c1]
            chunks_out = [
                {
                    "chunk_index": ch.get("chunk_index"),
                    "page_start": ch.get("page_start"),
                    "page_end": ch.get("page_end"),
                    "heading": ch.get("heading"),
                    "start_sec": ch.get("start_sec"),
                    "end_sec": ch.get("end_sec"),
                    "text": ch.get("text") or "",
                }
                for ch in page
            ]
            chunks_has_more = c1 < n_chunks
            chunks_next = c1 if chunks_has_more else None

        return {
            "ok": True,
            "case": c.__dict__,
            "path": path,
            "name": detail.get("name"),
            "bates": detail.get("bates"),
            "mime": detail.get("mime"),
            "status": detail.get("status"),
            "entities": detail.get("entities") or [],
            "tags": detail.get("tags") or [],
            "user_note": detail.get("user_note") or "",
            "user_tags": detail.get("user_tags") or [],
            "hot_doc": bool(detail.get("hot_doc")),
            "text": text,
            "text_total_chars": total,
            "offset_chars": start,
            "max_chars": limit,
            "next_offset_chars": next_off,
            "has_more": has_more,
            "chunk_count": n_chunks,
            "chunks": chunks_out,
            "chunk_offset": int(chunk_offset or 0) if chunks_out is not None else None,
            "chunk_limit": int(chunk_limit or 0) if chunks_out is not None else None,
            "chunks_next_offset": chunks_next,
            "chunks_has_more": chunks_has_more if chunks_out is not None else None,
            "wiki_doc_link": f"[[doc:{path}|{detail.get('bates') or detail.get('name') or path}]]",
            **file_urls(settings, c, path),
        }

    @mcp.tool()
    def get_file_link(path: str, case_id: int | None = None) -> dict[str, Any]:
        """Read-only: open/download URLs for a case-relative discovery path."""
        c = active_or(case_id)
        target = resolve_under_root(c, path)
        if not target.is_file():
            raise ValueError("file not found under case root")
        row = db.get_file(c.id, path)
        return {
            "ok": True,
            "path": path,
            "name": target.name,
            "bates": row["bates"] if row else None,
            "size": target.stat().st_size,
            **file_urls(settings, c, path),
        }

    @mcp.tool()
    def open_file_browser(case_id: int | None = None, path: str = "") -> dict[str, Any]:
        """Read-only: URL to the Marshal Document Explorer for this path."""
        c = active_or(case_id)
        base = settings.public_base.rstrip("/")
        if path:
            url = f"{base}/#/cases/{c.id}?view=explorer&browse=folders&path={encode_rel_path(path)}"
        else:
            url = f"{base}/#/cases/{c.id}?view=explorer"
        return {"ok": True, "browser_url": url, "case": c.__dict__}

    @mcp.tool()
    def set_file_note(path: str, note: str, case_id: int | None = None) -> dict[str, Any]:
        """Writable annotation: set/clear reviewer note on a discovery file (SQLite meta only — never writes discovery bytes). Shows in Document Explorer Notes tab; non-empty notes appear in list_flagged_files. Pass empty string to clear."""
        c = active_or(case_id)
        out = db.set_file_user_note(c.id, path, note)
        return {"ok": True, "case": c.__dict__, **out}

    @mcp.tool()
    def set_file_hot_doc(path: str, hot: bool = True, case_id: int | None = None) -> dict[str, Any]:
        """Writable annotation: mark or unmark a discovery file as a hot doc (SQLite meta only). Hot docs show a diamond in Explorer and appear in list_flagged_files / the Notes browse list. Set hot=false to clear."""
        c = active_or(case_id)
        out = db.set_file_hot_doc(c.id, path, bool(hot))
        return {"ok": True, "case": c.__dict__, **out}

    @mcp.tool()
    def list_flagged_files(case_id: int | None = None) -> dict[str, Any]:
        """List discovery files flagged as hot_doc and/or that have a non-empty reviewer note. Same set as Document Explorer → Notes."""
        c = active_or(case_id)
        entries = []
        for f in db.list_flagged_files(c.id):
            entries.append({**f, **file_urls(settings, c, f["rel_path"])})
        return {
            "ok": True,
            "case": c.__dict__,
            "count": len(entries),
            "entries": entries,
        }

    @mcp.tool()
    def set_file_tags(path: str, tags: list[str], case_id: int | None = None) -> dict[str, Any]:
        """Writable annotation: set reviewer tags on a file (SQLite meta only — never writes discovery bytes). Ingest/AI tags remain separate."""
        c = active_or(case_id)
        out = db.set_file_user_tags(c.id, path, list(tags or []))
        return {"ok": True, "case": c.__dict__, **out}

    @mcp.tool()
    def notebook_list(case_id: int | None = None) -> dict[str, Any]:
        """List all notebook wiki pages for the case (dynamic tree — no fixed schema)."""
        c = active_or(case_id)
        db.ensure_notebook_home(c.id, c.display_name)
        return {"case": c.__dict__, "pages": db.list_notebook_pages(c.id)}

    @mcp.tool()
    def notebook_get(path: str, case_id: int | None = None) -> dict[str, Any]:
        """Read a notebook page by path (e.g. 'Theory' or 'Witnesses/Jane Doe/Cross outline')."""
        c = active_or(case_id)
        page = db.get_notebook_page(c.id, path)
        if not page:
            raise ValueError(f"notebook page not found: {path}")
        return {"case": c.__dict__, "page": page}

    @mcp.tool()
    def notebook_upsert(
        path: str,
        body: str,
        title: str = "",
        case_id: int | None = None,
    ) -> dict[str, Any]:
        """Writable: create/update a notebook Markdown page (not discovery files). Use [[doc:rel/path|label]] and [[page:Path|label]]. Load recipes via notebook_skill."""
        c = active_or(case_id)
        page = db.upsert_notebook_page(c.id, path, title or path.split("/")[-1], body)
        return {"ok": True, "page": page}

    @mcp.tool()
    def notebook_delete(path: str, case_id: int | None = None) -> dict[str, Any]:
        """Writable: delete a notebook page (never deletes discovery files)."""
        c = active_or(case_id)
        ok = db.delete_notebook_page(c.id, path)
        if not ok:
            raise ValueError(f"notebook page not found: {path}")
        return {"ok": True, "path": db.normalize_notebook_path(path)}

    @mcp.tool()
    def notebook_skill(name: str | None = None) -> dict[str, Any]:
        """List git-backed notebook recipes, or return one by name (e.g. cross-examination, timeline). Prefer this over reinventing prompts."""
        from .notebook import list_notebook_skills, load_notebook_skill_recipe

        if not name:
            return {
                "ok": True,
                "dir": "skills/",
                "overview": load_notebook_skill_text(strip_frontmatter=False),
                "skills": list_notebook_skills(),
                "instructions": (
                    "Skill list from repo skills/. Call again with name='cowork', "
                    "'cross-examination', 'timeline', or another skills/*.md stem. "
                    "Mirrored in the wiki at Meta/Skills/*."
                ),
            }
        try:
            body = load_notebook_skill_recipe(name, strip_frontmatter=False)
        except FileNotFoundError as e:
            raise ValueError(str(e)) from e
        return {
            "ok": True,
            "name": name,
            "wiki_path": f"Meta/Skills/{name}",
            "skill": body,
        }

    @mcp.tool()
    def notebook_build_witness_pack(
        case_id: int | None = None,
        min_count: int = 2,
        max_witnesses: int = 40,
    ) -> dict[str, Any]:
        """Optional helper: seed Witnesses/* from person entities. Prefer following the cross-examination skill via notebook_skill for full exam prep."""
        c = active_or(case_id)
        return build_witness_pack(
            db,
            settings,
            c,
            min_count=max(1, int(min_count)),
            max_witnesses=max(1, min(int(max_witnesses), 100)),
        )

    @mcp.tool()
    def forensics_overview(case_id: int | None = None) -> dict[str, Any]:
        """List forensic sources in the case (CSLI spreadsheets / AT&T returns, Cellebrite UFDRs). Use before csli_events or forensics_search."""
        c = active_or(case_id)
        summary = case_forensics_summary(c.files_root)
        return {
            "case": c.__dict__,
            **summary,
            "instructions": (
                "Pull CSLI rows with csli_events (filter by phone/day/start/end as needed). "
                "Search loosely with forensics_search. You analyze — Marshal only supplies data."
            ),
        }

    @mcp.tool()
    def csli_events(
        case_id: int | None = None,
        phone: str | None = None,
        day: str | None = None,
        start: str | None = None,
        end: str | None = None,
        source_path: str | None = None,
        limit: int = 200,
        offset: int = 0,
        refresh: bool = False,
    ) -> dict[str, Any]:
        """Pull CSLI / cell-site events for analysis. Filter optionally by phone, day (YYYY-MM-DD), start/end (as-recorded timestamps), source_path substring. Page with offset while has_more. Times include source_tz (AT&T is UTC). Tower lat/lon — not handset GPS. Analyze arbitrarily in Cowork."""
        c = active_or(case_id)
        result = query_csli_events(
            c.files_root,
            phone=phone,
            day=day,
            start=start,
            end=end,
            source_path=source_path,
            limit=limit,
            offset=offset,
            use_cache=not refresh,
        )
        return {"case": c.__dict__, **result}

    @mcp.tool()
    def forensics_search(
        query: str,
        case_id: int | None = None,
        top_k: int = 25,
    ) -> dict[str, Any]:
        """Keyword search over forensic file names and CSLI events. For bulk pull + your own analysis prefer csli_events. pin_ready hits can be passed to map_pin_add."""
        c = active_or(case_id)
        pins = db.list_map_pins(c.id)
        result = search_forensics(c.files_root, query, top_k=top_k, map_pins=pins)
        return {"case": c.__dict__, **result}

    @mcp.tool()
    def map_pin_list(case_id: int | None = None, phone: str | None = None) -> dict[str, Any]:
        """List CSLI map pins (points of interest) for the case. Optional phone filter shows global pins + that phone's pins."""
        c = active_or(case_id)
        return {"case": c.__dict__, "pins": db.list_map_pins(c.id, phone=phone)}

    @mcp.tool()
    def map_pin_add(
        label: str,
        lat: float,
        lon: float,
        note: str = "",
        phone: str | None = None,
        source_path: str | None = None,
        event_when: str | None = None,
        case_id: int | None = None,
    ) -> dict[str, Any]:
        """Writable: pin a lat/lon on the Forensics CSLI map. Pins only appear for the event_when date (and near that time when a timeline stop is selected). Prefer event_when from forensics_search hits."""
        c = active_or(case_id)
        pin = db.add_map_pin(
            c.id,
            label=label,
            lat=lat,
            lon=lon,
            note=note or "",
            phone=phone,
            source_path=source_path,
            kind="user",
            event_when=event_when,
        )
        return {
            "ok": True,
            "pin": pin,
            "map_url": f"{settings.public_base.rstrip('/')}/#/cases/{c.id}?module=forensics&csli=1",
        }

    @mcp.tool()
    def map_pin_delete(pin_id: int, case_id: int | None = None) -> dict[str, Any]:
        """Writable: remove a map pin by id from map_pin_list."""
        c = active_or(case_id)
        ok = db.delete_map_pin(c.id, int(pin_id))
        if not ok:
            raise ValueError(f"pin not found: {pin_id}")
        return {"ok": True, "id": int(pin_id)}

    @mcp.tool()
    def map_pin_update(
        pin_id: int,
        label: str | None = None,
        note: str | None = None,
        event_when: str | None = None,
        lat: float | None = None,
        lon: float | None = None,
        phone: str | None = None,
        case_id: int | None = None,
    ) -> dict[str, Any]:
        """Writable: edit an existing map pin (label, note, event_when, coords, phone)."""
        c = active_or(case_id)
        pin = db.update_map_pin(
            c.id,
            int(pin_id),
            label=label,
            note=note,
            event_when=event_when,
            lat=lat,
            lon=lon,
            phone=phone,
        )
        if not pin:
            raise ValueError(f"pin not found: {pin_id}")
        return {"ok": True, "pin": pin}

    @mcp.tool()
    def csli_phone_rename(
        phone: str,
        label: str,
        case_id: int | None = None,
    ) -> dict[str, Any]:
        """Writable: set a display name for a CSLI phone number (UI + tools). Empty label clears it."""
        c = active_or(case_id)
        labels = db.set_csli_phone_label(c.id, phone, label)
        return {"ok": True, "phone_labels": labels}

    return mcp
