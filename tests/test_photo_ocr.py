from pathlib import Path

import pytest

from marshal_oss import ingest
from marshal_oss.config import Settings


@pytest.fixture
def no_ai_ocr(monkeypatch):
    calls = []
    monkeypatch.setattr(ingest, "resolve_ai_ocr_client", lambda s: ("openai", "m", "k"))
    monkeypatch.setattr(ingest, "ai_ocr_file", lambda *a, **k: calls.append(a) or [{"text": "ocr"}])
    return calls


def _escalate(path: str, text: str):
    return ingest._maybe_ai_escalate(
        Path(path),
        [{"text": text}] if text else [],
        {"page_count": 1},
        settings=Settings(ocr_escalate=True),
        file_size=2_000_000,
        bates=None,
        log=lambda m: None,
    )


def test_photo_without_text_skips_ai_ocr(no_ai_ocr):
    chunks, meta = _escalate("Car damage photo 1.jpg", "")
    assert no_ai_ocr == []
    assert meta["quality_score"] is None
    assert meta["quality_reasons"] == ["photo_no_text"]


def test_garbled_scan_image_still_escalates(no_ai_ocr):
    garbled = "Th§ d€f€nd@nt w@s ¤bs€rv€d ~~ ¦¦ ±±± " * 20
    chunks, meta = _escalate("scan_page.jpg", garbled)
    assert len(no_ai_ocr) == 1
    assert meta.get("escalated")


def test_empty_pdf_still_escalates(no_ai_ocr):
    chunks, meta = _escalate("report.pdf", "")
    assert len(no_ai_ocr) == 1
