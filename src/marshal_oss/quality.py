"""Heuristics + Docling confidence → decide whether to escalate OCR to AI."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any

# Private-use / replacement / box-drawing / OCR-junk heavy ranges
_WEIRD_RE = re.compile(
    r"[\uFFFD\u25A0-\u25FF\u2500-\u257F\u00A6\u00A4\u00A7\u00B1\u25A1□■▪▫◆◇★☆]"
)
_MOJIBAKE_RE = re.compile(
    r"(?:Ã.|Â.|â€™|â€œ|â€\x9d|â€“|â€”|ï¿½|â€¦|\xef\xbf\xbd)"
)
_LETTER_RE = re.compile(r"[A-Za-z]")
_ALNUM_RE = re.compile(r"[A-Za-z0-9]")
# Tokens that look like OCR hash salad (few vowels, mixed case/digits/symbols)
_GARBAGE_TOKEN_RE = re.compile(
    r"^(?:[^\W\d_]{0,2}[\d|_/\\]{2,}[^\W\d_]{0,2}|[bcdfghjklmnpqrstvwxyzBCDFGHJKLMNPQRSTVWXYZ]{5,})$"
)


@dataclass
class QualityReport:
    escalate: bool
    score: float  # 0..1 higher = better
    reasons: list[str] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)
    bad_pages: list[int] = field(default_factory=list)

    def summary(self) -> str:
        if not self.escalate:
            return f"ok score={self.score:.2f}"
        return f"escalate score={self.score:.2f} reasons={','.join(self.reasons)}"


def _safe_grade(val: Any) -> str | None:
    if val is None:
        return None
    name = getattr(val, "value", None) or getattr(val, "name", None) or str(val)
    name = str(name).lower()
    if name in {"poor", "fair", "good", "excellent", "unspecified"}:
        return name
    return name


def extract_docling_confidence(conv_result: Any) -> dict[str, Any]:
    """Pull Docling ConfidenceReport into a plain dict (best-effort)."""
    out: dict[str, Any] = {"pages": {}}
    conf = getattr(conv_result, "confidence", None)
    if conf is None:
        return out

    for key in ("mean_grade", "low_grade", "ocr_score", "parse_score", "layout_score"):
        val = getattr(conf, key, None)
        if key.endswith("_grade"):
            out[key] = _safe_grade(val)
        else:
            try:
                f = float(val)
                out[key] = None if math.isnan(f) else f
            except (TypeError, ValueError):
                out[key] = None

    pages = getattr(conf, "pages", None) or {}
    try:
        items = pages.items() if hasattr(pages, "items") else enumerate(pages)
    except Exception:
        items = []
    for page_no, scores in items:
        try:
            pn = int(page_no)
        except Exception:
            continue
        page_info: dict[str, Any] = {}
        for key in ("mean_grade", "low_grade", "ocr_score", "parse_score", "layout_score"):
            val = getattr(scores, key, None)
            if key.endswith("_grade"):
                page_info[key] = _safe_grade(val)
            else:
                try:
                    f = float(val)
                    page_info[key] = None if math.isnan(f) else f
                except (TypeError, ValueError):
                    page_info[key] = None
        out["pages"][pn] = page_info
    return out


def join_chunk_text(chunks: list[dict[str, Any]]) -> str:
    return "\n\n".join(c.get("text") or "" for c in chunks if c.get("text"))


def score_extraction(
    text: str,
    *,
    file_size: int = 0,
    page_count: int | None = None,
    confidence: dict[str, Any] | None = None,
    min_chars: int = 40,
    min_chars_per_page: float = 35.0,
    min_chars_per_mb: float = 800.0,
    max_weird_ratio: float = 0.04,
    max_mojibake_hits: int = 8,
    max_garbage_token_ratio: float = 0.22,
    min_letter_ratio: float = 0.35,
    escalate_grades: frozenset[str] = frozenset({"poor", "fair"}),
    escalate_ocr_below: float = 0.55,
) -> QualityReport:
    """Return whether extraction looks bad enough to warrant AI OCR."""
    reasons: list[str] = []
    metrics: dict[str, Any] = {}
    bad_pages: list[int] = []
    penalties = 0.0

    text = text or ""
    n = len(text)
    metrics["chars"] = n
    metrics["file_size"] = file_size
    metrics["page_count"] = page_count

    # --- Docling confidence ---
    conf = confidence or {}
    mean_grade = conf.get("mean_grade")
    low_grade = conf.get("low_grade")
    ocr_score = conf.get("ocr_score")
    metrics["docling_mean_grade"] = mean_grade
    metrics["docling_low_grade"] = low_grade
    metrics["docling_ocr_score"] = ocr_score

    if mean_grade in escalate_grades:
        reasons.append(f"docling_mean_grade={mean_grade}")
        penalties += 0.35 if mean_grade == "poor" else 0.2
    if low_grade in escalate_grades:
        reasons.append(f"docling_low_grade={low_grade}")
        penalties += 0.25 if low_grade == "poor" else 0.12
    if isinstance(ocr_score, (int, float)) and ocr_score < escalate_ocr_below:
        reasons.append(f"docling_ocr_score={ocr_score:.2f}")
        penalties += 0.3

    for pn, pscores in (conf.get("pages") or {}).items():
        page_bad = False
        pg = pscores.get("mean_grade") or pscores.get("low_grade")
        if pg in escalate_grades:
            page_bad = True
        pos = pscores.get("ocr_score")
        if isinstance(pos, (int, float)) and pos < escalate_ocr_below:
            page_bad = True
        if page_bad:
            bad_pages.append(int(pn))
    if bad_pages:
        reasons.append(f"bad_pages={len(bad_pages)}")
        penalties += min(0.25, 0.05 * len(bad_pages))

    # --- Empty / sparse text ---
    if n < min_chars:
        reasons.append("near_empty_text")
        penalties += 0.55
    if page_count and page_count > 0:
        cpp = n / page_count
        metrics["chars_per_page"] = round(cpp, 1)
        if cpp < min_chars_per_page:
            reasons.append(f"sparse_chars_per_page={cpp:.1f}")
            penalties += 0.35
    if file_size > 50_000:
        mb = file_size / (1024 * 1024)
        cpm = n / max(mb, 0.01)
        metrics["chars_per_mb"] = round(cpm, 1)
        # Scanned PDFs are large; very little text ⇒ likely OCR failure
        if cpm < min_chars_per_mb and n < max(min_chars * 20, 2000):
            reasons.append(f"sparse_vs_filesize={cpm:.0f}c/MB")
            penalties += 0.3

    if n == 0:
        score = max(0.0, 1.0 - penalties)
        return QualityReport(
            escalate=True,
            score=score,
            reasons=list(dict.fromkeys(reasons)) or ["empty"],
            metrics=metrics,
            bad_pages=sorted(set(bad_pages)),
        )

    # --- Weird symbols / replacement chars ---
    weird = len(_WEIRD_RE.findall(text))
    weird_ratio = weird / n
    metrics["weird_ratio"] = round(weird_ratio, 4)
    if weird_ratio > max_weird_ratio or weird >= 12:
        reasons.append(f"weird_symbols={weird}")
        penalties += 0.35

    mojibake = len(_MOJIBAKE_RE.findall(text))
    metrics["mojibake_hits"] = mojibake
    if mojibake >= max_mojibake_hits:
        reasons.append(f"mojibake={mojibake}")
        penalties += 0.25

    # --- Letter density (legal English/Spanish still letter-heavy) ---
    letters = len(_LETTER_RE.findall(text))
    letter_ratio = letters / n
    metrics["letter_ratio"] = round(letter_ratio, 3)
    # Only flag when we also have some content; pure number tables can be letter-light
    if n > 200 and letter_ratio < min_letter_ratio:
        reasons.append(f"low_letter_ratio={letter_ratio:.2f}")
        penalties += 0.25

    # --- Garbage tokens ---
    tokens = re.findall(r"\S+", text)
    if tokens:
        garbage = sum(1 for t in tokens if _GARBAGE_TOKEN_RE.match(t) or (len(t) > 2 and not _ALNUM_RE.search(t)))
        g_ratio = garbage / len(tokens)
        metrics["garbage_token_ratio"] = round(g_ratio, 3)
        if g_ratio > max_garbage_token_ratio and len(tokens) > 40:
            reasons.append(f"garbage_tokens={g_ratio:.2f}")
            penalties += 0.3

    # --- Control / non-printable ---
    controls = sum(1 for ch in text if ord(ch) < 32 and ch not in "\n\r\t")
    if controls > 5:
        reasons.append(f"control_chars={controls}")
        penalties += 0.2

    score = max(0.0, min(1.0, 1.0 - penalties))
    # Escalate on any strong reason, or weak score
    escalate = bool(reasons) and (score < 0.72 or any(
        r.startswith(("near_empty", "docling_mean_grade=poor", "docling_low_grade=poor", "weird_symbols", "sparse_"))
        for r in reasons
    ) or len(reasons) >= 2)

    # Soft single fair-grade alone without text issues → don't escalate
    if reasons == [f"docling_mean_grade=fair"] or reasons == [f"docling_low_grade=fair"]:
        if letter_ratio >= 0.45 and n >= min_chars * 5:
            escalate = False
            reasons.append("fair_but_text_ok")

    return QualityReport(
        escalate=escalate,
        score=score,
        reasons=list(dict.fromkeys(reasons)),
        metrics=metrics,
        bad_pages=sorted(set(bad_pages)),
    )
