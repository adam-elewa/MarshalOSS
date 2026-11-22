"""Expand production ZIP containers with stdlib zipfile (no custom unpackers)."""

from __future__ import annotations

import os
import zipfile
from pathlib import Path
from typing import Callable

LogFn = Callable[[str], None]

# Extract beside the archive:  Foo.zip → Foo__unzipped/
EXTRACT_SUFFIX = "__unzipped"


def _extract_dest(zpath: Path) -> Path:
    return zpath.parent / f"{zpath.stem}{EXTRACT_SUFFIX}"


def _safe_extract_member(zf: zipfile.ZipFile, member: zipfile.ZipInfo, dest: Path) -> Path | None:
    """Extract one member; reject zip-slip. Returns written path or None if skipped."""
    name = member.filename.replace("\\", "/")
    if not name or name.endswith("/"):
        return None
    parts = Path(name).parts
    if any(p.startswith("__MACOSX") or p == ".DS_Store" for p in parts):
        return None
    base = Path(name).name
    if base.startswith(".") or base in {"Thumbs.db", "desktop.ini"}:
        return None

    dest_root = dest.resolve()
    target = (dest / name).resolve()
    if os.sep != "/":
        # Windows: keep resolve semantics
        pass
    if not (target == dest_root or dest_root in target.parents):
        raise RuntimeError(f"zip-slip blocked: {name}")

    target.parent.mkdir(parents=True, exist_ok=True)
    with zf.open(member, "r") as src, target.open("wb") as out:
        while True:
            chunk = src.read(1024 * 1024)
            if not chunk:
                break
            out.write(chunk)
    return target


def expand_zips(
    root: Path,
    *,
    max_depth: int = 2,
    log: LogFn | None = None,
) -> dict[str, int]:
    """
    Expand ``*.zip`` under ``root`` into sibling ``<name>__unzipped/`` folders.

    Idempotent when the extract folder already has files. Recurses up to
    ``max_depth`` so nested production zips expand too.
    Uses only the stdlib ``zipfile`` module. No archive size cap.
    """
    _log = log or (lambda _m: None)
    root = root.resolve()
    stats = {"archives": 0, "extracted": 0, "skipped": 0, "errors": 0}

    for depth in range(max_depth):
        uniq: list[Path] = []
        seen: set[Path] = set()
        for zpath in sorted(root.rglob("*.zip")):
            if not zpath.is_file():
                continue
            # Don't treat extract dirs as sources of confusion; still expand zips inside them
            rz = zpath.resolve()
            if rz in seen:
                continue
            seen.add(rz)
            uniq.append(zpath)

        new_this_pass = 0
        for zpath in uniq:
            if not zpath.is_file():
                stats["errors"] += 1
                continue

            dest = _extract_dest(zpath)
            if dest.is_dir() and any(dest.rglob("*")):
                stats["skipped"] += 1
                continue

            try:
                rel = zpath.relative_to(root)
            except ValueError:
                rel = zpath
            dest.mkdir(parents=True, exist_ok=True)
            _log(f"[cyan]zip expand[/cyan] {rel} → {dest.name}/")
            try:
                with zipfile.ZipFile(zpath, "r") as zf:
                    for info in zf.infolist():
                        if info.is_dir():
                            continue
                        written = _safe_extract_member(zf, info, dest)
                        if written is not None:
                            stats["extracted"] += 1
                stats["archives"] += 1
                new_this_pass += 1
            except Exception as e:
                _log(f"[red]zip failed[/red] {rel}: {e}")
                stats["errors"] += 1

        if new_this_pass == 0:
            break
        _log(f"[dim]zip pass {depth + 1}: {new_this_pass} archive(s)[/dim]")

    return stats
