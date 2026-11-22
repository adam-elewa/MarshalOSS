"""Escalate poor Docling ASR to OpenAI gpt-4o-transcribe (ffmpeg preprocess)."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any, Callable

from .config import Settings
from .media_ffmpeg import (
    AUDIO_VIDEO_EXTS,
    ffmpeg_wav,
    media_duration_sec,
)

LogFn = Callable[[str], None]

# Stay under typical API upload limits; chunk long media.
_CHUNK_SEC = 600.0  # 10 minutes
_MAX_UPLOAD_BYTES = 24 * 1024 * 1024


def resolve_asr_client(settings: Settings) -> str | None:
    """Return OpenAI API key if ASR escalate can run."""
    if not settings.asr_escalate:
        return None
    return (
        settings.openai_api_key
        or os.environ.get("OPENAI_API_KEY")
        or os.environ.get("MARSHALOSS_OPENAI_API_KEY")
    )


def _transcribe_file(client: Any, model: str, audio_path: Path, *, prompt: str | None) -> str:
    kwargs: dict[str, Any] = {
        "model": model,
        "file": audio_path.open("rb"),
        "response_format": "text",
    }
    if prompt:
        kwargs["prompt"] = prompt
    try:
        result = client.audio.transcriptions.create(**kwargs)
    finally:
        kwargs["file"].close()
    if isinstance(result, str):
        return result.strip()
    text = getattr(result, "text", None)
    return (text or str(result)).strip()


def ai_transcribe_file(
    path: Path,
    settings: Settings,
    *,
    bates: str | None = None,
    duration_sec: float | None = None,
    log: LogFn | None = None,
) -> list[dict[str, Any]]:
    """
    ffmpeg → 16 kHz mono → OpenAI gpt-4o-transcribe (chunked if long/large).
    Returns Marshal-style chunks (plain text segments).
    Works for Docling-native and ffmpeg-bridge A/V formats.
    """
    _log = log or (lambda _m: None)
    key = resolve_asr_client(settings)
    if not key:
        raise RuntimeError("OPENAI_API_KEY required for ASR escalate")
    if path.suffix.lower() not in AUDIO_VIDEO_EXTS:
        raise ValueError(f"not a media file: {path}")

    from openai import OpenAI

    client = OpenAI(api_key=key)
    model = settings.asr_escalate_model
    prompt = settings.asr_escalate_prompt or None
    dur = duration_sec if duration_sec is not None else media_duration_sec(path)

    texts: list[str] = []
    with tempfile.TemporaryDirectory(prefix="marshaloss-asr-") as td:
        tmp = Path(td)
        if dur and dur > _CHUNK_SEC:
            start = 0.0
            idx = 0
            while start < dur - 0.5:
                chunk_path = tmp / f"chunk_{idx:04d}.wav"
                span = min(_CHUNK_SEC, dur - start)
                _log(f"[yellow]asr[/yellow] chunk {idx} @ {start:.0f}s ({span:.0f}s)")
                ffmpeg_wav(path, chunk_path, start_sec=start, duration_sec=span)
                if chunk_path.stat().st_size > _MAX_UPLOAD_BYTES:
                    span = min(180.0, span)
                    ffmpeg_wav(path, chunk_path, start_sec=start, duration_sec=span)
                text = _transcribe_file(client, model, chunk_path, prompt=prompt)
                if text:
                    texts.append(text)
                start += span
                idx += 1
                if idx > settings.asr_escalate_max_chunks:
                    _log(f"[yellow]asr[/yellow] hit max chunks ({settings.asr_escalate_max_chunks})")
                    break
        else:
            wav = tmp / "full.wav"
            ffmpeg_wav(path, wav)
            if wav.stat().st_size > _MAX_UPLOAD_BYTES:
                d = dur or 1800.0
                start = 0.0
                idx = 0
                while start < d - 0.5 and idx < settings.asr_escalate_max_chunks:
                    chunk_path = tmp / f"part_{idx:04d}.wav"
                    span = 180.0
                    ffmpeg_wav(path, chunk_path, start_sec=start, duration_sec=span)
                    text = _transcribe_file(client, model, chunk_path, prompt=prompt)
                    if text:
                        texts.append(text)
                    start += span
                    idx += 1
            else:
                text = _transcribe_file(client, model, wav, prompt=prompt)
                if text:
                    texts.append(text)

    joined = "\n\n".join(t for t in texts if t).strip()
    if not joined:
        return []
    return [
        {
            "text": joined,
            "page_start": None,
            "page_end": None,
            "heading": "AI transcript",
            "bates": bates,
            "meta": {"source": "gpt-4o-transcribe", "chunks": len(texts)},
        }
    ]


# Back-compat aliases used by ingest imports
_ffmpeg_wav = ffmpeg_wav
