from __future__ import annotations

import mimetypes
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any
from urllib.parse import unquote

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from .config import Settings, get_settings
from .db import Database
from .embed import Embedder
from .fsbrowse import list_dir
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

    @app.get("/browser", response_class=HTMLResponse)
    def browser() -> HTMLResponse:
        html_path = WEB_DIR / "browser.html"
        if not html_path.exists():
            return HTMLResponse("<h1>browser.html missing</h1>", status_code=500)
        return HTMLResponse(html_path.read_text(encoding="utf-8"))

    @app.get("/")
    def root() -> HTMLResponse:
        return HTMLResponse(
            """<!doctype html><meta charset=utf-8>
            <title>MarshalOSS</title>
            <body style="font:14px system-ui;padding:24px">
            <h1>MarshalOSS</h1>
            <p><a href="/browser">Case file browser</a></p>
            <p>Health: <a href="/health">/health</a></p>
            <p>MCP: <code>/mcp</code></p>
            </body>"""
        )

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
        c, target = _safe_file(slug, rel_path)
        mime = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        urls = file_urls(settings, c, "/".join(unquote(p) for p in rel_path.split("/") if p))
        name = target.name
        safe = name.replace("<", "").replace(">", "").replace('"', "")
        lower = name.lower()
        is_pdf = mime == "application/pdf" or lower.endswith(".pdf")
        is_image = mime.startswith("image/")
        is_text = mime.startswith("text/") or lower.endswith((".txt", ".md", ".csv", ".log", ".json"))
        if is_pdf or is_image:
            body = f"""<!doctype html><html><head><meta charset=utf-8><title>{safe}</title>
            <style>html,body{{margin:0;height:100%;background:#111;color:#eee;font:14px system-ui}}
            header{{padding:8px 12px;background:#1b1b1b;display:flex;justify-content:space-between}}
            a{{color:#9fdfc2}}iframe,img{{width:100%;height:calc(100vh - 42px);border:0}}
            img{{object-fit:contain;height:auto;max-height:calc(100vh - 42px);display:block;margin:0 auto}}</style></head>
            <body><header><strong>{safe}</strong><a href="{urls['download_url']}">Download</a></header>
            {"<iframe src='" + urls['raw_url'] + "'></iframe>" if is_pdf else "<img src='" + urls['raw_url'] + "'/>"}
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
                <header>{safe} · <a href="{urls['download_url']}">Download</a></header><pre>{esc}</pre>"""
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
        """Return URL to the MarshalOSS web file browser."""
        c = active_or(case_id)
        url = f"{settings.public_base.rstrip('/')}/browser?case_id={c.id}"
        if path:
            url += f"&path={encode_rel_path(path)}"
        return {"ok": True, "browser_url": url, "case": c.__dict__}

    return mcp
