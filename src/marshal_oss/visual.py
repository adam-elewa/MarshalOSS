"""
Late-pipeline visual index: AI captions for stills + sampled video frames.

Adds searchable text *alongside* OCR/ASR (does not replace them).
Uses the same vision providers as OCR escalate; frames via ffmpeg.
"""

from __future__ import annotations

import base64
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Callable

from .config import Settings
from .escalate import render_image_file, resolve_ai_ocr_client
from .media_ffmpeg import media_duration_sec
from .quality import join_chunk_text

LogFn = Callable[[str], None]

IMAGE_EXTS = frozenset({".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"})
VIDEO_EXTS = frozenset(
    {
        ".mp4",
        ".mov",
        ".avi",
        ".mkv",
        ".webm",
        ".wmv",
        ".flv",
        ".asf",
        ".ts",
        ".m4v",
        ".mpeg",
        ".mpg",
        ".3gp",
        ".dav",
        ".vob",
        ".h264",
        ".m2ts",
        ".mts",
        ".mxf",
    }
)

_VISUAL_PROMPT = """You are indexing legal-discovery media for search.
Describe what is visibly happening in this image for an attorney who cannot see it.
Include: setting/location cues, people (count, clothing, positions, apparent actions),
vehicles, weapons or objects of interest, text/signage if readable, time-of-day/lighting,
and anything relevant to use-of-force, identity, or scene reconstruction.
Be concrete and factual. Do not invent details you cannot see.
2–6 short sentences. Plain text only."""


def is_image_path(path: Path) -> bool:
    return path.suffix.lower() in IMAGE_EXTS


def is_video_path(path: Path) -> bool:
    return path.suffix.lower() in VIDEO_EXTS


def _b64_png(png: bytes) -> str:
    return base64.standard_b64encode(png).decode("ascii")


def _vision_describe(settings: Settings, png: bytes) -> str:
    client = resolve_ai_ocr_client(settings)
    if client is None:
        raise RuntimeError("no OPENAI/ANTHROPIC key for visual analyze")
    provider, model, key = client
    data_url = f"data:image/png;base64,{_b64_png(png)}"

    if provider == "openai":
        from openai import OpenAI

        resp = OpenAI(api_key=key).chat.completions.create(
            model=model,
            temperature=0,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": _VISUAL_PROMPT},
                        {"type": "image_url", "image_url": {"url": data_url}},
                    ],
                }
            ],
            max_tokens=600,
        )
        return (resp.choices[0].message.content or "").strip()

    from anthropic import Anthropic

    resp = Anthropic(api_key=key).messages.create(
        model=model,
        max_tokens=600,
        temperature=0,
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": _b64_png(png)}},
                    {"type": "text", "text": _VISUAL_PROMPT},
                ],
            }
        ],
    )
    parts = []
    for block in resp.content:
        t = getattr(block, "text", None)
        if t:
            parts.append(t)
    return "\n".join(parts).strip()


def sample_video_frames(
    path: Path,
    *,
    interval_sec: float,
    max_frames: int,
    max_width: int = 1280,
) -> list[tuple[float, bytes]]:
    """
    Extract up to ``max_frames`` PNGs at ``interval_sec`` spacing via ffmpeg.
    Returns list of (timestamp_sec, png_bytes).
    """
    if not shutil.which("ffmpeg"):
        raise RuntimeError("ffmpeg required for video frame sampling")
    interval = max(float(interval_sec), 0.5)
    dur = media_duration_sec(path)
    # fps = 1/interval
    frames: list[tuple[float, bytes]] = []
    with tempfile.TemporaryDirectory(prefix="marshaloss-frames-") as td:
        out_pat = Path(td) / "f_%05d.png"
        vf = f"fps=1/{interval:.4f},scale='min({max_width},iw)':-2"
        cmd = [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(path),
            "-vf",
            vf,
            "-frames:v",
            str(max_frames),
            str(out_pat),
        ]
        subprocess.check_call(cmd, timeout=3600)
        paths = sorted(Path(td).glob("f_*.png"))
        for i, fp in enumerate(paths[:max_frames]):
            t = i * interval
            if dur is not None and t > dur + 0.5:
                break
            frames.append((t, fp.read_bytes()))
    return frames


def _chunk(
    text: str,
    *,
    bates: str | None,
    heading: str,
    meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "text": text.strip(),
        "page_start": None,
        "page_end": None,
        "heading": heading,
        "bates": bates,
        "meta": meta or {"source": "visual"},
    }


def should_visual_image(path: Path, existing_chunks: list[dict[str, Any]], settings: Settings) -> bool:
    if not settings.visual_analyze or not settings.visual_analyze_images:
        return False
    if not is_image_path(path):
        return False
    # Skip text-heavy scans that already OCR'd well
    existing = join_chunk_text(existing_chunks)
    if len(existing) >= settings.visual_skip_if_ocr_chars:
        return False
    return resolve_ai_ocr_client(settings) is not None


def should_visual_video(path: Path, settings: Settings) -> bool:
    if not settings.visual_analyze or not settings.visual_analyze_video:
        return False
    if not is_video_path(path):
        return False
    return resolve_ai_ocr_client(settings) is not None


def visual_analyze_file(
    path: Path,
    settings: Settings,
    *,
    existing_chunks: list[dict[str, Any]] | None = None,
    bates: str | None = None,
    log: LogFn | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """
    Return extra chunks + meta for visual index (images and/or video frames).
    Safe to call at end of ingest; empty if disabled / no key / wrong type.
    """
    _log = log or (lambda _m: None)
    existing_chunks = existing_chunks or []
    chunks: list[dict[str, Any]] = []
    meta: dict[str, Any] = {"visual": False}

    if should_visual_image(path, existing_chunks, settings):
        _log(f"[cyan]visual[/cyan] image {path.name}")
        try:
            rendered = render_image_file(path)
            png = rendered[0][1]
            text = _vision_describe(settings, png)
            if text:
                chunks.append(
                    _chunk(
                        text,
                        bates=bates,
                        heading="Visual description",
                        meta={"source": "visual", "kind": "image"},
                    )
                )
                meta.update({"visual": True, "visual_kind": "image", "visual_frames": 1})
        except Exception as e:
            meta["visual_error"] = str(e)[:500]
            _log(f"[yellow]visual image failed[/yellow] {path.name}: {e}")

    elif should_visual_video(path, settings):
        interval = settings.visual_frame_interval_sec
        max_frames = settings.visual_max_frames
        _log(
            f"[cyan]visual[/cyan] video {path.name} "
            f"(every {interval:g}s, max {max_frames} frames)"
        )
        try:
            frames = sample_video_frames(
                path,
                interval_sec=interval,
                max_frames=max_frames,
            )
            described = 0
            for t, png in frames:
                text = _vision_describe(settings, png)
                if not text:
                    continue
                mm = int(t // 60)
                ss = int(t % 60)
                chunks.append(
                    _chunk(
                        text,
                        bates=bates,
                        heading=f"Visual @{mm}:{ss:02d}",
                        meta={
                            "source": "visual",
                            "kind": "video_frame",
                            "t_sec": round(t, 2),
                        },
                    )
                )
                described += 1
            meta.update(
                {
                    "visual": True,
                    "visual_kind": "video",
                    "visual_frames": described,
                    "visual_interval_sec": interval,
                }
            )
            _log(f"[green]visual ok[/green] {path.name}: {described} frame caption(s)")
        except Exception as e:
            meta["visual_error"] = str(e)[:500]
            _log(f"[yellow]visual video failed[/yellow] {path.name}: {e}")

    return chunks, meta
