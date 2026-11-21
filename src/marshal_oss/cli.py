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


@app.command("ingest")
def ingest_cmd(
    files_root: Path = typer.Argument(..., exists=True, file_okay=False, dir_okay=True),
    slug: str = typer.Option(..., help="Case slug, e.g. washington-jersey-city"),
    name: Optional[str] = typer.Option(None, help="Display name"),
    limit: Optional[int] = typer.Option(None, help="Max files to ingest (smoke test)"),
) -> None:
    """Ingest a discovery folder with Docling → chunks → embeddings → SQLite."""
    _load_api_keys()
    settings = get_settings()
    db = Database(settings.database_path)
    embedder = Embedder(settings)
    display = name or slug.replace("-", " ").title()
    console.print(f"DB: {settings.database_path}")
    console.print(f"Embed: {embedder.backend} / {embedder.model_id}")
    console.print(
        f"AI OCR escalate: {settings.ocr_escalate} "
        f"(provider={settings.ocr_escalate_provider})"
    )
    stats = ingest_case(
        db,
        settings,
        embedder,
        slug=slug,
        display_name=display,
        files_root=files_root,
        limit=limit,
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

        # Allow Tailscale / LAN Host headers (DNS-rebinding guard blocks raw IPs by default)
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


def main() -> None:
    app()


if __name__ == "__main__":
    main()
