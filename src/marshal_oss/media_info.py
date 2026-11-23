"""
Classify audio/video at ingest (bodycam, dashcam, other) and pull recording details.

Signals, strongest first: known vendor filename patterns, folder/file keywords,
and ffprobe container tags. Feeds the defense viewers (e.g. bodycam timeline).
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from .media_ffmpeg import is_media_path, media_duration_sec

_AUDIO_ONLY_EXTS = frozenset({".wav", ".mp3", ".m4a", ".aac", ".ogg", ".flac", ".wma", ".caf"})

# Axon exports: Axon_Body_3_Video_2024-08-09_1505_X60AE180D.mp4,
# AXON_Fleet_2_IR_Video_2024-08-09_1500.mp4
_AXON_BODY = re.compile(
    r"(?i)^axon[_ ]body[_ ]?(\d+)[_ ]video[_ ](\d{4}-\d{2}-\d{2})[_ ](\d{2})(\d{2})(?:[_ ]([A-Z0-9]{6,}))?"
)
_AXON_FLEET = re.compile(
    r"(?i)^axon[_ ]fleet[_ ]?(\d+)(?:[_ ](ir))?[_ ]video[_ ](\d{4}-\d{2}-\d{2})[_ ](\d{2})(\d{2})"
)

_BODYCAM_WORDS = re.compile(r"(?i)(body[\s_-]?(cam|worn|camera)|\bbwc\b|\bbwv\b)")
_DASHCAM_WORDS = re.compile(r"(?i)(dash[\s_-]?cam|in[\s_-]?car\b|\bmvr\b)")


def fmt_clock(sec: float | None) -> str:
    if sec is None:
        return "?"
    s = int(sec)
    h, rem = divmod(s, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _ffprobe_tags(path: Path) -> dict[str, str]:
    if not shutil.which("ffprobe"):
        return {}
    try:
        out = subprocess.check_output(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format_tags:stream_tags",
                "-of",
                "json",
                str(path),
            ],
            text=True,
            timeout=120,
            stderr=subprocess.DEVNULL,
        )
        data = json.loads(out or "{}")
    except Exception:
        return {}
    tags: dict[str, str] = {}
    for stream in data.get("streams") or []:
        for k, v in (stream.get("tags") or {}).items():
            tags.setdefault(k.lower(), str(v))
    for k, v in ((data.get("format") or {}).get("tags") or {}).items():
        tags[k.lower()] = str(v)
    return tags


def describe_media(path: Path, rel_path: str) -> dict[str, Any] | None:
    """Return media kind + recording details for A/V files, else None."""
    if not is_media_path(path):
        return None
    audio_only = path.suffix.lower() in _AUDIO_ONLY_EXTS
    info: dict[str, Any] = {
        "kind": "audio" if audio_only else "video",
        "duration_sec": media_duration_sec(path),
        "signals": [],
    }

    m = _AXON_BODY.match(path.name)
    if m:
        n, date, hh, mm, serial = m.groups()
        info.update(
            kind="bodycam",
            vendor="Axon",
            device=f"Axon Body {n}",
            device_serial=serial,
            recorded_start=f"{date}T{hh}:{mm}",
        )
        info["signals"].append("filename:axon_body")
    else:
        m = _AXON_FLEET.match(path.name)
        if m:
            n, ir, date, hh, mm = m.groups()
            info.update(
                kind="dashcam",
                vendor="Axon",
                device=f"Axon Fleet {n}" + (" (IR)" if ir else ""),
                recorded_start=f"{date}T{hh}:{mm}",
            )
            info["signals"].append("filename:axon_fleet")

    if info["kind"] == "video":
        if _BODYCAM_WORDS.search(rel_path):
            info["kind"] = "bodycam"
            info["signals"].append("path:bodycam_keyword")
        elif _DASHCAM_WORDS.search(rel_path):
            info["kind"] = "dashcam"
            info["signals"].append("path:dashcam_keyword")

    tags = _ffprobe_tags(path)
    if tags.get("creation_time") and not info.get("recorded_start"):
        info["recorded_start"] = tags["creation_time"]
        info["signals"].append("ffprobe:creation_time")
    for key in ("make", "model", "encoder", "com.apple.quicktime.make", "com.apple.quicktime.model"):
        if tags.get(key):
            info.setdefault("container_tags", {})[key] = tags[key]
    return info
