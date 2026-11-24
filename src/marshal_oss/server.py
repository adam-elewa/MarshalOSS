from __future__ import annotations

import mimetypes
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any
from urllib.parse import unquote

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from .categories import CATEGORY_DEFS, CATEGORY_LABELS, categorize_file_row
from .config import Settings, get_settings
from .db import Database
from .embed import Embedder
from .fsbrowse import list_dir
from .notebook import build_witness_pack
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

    app = FastAPI(title="MarshalOSS", version="0.1.0", lifespan=lifespan)
    app.state.settings = settings
    app.state.db = db
    app.state.embedder = embedder

    if WEB_DIR.is_dir():
        app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")

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

    @app.get("/api/cases/{case_id}/overview")
    def api_case_overview(case_id: int) -> dict[str, Any]:
        """Case-level stats + full production manifest for the review UI overview pane."""
        c = resolve_case(case_id=case_id)
        summary = db.production_manifest_summary(c.id)
        entities = db.case_entity_tag_stats(c.id)
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
        return {
            "case": c.__dict__,
            "summary": summary,
            "entities": entities,
            "manifest": manifest,
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
                "entities": detail.get("entities") or [],
                "tags": detail.get("tags") or [],
                "text": detail.get("text") or "",
                "chunk_count": len(detail.get("chunks") or []),
                "chunks": detail.get("chunks") or [],
                **urls,
            },
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
                "<h1>MarshalOSS</h1><p><a href=/browser>Legacy browser</a></p>",
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
            body = f"""<!doctype html><html><head><meta charset=utf-8><title>{safe}</title>
            <style>html,body{{margin:0;height:100%;background:#111;color:#eee;font:14px system-ui}}
            header{{padding:8px 12px;background:#1b1b1b;display:flex;justify-content:space-between}}
            a{{color:#9fdfc2}}iframe,img{{width:100%;height:calc(100vh - 42px);border:0}}
            img{{object-fit:contain;height:auto;max-height:calc(100vh - 42px);display:block;margin:0 auto}}</style></head>
            <body><header><strong>{safe}</strong><a href="{download_url}">Download</a></header>
            {"<iframe src='" + raw_url + "'></iframe>" if is_pdf else "<img src='" + raw_url + "'/>"}
            </body></html>"""
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
    @app.get("/api/mcp/tools")
    def mcp_tools_catalog() -> dict[str, Any]:
        return {
            "tools": [
                "list_cases",
                "set_active_case",
                "get_active_case",
                "browse_case_files",
                "search",
                "ask",
                "get_file_link",
                "open_file_browser",
                "notebook_list",
                "notebook_get",
                "notebook_upsert",
                "notebook_delete",
                "notebook_build_witness_pack",
            ]
        }

    return app


def build_mcp_server(settings: Settings, db: Database, embedder: Embedder):
    """FastMCP server sharing the same DB/search stack."""
    from mcp.server.mcpserver import MCPServer

    mcp = MCPServer("marshaloss")

    def active_or(case_id: int | None = None):
        if case_id is not None:
            c = db.get_case_by_id(case_id)
        else:
            c = db.get_active_case()
        if not c:
            raise ValueError("No active case. Ingest a case first.")
        return c

    @mcp.tool()
    def list_cases() -> dict[str, Any]:
        """List MarshalOSS cases."""
        return {"cases": [c.__dict__ for c in db.list_cases()], "active": getattr(db.get_active_case(), "__dict__", None)}

    @mcp.tool()
    def set_active_case(case_id: int) -> dict[str, Any]:
        """Set the active case for subsequent search/browse calls."""
        c = db.set_active_case(case_id)
        return {"ok": True, "active": c.__dict__}

    @mcp.tool()
    def get_active_case() -> dict[str, Any]:
        """Return the active case."""
        c = db.get_active_case()
        return {"active": c.__dict__ if c else None}

    @mcp.tool()
    def browse_case_files(case_id: int | None = None, path: str = "") -> dict[str, Any]:
        """List files/folders with Bates labels and open_url/download_url for files."""
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
        """Hybrid/keyword/semantic search over the active case. Returns Bates, snippets, open_url."""
        c = active_or(case_id)
        if mode not in ("hybrid", "keyword", "semantic"):
            raise ValueError("mode must be hybrid|keyword|semantic")
        hits = search_case(db, settings, embedder, c, query, mode=mode, top_k=top_k)  # type: ignore
        return {"case": c.__dict__, "query": query, "mode": mode, "hits": hits}

    @mcp.tool()
    def ask(question: str, case_id: int | None = None, top_k: int = 8) -> dict[str, Any]:
        """Retrieve top hybrid chunks for a question (client LLM does final RAG reasoning)."""
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
            "instructions": "Use the context/hits to answer. Open open_url for full documents.",
        }

    @mcp.tool()
    def get_file_link(path: str, case_id: int | None = None) -> dict[str, Any]:
        """Return open/download URLs (via PUBLIC_BASE) for a case-relative file path."""
        c = active_or(case_id)
        root = Path(c.files_root)
        target = (root / path).resolve()
        if not str(target).startswith(str(root.resolve())) or not target.is_file():
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
        """Return URL to the MarshalOSS case UI (Document Explorer)."""
        c = active_or(case_id)
        base = settings.public_base.rstrip("/")
        if path:
            url = f"{base}/#/cases/{c.id}?view=explorer&browse=folders&path={encode_rel_path(path)}"
        else:
            url = f"{base}/#/cases/{c.id}?view=explorer"
        return {"ok": True, "browser_url": url, "case": c.__dict__}

    @mcp.tool()
    def notebook_list(case_id: int | None = None) -> dict[str, Any]:
        """List case notebook (wiki) pages: path, title, updated_at."""
        c = active_or(case_id)
        db.ensure_notebook_home(c.id, c.display_name)
        return {"case": c.__dict__, "pages": db.list_notebook_pages(c.id)}

    @mcp.tool()
    def notebook_get(path: str, case_id: int | None = None) -> dict[str, Any]:
        """Read a notebook page by path (e.g. 'Witnesses/Jane Doe/Cross outline')."""
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
        """Create or update a notebook Markdown page. Use [[doc:rel/path|label]] and [[page:Path|label]] wiki links."""
        c = active_or(case_id)
        page = db.upsert_notebook_page(c.id, path, title or path.split("/")[-1], body)
        return {"ok": True, "page": page}

    @mcp.tool()
    def notebook_delete(path: str, case_id: int | None = None) -> dict[str, Any]:
        """Delete a notebook page by path."""
        c = active_or(case_id)
        ok = db.delete_notebook_page(c.id, path)
        if not ok:
            raise ValueError(f"notebook page not found: {path}")
        return {"ok": True, "path": db.normalize_notebook_path(path)}

    @mcp.tool()
    def notebook_build_witness_pack(
        case_id: int | None = None,
        min_count: int = 2,
        max_witnesses: int = 40,
    ) -> dict[str, Any]:
        """Scaffold Witnesses pages + per-witness folders and cross-outline stubs with [[doc:…]] links from person entities."""
        c = active_or(case_id)
        return build_witness_pack(
            db,
            settings,
            c,
            min_count=max(1, int(min_count)),
            max_witnesses=max(1, min(int(max_witnesses), 100)),
        )

    return mcp
