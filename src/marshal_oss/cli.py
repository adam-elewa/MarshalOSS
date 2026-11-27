from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

import typer
import uvicorn
from rich.console import Console

from .config import get_settings
from .db import Database
from .embed import Embedder
from .ingest import ingest_case
from .manifest import prepare_production
from .server import build_mcp_server, create_app

app = typer.Typer(add_completion=False, no_args_is_help=True)
console = Console()


def _load_api_keys() -> None:
    """Pull API keys from ~/.marshal/env.sh when unset."""
    keys = ("VOYAGE_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY")
    missing = [k for k in keys if not os.environ.get(k)]
    if not missing:
        return
    env_sh = Path.home() / ".marshal" / "env.sh"
    if not env_sh.exists():
        return
    import subprocess

    try:
        export = " ".join(f'printf "%s\\n" "{k}=${k}"' for k in missing)
        out = subprocess.check_output(
            ["bash", "-lc", f'source "{env_sh}" >/dev/null 2>&1; {export}'],
            text=True,
        )
        for line in out.splitlines():
            if "=" not in line:
                continue
            k, _, v = line.partition("=")
            if k in missing and v.strip():
                os.environ[k] = v.strip()
    except Exception:
        pass


@app.command("prepare")
def prepare_cmd(
    files_root: Path = typer.Argument(..., exists=True, file_okay=False, dir_okay=True),
    slug: str = typer.Option(..., help="Case slug, e.g. my-case"),
    name: Optional[str] = typer.Option(None, help="Display name"),
    no_expand: bool = typer.Option(False, help="Skip zip expand (hash only)"),
) -> None:
    """Unzip production archives, then write a hashed file-by-file manifest for UX."""
    settings = get_settings()
    db = Database(settings.database_path)
    display = name or slug.replace("-", " ").title()
    console.print(f"DB: {settings.database_path}")
    console.print(
        f"ZIP expand: {settings.expand_zips and not no_expand} "
        f"(depth={settings.expand_zip_max_depth})"
    )
    result = prepare_production(
        files_root,
        settings,
        db=db,
        slug=slug,
        display_name=display,
        expand=not no_expand,
        log=lambda m: console.print(m),
    )
    console.print(
        {
            "slug": result.slug,
            "zip": result.zip_stats,
            "summary": {
                k: result.summary[k]
                for k in (
                    "file_count",
                    "total_bytes",
                    "ingestible_count",
                    "unique_sha256",
                    "by_kind",
                )
                if k in result.summary
            },
            "jsonl": result.jsonl_path,
            "summary_json": result.summary_path,
        }
    )


@app.command("ingest")
def ingest_cmd(
    files_root: Path = typer.Argument(..., exists=True, file_okay=False, dir_okay=True),
    slug: str = typer.Option(..., help="Case slug, e.g. my-case"),
    name: Optional[str] = typer.Option(None, help="Display name"),
    limit: Optional[int] = typer.Option(None, help="Max files to ingest (smoke test)"),
    workers: Optional[int] = typer.Option(
        None,
        "--workers",
        "-j",
        help="Parallel file workers (default: MARSHALOSS_INGEST_WORKERS or 4; 1=serial)",
    ),
) -> None:
    """Prepare (unzip+manifest) then Docling → chunks → embeddings → SQLite."""
    _load_api_keys()
    settings = get_settings()
    db = Database(settings.database_path)
    embedder = Embedder(settings)
    display = name or slug.replace("-", " ").title()
    n_workers = workers if workers is not None else settings.ingest_workers
    console.print(f"DB: {settings.database_path}")
    console.print(f"Embed: {embedder.backend} / {embedder.model_id}")
    console.print(f"Ingest workers: {n_workers}")
    console.print(
        f"AI OCR escalate: {settings.ocr_escalate} "
        f"(provider={settings.ocr_escalate_provider})"
    )
    console.print(
        f"Transcription: provider={settings.asr_provider} model={settings.asr_model} "
        f"hq={settings.asr_hq} (local escalate={settings.asr_escalate})"
    )
    console.print(
        f"ZIP expand: {settings.expand_zips} "
        f"(depth={settings.expand_zip_max_depth})"
    )
    console.print(
        f"Visual analyze: {settings.visual_analyze} "
        f"(images={settings.visual_analyze_images}, "
        f"video every {settings.visual_frame_interval_sec:g}s, "
        f"max_frames={settings.visual_max_frames})"
    )
    console.print(
        f"Entity/tag extract: {settings.entity_extract} "
        f"(provider={settings.entity_extract_provider}, "
        f"segment={settings.entity_extract_segment_chars}, "
        f"max_chars={settings.entity_extract_max_chars or 'all'})"
    )
    stats = ingest_case(
        db,
        settings,
        embedder,
        slug=slug,
        display_name=display,
        files_root=files_root,
        limit=limit,
        workers=n_workers,
    )
    console.print(stats)


@app.command("serve")
def serve_cmd(
    host: Optional[str] = None,
    port: Optional[int] = None,
) -> None:
    """Run HTTP API + file browser + MCP (streamable HTTP at /mcp)."""
    from contextlib import asynccontextmanager

    from fastapi import FastAPI

    _load_api_keys()
    settings = get_settings()
    if host:
        settings.host = host
    if port:
        settings.port = port
    db = Database(settings.database_path)
    embedder = Embedder(settings)

    mcp = build_mcp_server(settings, db, embedder)
    mcp_http = None
    try:
        from urllib.parse import urlparse

        from mcp.server.transport_security import TransportSecuritySettings

        # Allow Host from PUBLIC_BASE (VPN/tunnel/LAN); DNS-rebinding guard blocks unknown hosts
        parsed = urlparse(settings.public_base)
        pub_host = parsed.hostname or "127.0.0.1"
        pub_port = parsed.port or settings.port
        transport_security = TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=[
                "127.0.0.1",
                "localhost",
                f"127.0.0.1:{settings.port}",
                f"localhost:{settings.port}",
                pub_host,
                f"{pub_host}:{pub_port}",
            ],
            allowed_origins=[
                "*",
                settings.public_base.rstrip("/"),
                f"http://127.0.0.1:{settings.port}",
                f"http://localhost:{settings.port}",
            ],
        )
        # Creates session_manager; route path is /mcp on this sub-app
        mcp_http = mcp.streamable_http_app(transport_security=transport_security)
    except Exception as e:
        console.print(f"[yellow]MCP app build failed:[/yellow] {e}")

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        if mcp_http is not None:
            async with mcp.session_manager.run():
                yield
        else:
            yield

    api = create_app(settings, db, embedder, lifespan=lifespan)
    if mcp_http is not None:
        # Mount at "/" so MCP's own "/mcp" route is served at /mcp (not /mcp/mcp)
        api.mount("/", mcp_http)
        console.print("MCP mounted at /mcp (session lifespan active)")
    else:
        console.print("REST search still available at /api/search")

    console.print(f"Listening on http://{settings.host}:{settings.port}")
    console.print(f"Public base: {settings.public_base}")
    uvicorn.run(api, host=settings.host, port=settings.port, log_level="info")


@app.command("retranscribe")
def retranscribe_cmd(
    slug: Optional[str] = typer.Option(None, help="Case slug (default: active case)"),
    path_contains: Optional[str] = typer.Option(
        None, "--path-contains", help="Only files whose path/name contain this string"
    ),
    bodycam: bool = typer.Option(True, help="Include bodycam/dashcam (default)"),
    limit: Optional[int] = typer.Option(None, help="Max files (smoke test)"),
    no_hq: bool = typer.Option(False, "--no-hq", help="Use single-pass asr_model instead of HQ stack"),
    artifacts: Optional[Path] = typer.Option(
        None, help="Write HQ accepted/rejected JSON under this directory"
    ),
) -> None:
    """Re-run OpenAI ASR on ready media (preserves visual frame captions)."""
    _load_api_keys()
    from .retranscribe import retranscribe_case_media

    settings = get_settings()
    if no_hq:
        settings.asr_hq = False
    else:
        settings.asr_hq = True
    db = Database(settings.database_path)
    embedder = Embedder(settings)
    case = db.get_case_by_slug(slug) if slug else db.get_active_case()
    if not case:
        raise typer.Exit("No case found")
    kinds = {"bodycam", "dashcam"} if bodycam else {"bodycam", "dashcam", "video", "audio"}
    console.print(
        f"Retranscribe {case.slug}: hq={settings.asr_hq} "
        f"path_contains={path_contains!r} limit={limit}"
    )
    stats = retranscribe_case_media(
        db,
        settings,
        embedder,
        case,
        path_contains=path_contains,
        kinds=kinds,
        limit=limit,
        artifact_root=artifacts,
        log=lambda m: console.print(m),
    )
    console.print(stats)


@app.command("search")
def search_cmd(
    query: str,
    mode: str = typer.Option("hybrid"),
    top_k: int = typer.Option(8),
) -> None:
    """CLI search against the active case."""
    _load_api_keys()
    from .search import search_case

    settings = get_settings()
    db = Database(settings.database_path)
    embedder = Embedder(settings)
    case = db.get_active_case()
    if not case:
        raise typer.Exit("No active case")
    hits = search_case(db, settings, embedder, case, query, mode=mode, top_k=top_k)  # type: ignore
    for h in hits:
        console.print(
            f"[bold]{h.get('bates') or h.get('name')}[/bold]  {h.get('rel_path')}\n"
            f"  {h.get('snippet', '')[:200]}\n  {h.get('open_url')}\n"
        )


@app.command("knobs")
def knobs_cmd(
    json_out: bool = typer.Option(False, "--json", help="Machine-readable dump"),
) -> None:
    """Show cost/quality knobs (sources, profile, effective Settings values)."""
    from .knobs import knobs_summary, packaged_knobs_path, user_knobs_path

    settings = get_settings()
    summary = knobs_summary()
    effective = {
        "asr_provider": settings.asr_provider,
        "asr_model": settings.asr_model,
        "asr_hq": settings.asr_hq,
        "ocr_escalate": settings.ocr_escalate,
        "ocr_escalate_provider": settings.ocr_escalate_provider,
        "ocr_escalate_max_pages": settings.ocr_escalate_max_pages,
        "ocr_escalate_min_score": settings.ocr_escalate_min_score,
        "visual_analyze": settings.visual_analyze,
        "visual_frame_interval_sec": settings.visual_frame_interval_sec,
        "visual_max_frames": settings.visual_max_frames,
        "entity_extract": settings.entity_extract,
        "entity_extract_provider": settings.entity_extract_provider,
        "embed_backend": settings.embed_backend,
        "embed_model": settings.embed_model,
        "ingest_workers": settings.ingest_workers,
    }
    payload = {
        **summary,
        "effective": effective,
        "hint": (
            f"Edit {user_knobs_path() or (Path.home() / '.marshaloss' / 'knobs.toml')} "
            f"(copy from {packaged_knobs_path()}) or set MARSHALOSS_* env vars. "
            "Then run: marshal estimate /path/to/case"
        ),
    }
    if json_out:
        import json

        console.print_json(json.dumps(payload, default=str))
        return
    console.print(f"Profile: {summary.get('active_profile')}")
    console.print(f"Sources: {summary.get('sources')}")
    console.print("Effective cost/quality settings:")
    for k, v in effective.items():
        console.print(f"  {k} = {v!r}")
    console.print(f"\n{payload['hint']}")


@app.command("estimate")
def estimate_cmd(
    files_root: Optional[Path] = typer.Argument(
        None, exists=True, file_okay=False, dir_okay=True, help="Case folder to scan"
    ),
    slug: Optional[str] = typer.Option(None, help="Use ingested case from DB instead of walking disk"),
    mode: str = typer.Option(
        "ingest",
        help="ingest = full case (docs/images/A/V: ASR+OCR+visual+entities+embed); retranscribe = ASR-only",
    ),
    path_contains: Optional[str] = typer.Option(
        None, "--path-contains", help="Only paths/names containing this string"
    ),
    media_only: bool = typer.Option(False, help="Only audio/video files"),
    limit: Optional[int] = typer.Option(None, help="Max files (smoke)"),
    no_probe: bool = typer.Option(
        False, "--no-probe", help="Skip ffprobe (durations=0 unless from DB)"
    ),
    json_out: bool = typer.Option(False, "--json", help="JSON instead of table"),
) -> None:
    """Estimate OpenAI / Voyage / vision API spend before ingest or retranscribe."""
    _load_api_keys()
    from .estimate import (
        collect_tallies,
        collect_tallies_from_db,
        estimate_costs,
        format_estimate,
    )

    settings = get_settings()
    mode_norm = mode.strip().lower()
    if mode_norm not in ("ingest", "retranscribe"):
        raise typer.Exit("mode must be ingest or retranscribe")

    if slug:
        db = Database(settings.database_path)
        case = db.get_case_by_slug(slug) or (
            db.get_active_case() if slug == "active" else None
        )
        if not case:
            raise typer.Exit(f"Case not found: {slug}")
        tallies = collect_tallies_from_db(
            db,
            case,
            path_contains=path_contains,
            media_only=media_only or mode_norm == "retranscribe",
            limit=limit,
        )
        root_label = f"db:{case.slug}"
    else:
        if files_root is None:
            raise typer.Exit("Pass a case folder or --slug")
        tallies = collect_tallies(
            files_root,
            settings,
            path_contains=path_contains,
            media_only=media_only or mode_norm == "retranscribe",
            limit=limit,
            probe_duration=not no_probe,
            log=lambda m: console.print(m),
        )
        root_label = str(files_root)

    est = estimate_costs(tallies, settings, mode=mode_norm, root_label=root_label)
    if json_out:
        import json

        console.print_json(json.dumps(est.to_dict(), default=str))
    else:
        console.print(format_estimate(est))


def main() -> None:
    app()


if __name__ == "__main__":
    main()
