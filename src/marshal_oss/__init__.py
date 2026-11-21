"""MarshalOSS: Docling ingest + hybrid search + MCP for Claude/Perplexity."""

__all__ = ["main"]


def __getattr__(name: str):
    if name == "main":
        from .cli import main

        return main
    raise AttributeError(name)
