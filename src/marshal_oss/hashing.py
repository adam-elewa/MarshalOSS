"""Content hashing helpers."""

from __future__ import annotations

import hashlib
from pathlib import Path


def sha256_file(path: Path, *, max_bytes: int | None = None) -> str:
    """
    SHA-256 of file contents.

    ``max_bytes=None`` hashes the whole file (production manifest).
    A positive cap is only for cheap change-detection shortcuts.
    """
    h = hashlib.sha256()
    with path.open("rb") as f:
        if max_bytes is None:
            while True:
                chunk = f.read(1024 * 1024)
                if not chunk:
                    break
                h.update(chunk)
        else:
            remaining = max_bytes
            while remaining > 0:
                chunk = f.read(min(1024 * 1024, remaining))
                if not chunk:
                    break
                h.update(chunk)
                remaining -= len(chunk)
    return h.hexdigest()


def sniff_ext(path: Path, *, head: bytes | None = None) -> str | None:
    """Best-effort extension from magic bytes (for Bates files with no suffix)."""
    data = head
    if data is None:
        try:
            with path.open("rb") as f:
                data = f.read(64)
        except OSError:
            return None
    if not data:
        return None
    if data.startswith(b"%PDF"):
        return ".pdf"
    if data.startswith(b"PK\x03\x04"):
        return ".zip"
    if data.startswith(b"RIFF") and b"WAVE" in data[:16]:
        return ".wav"
    if data.startswith(b"ID3") or data[:2] in (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"):
        return ".mp3"
    if len(data) >= 12 and data[4:8] == b"ftyp":
        brand = data[8:12]
        if brand in (b"M4A ", b"M4B ", b"mp42", b"isom", b"avc1", b"qt  "):
            return ".m4a" if brand in (b"M4A ", b"M4B ") else ".mp4"
        return ".mp4"
    if data.startswith(b"\x1aE\xdf\xa3"):
        return ".mkv"
    if data.startswith(b"OggS"):
        return ".ogg"
    if data.startswith(b"fLaC"):
        return ".flac"
    return None
