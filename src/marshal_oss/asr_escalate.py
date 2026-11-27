"""OpenAI transcription for audio/video (ffmpeg → 16 kHz mono WAV windows).

Primary path when ``asr_provider=openai``; otherwise re-runs poor Docling transcripts.
Diarize models return speaker-labelled lines with times; plain models return text
per upload window.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any, Callable, Iterator

from .config import Settings
from .media_ffmpeg import (
    AUDIO_VIDEO_EXTS,
    ffmpeg_wav,
    media_duration_sec,
)
from .media_info import fmt_clock

LogFn = Callable[[str], None]

# Stay under typical API upload limits; chunk long media.
_CHUNK_SEC = 600.0  # 10 minutes
_SMALL_CHUNK_SEC = 180.0
_MAX_UPLOAD_BYTES = 24 * 1024 * 1024
_PIECE_CHARS = 1800


def _openai_key(settings: Settings) -> str | None:
    return (
        settings.openai_api_key
        or os.environ.get("OPENAI_API_KEY")
        or os.environ.get("MARSHALOSS_OPENAI_API_KEY")
    )


def resolve_asr_client(settings: Settings) -> str | None:
    """OpenAI key if poor local transcripts may be re-run."""
    return _openai_key(settings) if settings.asr_escalate else None


def use_ai_asr(settings: Settings) -> bool:
    """True when every A/V file should go straight to the OpenAI model."""
    return settings.asr_provider == "openai" and _openai_key(settings) is not None


def _audio_windows(
    path: Path,
    tmp: Path,
    dur: float | None,
    max_windows: int,
    log: LogFn,
) -> Iterator[tuple[float, float, Path]]:
    """Yield (start_sec, end_sec, wav_path) upload-sized windows."""
    if not dur or dur <= _CHUNK_SEC:
        wav = tmp / "full.wav"
        ffmpeg_wav(path, wav)
        if wav.stat().st_size <= _MAX_UPLOAD_BYTES:
            yield 0.0, dur or 0.0, wav
            return
        step, total = _SMALL_CHUNK_SEC, dur or 1800.0
    else:
        step, total = _CHUNK_SEC, dur
    start, idx = 0.0, 0
    while start < total - 0.5 and idx < max_windows:
        span = min(step, total - start)
        wav = tmp / f"w_{idx:04d}.wav"
        log(f"[cyan]asr[/cyan] {path.name} window {idx} @ {fmt_clock(start)}")
        ffmpeg_wav(path, wav, start_sec=start, duration_sec=span)
        if wav.stat().st_size > _MAX_UPLOAD_BYTES:
            span = min(_SMALL_CHUNK_SEC, span)
            ffmpeg_wav(path, wav, start_sec=start, duration_sec=span)
        yield start, start + span, wav
        start += span
        idx += 1
    if start < total - 0.5:
        log(f"[yellow]asr[/yellow] {path.name}: stopped at {fmt_clock(start)} (asr_max_windows={max_windows})")


def _transcribe_text(client: Any, model: str, wav: Path, *, prompt: str | None) -> str:
    kwargs: dict[str, Any] = {"model": model, "response_format": "text"}
    if prompt:
        kwargs["prompt"] = prompt
    with wav.open("rb") as f:
        result = client.audio.transcriptions.create(file=f, **kwargs)
    if isinstance(result, str):
        return result.strip()
    return (getattr(result, "text", None) or str(result)).strip()


def _transcribe_diarized(client: Any, model: str, wav: Path) -> list[dict[str, Any]]:
    with wav.open("rb") as f:
        result = client.audio.transcriptions.create(
            model=model,
            file=f,
            response_format="diarized_json",
            chunking_strategy="auto",
        )
    data = result.model_dump() if hasattr(result, "model_dump") else dict(result)
    out = []
    for s in data.get("segments") or []:
        text = (s.get("text") or "").strip()
        if text:
            out.append(
                {
                    "start": float(s["start"]),
                    "end": float(s["end"]),
                    "speaker": s.get("speaker"),
                    "text": text,
                }
            )
    return out


def ai_transcribe_file(
    path: Path,
    settings: Settings,
    *,
    bates: str | None = None,
    duration_sec: float | None = None,
    log: LogFn | None = None,
    artifact_dir: Path | None = None,
) -> list[dict[str, Any]]:
    """Transcribe A/V with ``settings.asr_model``; returns timed transcript chunks.

    When ``settings.asr_hq`` is true, runs the legacy HQ stack
    (diarize → gpt-4o-transcribe + logprobs → whisper verify).
    """
    _log = log or (lambda _m: None)
    key = _openai_key(settings)
    if not key:
        raise RuntimeError("OPENAI_API_KEY required for OpenAI transcription")
    if path.suffix.lower() not in AUDIO_VIDEO_EXTS:
        raise ValueError(f"not a media file: {path}")

    if getattr(settings, "asr_hq", False):
        from .hq_transcribe import ai_transcribe_file_hq

        _log(f"[cyan]asr-hq[/cyan] {path.name}")
        return ai_transcribe_file_hq(
            path, settings, bates=bates, artifact_dir=artifact_dir, log=_log
        )

    from openai import OpenAI

    client = OpenAI(api_key=key)
    model = settings.asr_model
    diarize = "diarize" in model
    dur = duration_sec if duration_sec is not None else media_duration_sec(path)

    windows: list[tuple[float, float, list[dict[str, Any]] | str]] = []
    with tempfile.TemporaryDirectory(prefix="marshal-asr-") as td:
        for start, end, wav in _audio_windows(path, Path(td), dur, settings.asr_max_windows, _log):
            if diarize:
                windows.append((start, end, _transcribe_diarized(client, model, wav)))
            else:
                prompt = settings.asr_escalate_prompt or None
                windows.append((start, end, _transcribe_text(client, model, wav, prompt=prompt)))

    if diarize:
        segments = _offset_segments(windows)
        return _chunk_segments(segments, bates=bates, source=model)

    # Plain models return no line timings, so times are per upload window.
    chunks: list[dict[str, Any]] = []
    for start, end, text in windows:
        text = str(text).strip()
        for i, piece in enumerate(_split_text(text, _PIECE_CHARS)):
            chunks.append(
                {
                    "text": piece,
                    "page_start": None,
                    "page_end": None,
                    "heading": f"AI transcript @{fmt_clock(start)}–{fmt_clock(end)}",
                    "bates": bates,
                    "start_sec": start,
                    "end_sec": end,
                    "segments": [{"start": start, "end": end, "text": text}] if i == 0 else [],
                    "meta": {"source": model},
                }
            )
    return chunks


def _offset_segments(windows: list[tuple[float, float, Any]]) -> list[dict[str, Any]]:
    """Shift window-relative times to file times. Speaker labels are only consistent
    within one upload window, so multi-window files get the part number in the label."""
    multi = len(windows) > 1
    out = []
    for i, (start, _end, segs) in enumerate(windows):
        for s in segs:
            label = s.get("speaker")
            speaker = None
            if label:
                speaker = f"Speaker {label}" + (f", part {i + 1}" if multi else "")
            out.append(
                {
                    "start": start + s["start"],
                    "end": start + s["end"],
                    "speaker": speaker,
                    "text": s["text"],
                }
            )
    return out


def _chunk_segments(
    segments: list[dict[str, Any]],
    *,
    bates: str | None,
    source: str,
    max_chars: int = _PIECE_CHARS,
) -> list[dict[str, Any]]:
    chunks: list[dict[str, Any]] = []
    group: list[dict[str, Any]] = []
    size = 0

    def flush() -> None:
        if not group:
            return
        lines = [f"{s['speaker']}: {s['text']}" if s.get("speaker") else s["text"] for s in group]
        start, end = group[0]["start"], group[-1]["end"]
        chunks.append(
            {
                "text": "\n".join(lines),
                "page_start": None,
                "page_end": None,
                "heading": f"Transcript @{fmt_clock(start)}–{fmt_clock(end)}",
                "bates": bates,
                "start_sec": start,
                "end_sec": end,
                "segments": list(group),
                "meta": {"source": source},
            }
        )

    for s in segments:
        line_len = len(s["text"]) + len(s.get("speaker") or "") + 3
        if group and size + line_len > max_chars:
            flush()
            group, size = [], 0
        group.append(s)
        size += line_len
    flush()
    return chunks


def _split_text(text: str, max_chars: int) -> list[str]:
    pieces: list[str] = []
    while len(text) > max_chars:
        cut = text.rfind(" ", 0, max_chars)
        if cut < max_chars // 3:
            cut = max_chars
        pieces.append(text[:cut].strip())
        text = text[cut:].strip()
    if text:
        pieces.append(text)
    return pieces


# Back-compat alias used by ingest imports
_ffmpeg_wav = ffmpeg_wav
