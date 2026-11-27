"""
Pre-run API cost estimator for ingest / retranscribe.

Scans a case tree (or an already-ingested case in SQLite), applies the active
knobs + rough provider list prices from ``knobs.toml`` ``[pricing]``, and
returns a breakdown. Estimates are intentionally conservative ballparks —
not invoices.
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

from .config import Settings
from .knobs import load_estimate_assumptions, load_pricing, knobs_summary
from .media_ffmpeg import AUDIO_VIDEO_EXTS, media_duration_sec

LogFn = Callable[[str], None]

_IMAGE_EXTS = frozenset({".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"})
_VIDEO_EXTS = frozenset(
    {".mp4", ".mov", ".avi", ".mkv", ".webm", ".m4v", ".wmv", ".flv", ".mpeg", ".mpg", ".3gp", ".ts", ".m2ts", ".mts"}
)
_AUDIO_EXTS = AUDIO_VIDEO_EXTS - _VIDEO_EXTS
_DOC_EXTS = frozenset(
    {
        ".pdf",
        ".docx",
        ".doc",
        ".pptx",
        ".ppt",
        ".xlsx",
        ".xls",
        ".csv",
        ".txt",
        ".md",
        ".html",
        ".htm",
        ".xml",
        ".eml",
        ".msg",
    }
)


@dataclass
class FileTally:
    path: str
    category: str  # document | image | audio | video | other
    size_bytes: int = 0
    duration_sec: float = 0.0
    pages_est: int = 0


@dataclass
class CostLine:
    stage: str
    provider: str
    detail: str
    units: float
    unit_label: str
    usd_low: float
    usd_high: float
    enabled: bool = True


@dataclass
class CostEstimate:
    root: str
    mode: str  # ingest | retranscribe
    profile: str
    providers_default: dict[str, str]
    tallies: dict[str, Any]
    lines: list[CostLine] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    knobs_sources: list[str] = field(default_factory=list)

    @property
    def usd_low(self) -> float:
        return round(sum(l.usd_low for l in self.lines if l.enabled), 2)

    @property
    def usd_high(self) -> float:
        return round(sum(l.usd_high for l in self.lines if l.enabled), 2)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["usd_low"] = self.usd_low
        d["usd_high"] = self.usd_high
        return d


def _iter_files(root: Path, allowed: set[str]) -> Iterable[Path]:
    for dirpath, dirnames, filenames in os.walk(root):
        # skip hidden / extract noise lightly
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        for name in filenames:
            if name.startswith("."):
                continue
            p = Path(dirpath) / name
            if p.suffix.lower() in allowed:
                yield p


def _categorize(path: Path) -> str:
    ext = path.suffix.lower()
    if ext in _VIDEO_EXTS:
        return "video"
    if ext in _AUDIO_EXTS or ext in AUDIO_VIDEO_EXTS:
        return "audio"
    if ext in _IMAGE_EXTS:
        return "image"
    if ext in _DOC_EXTS:
        return "document"
    return "other"


def _est_pdf_pages(size_bytes: int, pages_per_mb: float) -> int:
    mb = max(size_bytes, 1) / (1024 * 1024)
    return max(1, int(round(mb * pages_per_mb)))


def collect_tallies(
    root: Path,
    settings: Settings,
    *,
    path_contains: str | None = None,
    media_only: bool = False,
    limit: int | None = None,
    probe_duration: bool = True,
    log: LogFn | None = None,
) -> list[FileTally]:
    _log = log or (lambda _m: None)
    assumptions = load_estimate_assumptions()
    pages_per_mb = assumptions.get("default_pdf_pages_per_mb", 25.0)
    out: list[FileTally] = []
    allowed = settings.allowed_extensions
    if media_only:
        allowed = {e for e in allowed if e in AUDIO_VIDEO_EXTS}

    for path in _iter_files(root, allowed):
        rel = str(path.relative_to(root)) if path.is_relative_to(root) else str(path)
        if path_contains:
            needle = path_contains.lower()
            if needle not in rel.lower() and needle not in path.name.lower():
                continue
        cat = _categorize(path)
        try:
            size = path.stat().st_size
        except OSError:
            size = 0
        dur = 0.0
        pages = 0
        if cat in ("audio", "video") and probe_duration:
            d = media_duration_sec(path)
            dur = float(d or 0.0)
            if not d:
                _log(f"[yellow]no duration[/yellow] {rel}")
        if cat == "document" and path.suffix.lower() == ".pdf":
            pages = _est_pdf_pages(size, pages_per_mb)
        elif cat == "document":
            pages = max(1, _est_pdf_pages(size, pages_per_mb // 2 or 1))
        out.append(
            FileTally(
                path=rel,
                category=cat,
                size_bytes=size,
                duration_sec=dur,
                pages_est=pages,
            )
        )
        if limit is not None and len(out) >= limit:
            break
    return out


def collect_tallies_from_db(
    db: Any,
    case: Any,
    *,
    path_contains: str | None = None,
    media_only: bool = False,
    limit: int | None = None,
) -> list[FileTally]:
    """Use ingested file meta (duration_sec) when available — no ffprobe needed."""
    rows = db.conn.execute(
        "SELECT rel_path, name, mime, size, meta FROM files WHERE case_id=?",
        (case.id,),
    ).fetchall()
    out: list[FileTally] = []
    for r in rows:
        name = r["name"] or ""
        rel = r["rel_path"] or name
        if path_contains:
            needle = path_contains.lower()
            if needle not in rel.lower() and needle not in name.lower():
                continue
        meta: dict[str, Any] = {}
        if r["meta"]:
            try:
                meta = json.loads(r["meta"])
            except json.JSONDecodeError:
                meta = {}
        media = meta.get("media") if isinstance(meta.get("media"), dict) else {}
        # Fake path for categorize
        cat = _categorize(Path(rel))
        if media_only and cat not in ("audio", "video"):
            continue
        dur = float(media.get("duration_sec") or media.get("duration") or 0.0)
        size = int(r["size"] or 0)
        pages = 0
        if cat == "document":
            pages = _est_pdf_pages(size, 25.0)
        out.append(
            FileTally(
                path=rel,
                category=cat,
                size_bytes=size,
                duration_sec=dur,
                pages_est=pages,
            )
        )
        if limit is not None and len(out) >= limit:
            break
    return out


def _vision_provider(settings: Settings) -> str:
    pref = (settings.ocr_escalate_provider or "auto").lower()
    if pref == "anthropic":
        return "anthropic"
    if pref == "openai":
        return "openai"
    # auto
    if settings.openai_api_key or os.environ.get("OPENAI_API_KEY") or os.environ.get(
        "MARSHALOSS_OPENAI_API_KEY"
    ):
        return "openai"
    if settings.anthropic_api_key or os.environ.get("ANTHROPIC_API_KEY"):
        return "anthropic"
    return "openai (if keyed)"


def _entity_provider(settings: Settings) -> str:
    pref = (settings.entity_extract_provider or "auto").lower()
    if pref == "anthropic":
        return "anthropic"
    if pref == "openai":
        return "openai"
    return _vision_provider(settings)


def estimate_costs(
    tallies: list[FileTally],
    settings: Settings,
    *,
    mode: str = "ingest",
    root_label: str = "",
) -> CostEstimate:
    pricing = load_pricing()
    assumptions = load_estimate_assumptions()
    summary = knobs_summary()

    asr_rate = pricing.get("openai_transcribe_usd_per_min", 0.006)
    vision_oai = pricing.get("openai_vision_usd_per_image", 0.002)
    vision_ant = pricing.get("anthropic_haiku_vision_usd_per_image", 0.002)
    voyage_rate = pricing.get("voyage_law2_usd_per_mTok", 0.12)
    mini_in = pricing.get("openai_gpt4o_mini_input_usd_per_mTok", 0.15)
    mini_out = pricing.get("openai_gpt4o_mini_output_usd_per_mTok", 0.60)
    haiku_in = pricing.get("anthropic_haiku_input_usd_per_mTok", 1.0)
    haiku_out = pricing.get("anthropic_haiku_output_usd_per_mTok", 5.0)

    speech_frac = assumptions.get("speech_fraction", 0.20)
    verify_frac = assumptions.get("hq_verify_fraction", 0.15)
    ocr_frac = assumptions.get("ocr_escalate_page_fraction", 0.12)
    chars_per_tok = assumptions.get("chars_per_embed_token", 4.0) or 4.0
    text_chars_per_mb = assumptions.get("text_chars_per_mb", 50000.0)
    entity_out_tok = assumptions.get("entity_output_tokens_per_file", 400.0)

    n_doc = sum(1 for t in tallies if t.category == "document")
    n_img = sum(1 for t in tallies if t.category == "image")
    n_aud = sum(1 for t in tallies if t.category == "audio")
    n_vid = sum(1 for t in tallies if t.category == "video")
    media_sec = sum(t.duration_sec for t in tallies if t.category in ("audio", "video"))
    media_min = media_sec / 60.0
    pages = sum(t.pages_est for t in tallies if t.category == "document")
    bytes_non_media = sum(
        t.size_bytes for t in tallies if t.category in ("document", "image", "other")
    )

    # Video frame counts under current knobs
    frame_total = 0
    if settings.visual_analyze and settings.visual_analyze_video:
        interval = max(float(settings.visual_frame_interval_sec or 5.0), 0.5)
        cap = int(settings.visual_max_frames or 0)
        for t in tallies:
            if t.category != "video" or t.duration_sec <= 0:
                continue
            n = int(t.duration_sec // interval) + (1 if t.duration_sec > 0 else 0)
            if cap > 0:
                n = min(n, cap)
            frame_total += n

    vision_prov = _vision_provider(settings)
    vision_unit = vision_oai if vision_prov.startswith("openai") else vision_ant
    entity_prov = _entity_provider(settings)

    lines: list[CostLine] = []
    notes: list[str] = [
        "Ballpark only — actual OpenAI/Voyage bills vary with speech density, OCR fail rate, and tokens.",
        "Docling / ffmpeg local work is $0 API (dominates wall-clock, not this estimate).",
    ]

    # --- ASR ---
    asr_on = settings.asr_provider == "openai" and mode in ("ingest", "retranscribe")
    if media_min > 0:
        if settings.asr_hq and asr_on:
            # diarize full + retranscribe speech + verify fraction of speech
            diar = media_min * asr_rate
            speech_min = media_min * speech_frac
            second = speech_min * asr_rate
            verify = speech_min * verify_frac * asr_rate
            low = diar + second  # sparse verify
            high = diar + second + verify + (media_min * asr_rate * 0.3)  # denser talk pad
            lines.append(
                CostLine(
                    stage="asr_hq",
                    provider="openai",
                    detail=(
                        f"diarize {media_min:.1f} min + gpt-4o-transcribe "
                        f"~{speech_frac:.0%} speech + whisper verify"
                    ),
                    units=media_min,
                    unit_label="audio-minutes",
                    usd_low=round(low, 2),
                    usd_high=round(high, 2),
                    enabled=True,
                )
            )
        elif asr_on:
            cost = media_min * asr_rate
            lines.append(
                CostLine(
                    stage="asr_diarize",
                    provider="openai",
                    detail=f"single-pass {settings.asr_model}",
                    units=media_min,
                    unit_label="audio-minutes",
                    usd_low=round(cost, 2),
                    usd_high=round(cost * 1.15, 2),
                    enabled=True,
                )
            )
        else:
            lines.append(
                CostLine(
                    stage="asr_local",
                    provider="local",
                    detail="Docling Whisper (no OpenAI ASR)",
                    units=media_min,
                    unit_label="audio-minutes",
                    usd_low=0.0,
                    usd_high=0.0,
                    enabled=True,
                )
            )
    elif mode == "retranscribe":
        notes.append("No media duration found — run with files on disk or after prepare/ingest.")

    if mode == "retranscribe":
        # skip non-ASR stages
        est = CostEstimate(
            root=root_label,
            mode=mode,
            profile=str(summary.get("active_profile") or "balanced"),
            providers_default={
                "asr": "openai" if settings.asr_provider == "openai" else "local",
                "ocr_visual_entities": vision_prov,
                "embed": settings.embed_backend,
            },
            tallies={
                "files": len(tallies),
                "documents": n_doc,
                "images": n_img,
                "audio": n_aud,
                "video": n_vid,
                "media_minutes": round(media_min, 2),
                "pages_est": pages,
                "video_frames_est": frame_total,
            },
            lines=lines,
            notes=notes
            + [
                "Retranscribe mode: ASR only (visual captions preserved; no embed/OCR/entity redo)."
            ],
            knobs_sources=list(summary.get("sources") or []),
        )
        return est

    # --- OCR escalate ---
    if settings.ocr_escalate and pages > 0:
        esc_pages = min(
            pages * ocr_frac,
            float(n_doc * settings.ocr_escalate_max_pages),
        )
        # low/high around assumed fail rate
        low_p = esc_pages * 0.5
        high_p = min(pages * (ocr_frac * 2), float(n_doc * settings.ocr_escalate_max_pages))
        lines.append(
            CostLine(
                stage="ocr_escalate",
                provider=vision_prov,
                detail=f"~{ocr_frac:.0%} of ~{pages} pages fail Docling → vision OCR",
                units=round(esc_pages, 1),
                unit_label="pages",
                usd_low=round(low_p * vision_unit, 2),
                usd_high=round(high_p * vision_unit, 2),
                enabled=True,
            )
        )

    # --- Visual ---
    if settings.visual_analyze:
        img_n = n_img if settings.visual_analyze_images else 0
        # assume ~70% of images need visual (rest skip via OCR chars)
        img_desc = img_n * 0.7
        frames = frame_total if settings.visual_analyze_video else 0
        total_imgs = img_desc + frames
        if total_imgs > 0:
            lines.append(
                CostLine(
                    stage="visual",
                    provider=vision_prov,
                    detail=f"~{img_desc:.0f} stills + {frames} video frames",
                    units=round(total_imgs, 1),
                    unit_label="images",
                    usd_low=round(total_imgs * vision_unit * 0.7, 2),
                    usd_high=round(total_imgs * vision_unit * 1.3, 2),
                    enabled=True,
                )
            )

    # --- Entities ---
    if settings.entity_extract:
        text_files = n_doc + n_img + n_aud + n_vid  # media also get tags from transcripts
        mb = bytes_non_media / (1024 * 1024)
        # plus rough transcript chars from media
        media_chars = media_min * 60 * 12  # ~12 chars/sec talking average over full timeline pad
        in_tok = (mb * text_chars_per_mb + media_chars) / chars_per_tok
        out_tok = text_files * entity_out_tok
        if entity_prov.startswith("anthropic"):
            usd = (in_tok / 1e6) * haiku_in + (out_tok / 1e6) * haiku_out
        else:
            usd = (in_tok / 1e6) * mini_in + (out_tok / 1e6) * mini_out
        lines.append(
            CostLine(
                stage="entities",
                provider=entity_prov,
                detail=f"~{text_files} files with text/transcripts",
                units=round(in_tok / 1000, 1),
                unit_label="k-input-tokens",
                usd_low=round(usd * 0.6, 2),
                usd_high=round(usd * 1.5, 2),
                enabled=True,
            )
        )

    # --- Embeddings ---
    if settings.embed_backend == "voyage":
        mb = bytes_non_media / (1024 * 1024)
        media_chars = media_min * 60 * 8
        tok = (mb * text_chars_per_mb + media_chars) / chars_per_tok
        usd = (tok / 1e6) * voyage_rate
        lines.append(
            CostLine(
                stage="embed",
                provider="voyage",
                detail=f"{settings.embed_model} (~{tok/1000:.0f}k tokens est.)",
                units=round(tok / 1000, 1),
                unit_label="k-tokens",
                usd_low=round(usd * 0.5, 2),
                usd_high=round(usd * 1.5, 2),
                enabled=True,
            )
        )
        notes.append(
            "Voyage law-2 includes a free-token allotment per account; estimate ignores free tier."
        )
    else:
        lines.append(
            CostLine(
                stage="embed",
                provider="local-hash",
                detail="hash backend (no Voyage)",
                units=0,
                unit_label="tokens",
                usd_low=0.0,
                usd_high=0.0,
                enabled=True,
            )
        )

    notes.append(
        "Default stack: OpenAI for ASR + (auto) OCR/visual/entities; Voyage for embeddings. "
        "Anthropic is optional for non-ASR stages only."
    )

    return CostEstimate(
        root=root_label,
        mode=mode,
        profile=str(summary.get("active_profile") or "balanced"),
        providers_default={
            "asr": "openai" if settings.asr_provider == "openai" else "local",
            "ocr_visual_entities": vision_prov,
            "embed": settings.embed_backend,
        },
        tallies={
            "files": len(tallies),
            "documents": n_doc,
            "images": n_img,
            "audio": n_aud,
            "video": n_vid,
            "media_minutes": round(media_min, 2),
            "pages_est": pages,
            "video_frames_est": frame_total,
        },
        lines=lines,
        notes=notes,
        knobs_sources=list(summary.get("sources") or []),
    )


def format_estimate(est: CostEstimate) -> str:
    scope = (
        "full ingest (ASR + OCR + visual + entities + embed)"
        if est.mode == "ingest"
        else "ASR retranscribe only (docs/visual/entities/embed not re-billed)"
    )
    lines = [
        f"Marshal cost estimate  ({est.mode}) — {scope}",
        f"  root:     {est.root or '(db)'}",
        f"  profile:  {est.profile}",
        f"  providers:{est.providers_default}",
        f"  files:    {est.tallies}",
        "",
        f"  {'stage':16} {'provider':12} {'low':>8} {'high':>8}  detail",
        f"  {'-'*16} {'-'*12} {'-'*8} {'-'*8}  {'-'*40}",
    ]
    for ln in est.lines:
        if not ln.enabled:
            continue
        lines.append(
            f"  {ln.stage:16} {ln.provider:12} ${ln.usd_low:7.2f} ${ln.usd_high:7.2f}  {ln.detail}"
        )
    lines.append("")
    lines.append(f"  TOTAL (ballpark)   ${est.usd_low:.2f} – ${est.usd_high:.2f}")
    if est.notes:
        lines.append("")
        lines.append("  Notes:")
        for n in est.notes:
            lines.append(f"    • {n}")
    if est.knobs_sources:
        lines.append("")
        lines.append("  Knobs from: " + " → ".join(est.knobs_sources))
    return "\n".join(lines)
