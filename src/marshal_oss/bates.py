from __future__ import annotations

import re
from pathlib import Path

from .media_ffmpeg import is_media_path

# Filename tokens that look like camera clocks / date stamps, not production Bates.
_TIMESTAMPISH = re.compile(
    r"""(?ix)
    ^(?:
        \d{8}             # YYYYMMDD
      | \d{6}             # HHMMSS / YYMMDD
      | \d{4}[-_/]\d{2}[-_/]\d{2}   # date
      | \d{1,2}:\d{2}(?::\d{2})?    # clock
    )$
    """
)


def _looks_like_timestamp_stamp(label: str) -> bool:
    """True when a Bates-pattern hit is really a video/date time stamp."""
    s = re.sub(r"\s+", " ", (label or "").strip())
    if not s:
        return False
    # BODYCAM_143022 / REC_091530 / CLIP-20240115 → trailing stamp token
    tail = re.split(r"[\s_-]+", s)[-1]
    if _TIMESTAMPISH.match(tail):
        return True
    # Entire match is only a stamp
    if _TIMESTAMPISH.match(s):
        return True
    return False


def extract_bates(path: str | Path, pattern: str) -> str | None:
    """Best-effort Bates label from filename then parent folders.

    Skips audio/video paths — those filenames are full of clock/date stamps
    that the generic Bates pattern otherwise treats as production ids.
    """
    p = Path(path)
    if is_media_path(p):
        return None
    rx = re.compile(pattern)
    for candidate in [p.name, *reversed(p.parts)]:
        m = rx.search(candidate)
        if not m:
            continue
        full = re.sub(r"\s+", " ", m.group(0).strip())
        if _looks_like_timestamp_stamp(full):
            continue
        for g in m.groups():
            if g and _looks_like_timestamp_stamp(g):
                continue
            if g:
                return full
        if not _looks_like_timestamp_stamp(full):
            return full
    return None
