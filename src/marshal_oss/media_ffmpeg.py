"""
ffmpeg/ffprobe helpers for A/V Docling does not take natively.

Docling ASR covers a short native list (wav/mp3/m4a/… + mp4/mov/…). Other
common A/V types are normalized to 16 kHz mono WAV with ffmpeg, then the same
ASR / escalate path is used. No custom demuxers — if ffmpeg cannot probe a
file, convert it upstream and re-ingest.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

# Docling [asr] documented inputs
DOCLING_NATIVE_MEDIA = frozenset(
    {
        ".wav",
        ".mp3",
        ".m4a",
        ".aac",
        ".ogg",
        ".flac",
        ".mp4",
        ".avi",
        ".mov",
        ".mkv",
        ".webm",
    }
)

# Extra A/V types commonly opened by ffmpeg, then bridged to wav
FFMPEG_BRIDGE_MEDIA = frozenset(
    {
        ".wma",
        ".wmv",
        ".flv",
        ".asf",
        ".ts",
        ".m4v",
        ".mpeg",
        ".mpg",
        ".3gp",
        ".caf",
        ".dav",
        ".vob",
        ".h264",
        ".m2ts",
        ".mts",
        ".mxf",
    }
)

AUDIO_VIDEO_EXTS = DOCLING_NATIVE_MEDIA | FFMPEG_BRIDGE_MEDIA


def is_media_path(path: str | Path) -> bool:
    return Path(path).suffix.lower() in AUDIO_VIDEO_EXTS


def is_docling_native_media(path: str | Path) -> bool:
    return Path(path).suffix.lower() in DOCLING_NATIVE_MEDIA


def media_duration_sec(path: Path) -> float | None:
    """Best-effort duration via ffprobe."""
    if not shutil.which("ffprobe"):
        return None
    try:
        out = subprocess.check_output(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            text=True,
            timeout=120,
        ).strip()
        if not out or out.upper() == "N/A":
            return None
        return float(out)
    except Exception:
        return None


def ffprobe_readable(path: Path) -> bool:
    """True when ffprobe can open the file (has streams or duration)."""
    if not shutil.which("ffprobe"):
        return False
    try:
        subprocess.check_output(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "stream=codec_type",
                "-of",
                "csv=p=0",
                str(path),
            ],
            text=True,
            timeout=120,
            stderr=subprocess.DEVNULL,
        )
        return True
    except Exception:
        return False


def ffmpeg_wav(
    src: Path,
    dst: Path,
    *,
    start_sec: float | None = None,
    duration_sec: float | None = None,
) -> None:
    if not shutil.which("ffmpeg"):
        raise RuntimeError("ffmpeg not found on PATH (required for media bridge / ASR)")
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"]
    if start_sec is not None:
        cmd += ["-ss", f"{start_sec:.3f}"]
    cmd += ["-i", str(src)]
    if duration_sec is not None:
        cmd += ["-t", f"{duration_sec:.3f}"]
    cmd += [
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-c:a",
        "pcm_s16le",
        str(dst),
    ]
    subprocess.check_call(cmd, timeout=3600)


@contextmanager
def as_docling_media(path: Path) -> Iterator[tuple[Path, dict]]:
    """
    Yield a path Docling ASR can read.

    Native formats → original path.
    Bridge formats → temp 16 kHz WAV via ffmpeg (deleted on exit).
    """
    meta: dict = {"media_bridge": False, "original_suffix": path.suffix.lower()}
    if is_docling_native_media(path):
        yield path, meta
        return

    if not ffprobe_readable(path):
        raise RuntimeError(f"ffprobe cannot read media: {path.name}")

    tmp = tempfile.TemporaryDirectory(prefix="marshaloss-media-")
    try:
        wav = Path(tmp.name) / "bridge.wav"
        ffmpeg_wav(path, wav)
        meta["media_bridge"] = True
        meta["bridge"] = "ffmpeg_wav"
        yield wav, meta
    finally:
        tmp.cleanup()
