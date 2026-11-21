"""AI vision OCR escalation for low-quality Docling extractions."""

from __future__ import annotations

import base64
import io
import os
import re
from pathlib import Path
from typing import Any, Callable

from .config import Settings

LogFn = Callable[[str], None]

_OCR_PROMPT = """You are an expert legal-discovery OCR engine.
Transcribe ALL readable text from this page image faithfully.
Rules:
- Preserve reading order and paragraph breaks.
- Keep names, dates, Bates-like numbers, and stamps exactly.
- Do not invent text you cannot see.
- If a region is illegible, write [illegible].
- Output plain text only (no markdown fences, no commentary).
"""


def _b64_png(png_bytes: bytes) -> str:
    return base64.standard_b64encode(png_bytes).decode("ascii")


def render_pdf_pages(
    path: Path,
    pages: list[int] | None,
    *,
    max_pages: int,
    scale: float = 2.0,
) -> list[tuple[int, bytes]]:
    """Render PDF pages to PNG via pypdfium2. page numbers are 1-based."""
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(str(path))
    n = len(pdf)
    if pages:
        wanted = [p for p in pages if 1 <= p <= n]
    else:
        wanted = list(range(1, n + 1))
    wanted = wanted[:max_pages]
    out: list[tuple[int, bytes]] = []
    for pno in wanted:
        page = pdf[pno - 1]
        bitmap = page.render(scale=scale)
        pil = bitmap.to_pil()
        buf = io.BytesIO()
        pil.save(buf, format="PNG")
        out.append((pno, buf.getvalue()))
    return out


def render_image_file(path: Path) -> list[tuple[int, bytes]]:
    from PIL import Image

    img = Image.open(path)
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return [(1, buf.getvalue())]


def pages_to_ocr(
    path: Path,
    *,
    bad_pages: list[int],
    max_pages: int,
    whole_doc: bool,
) -> list[tuple[int, bytes]]:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        pages = None if whole_doc or not bad_pages else bad_pages
        return render_pdf_pages(path, pages, max_pages=max_pages)
    if suffix in {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"}:
        return render_image_file(path)
    return []


def _openai_ocr_page(api_key: str, model: str, png: bytes) -> str:
    from openai import OpenAI

    client = OpenAI(api_key=api_key)
    data_url = f"data:image/png;base64,{_b64_png(png)}"
    resp = client.chat.completions.create(
        model=model,
        temperature=0,
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": _OCR_PROMPT},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            }
        ],
        max_tokens=4096,
    )
    return (resp.choices[0].message.content or "").strip()


def _anthropic_ocr_page(api_key: str, model: str, png: bytes) -> str:
    import anthropic

    client = anthropic.Anthropic(api_key=api_key)
    resp = client.messages.create(
        model=model,
        max_tokens=4096,
        temperature=0,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/png",
                            "data": _b64_png(png),
                        },
                    },
                    {"type": "text", "text": _OCR_PROMPT},
                ],
            }
        ],
    )
    parts = []
    for block in resp.content:
        if getattr(block, "type", None) == "text":
            parts.append(block.text)
    return "\n".join(parts).strip()


def resolve_ai_ocr_client(settings: Settings) -> tuple[str, str, str] | None:
    """Return (provider, model, api_key) or None if unavailable."""
    provider = (settings.ocr_escalate_provider or "auto").lower()
    openai_key = settings.openai_api_key or os.environ.get("OPENAI_API_KEY")
    anthropic_key = settings.anthropic_api_key or os.environ.get("ANTHROPIC_API_KEY")

    if provider in ("auto", "openai") and openai_key:
        return ("openai", settings.ocr_escalate_openai_model, openai_key)
    if provider in ("auto", "anthropic") and anthropic_key:
        return ("anthropic", settings.ocr_escalate_anthropic_model, anthropic_key)
    if provider == "openai" and not openai_key:
        return None
    if provider == "anthropic" and not anthropic_key:
        return None
    return None


def ai_ocr_file(
    path: Path,
    settings: Settings,
    *,
    bad_pages: list[int],
    whole_doc: bool,
    bates: str | None,
    log: LogFn | None = None,
) -> list[dict[str, Any]]:
    """OCR flagged pages via vision LLM; return MarshalOSS-style chunks."""
    client = resolve_ai_ocr_client(settings)
    if not client:
        raise RuntimeError(
            "AI OCR escalation enabled but no API key "
            "(set OPENAI_API_KEY or ANTHROPIC_API_KEY / MARSHALOSS_* equivalents)"
        )
    provider, model, api_key = client
    _log = log or (lambda _m: None)

    images = pages_to_ocr(
        path,
        bad_pages=bad_pages,
        max_pages=settings.ocr_escalate_max_pages,
        whole_doc=whole_doc,
    )
    if not images:
        raise RuntimeError(f"cannot render pages for AI OCR: {path.suffix}")

    chunks: list[dict[str, Any]] = []
    for page_no, png in images:
        _log(f"[magenta]ai-ocr[/magenta] {path.name} p{page_no} via {provider}/{model}")
        if provider == "openai":
            text = _openai_ocr_page(api_key, model, png)
        else:
            text = _anthropic_ocr_page(api_key, model, png)
        text = _clean_ocr_text(text)
        if not text:
            continue
        chunks.append(
            {
                "text": text,
                "page_start": page_no,
                "page_end": page_no,
                "heading": f"AI OCR p{page_no}",
                "bates": bates,
            }
        )
    return chunks


_FENCE_RE = re.compile(r"^```(?:\w+)?\s*|\s*```$", re.MULTILINE)


def _clean_ocr_text(text: str) -> str:
    text = (text or "").strip()
    text = _FENCE_RE.sub("", text).strip()
    return text
