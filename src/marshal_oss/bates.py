from __future__ import annotations

import re
from pathlib import Path


def extract_bates(path: str | Path, pattern: str) -> str | None:
    """Best-effort Bates label from filename then parent folders."""
    p = Path(path)
    rx = re.compile(pattern)
    for candidate in [p.name, *reversed(p.parts)]:
        m = rx.search(candidate)
        if not m:
            continue
        # Prefer the first capturing group that matched
        for g in m.groups():
            if g:
                # Reconstruct a readable label from the full match when useful
                full = m.group(0).strip()
                return re.sub(r"\s+", " ", full)
        return m.group(0).strip()
    return None
