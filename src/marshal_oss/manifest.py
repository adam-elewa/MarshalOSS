"""
Production prepare workflow: unzip → file-by-file hashed manifest.

Writes artifacts a UX can consume:
  <root>/.marshaloss/manifest.jsonl
  <root>/.marshaloss/manifest_summary.json

Also upserts rows into SQLite ``production_manifest`` when a case is bound.
"""

from __future__ import annotations

import json
import mimetypes
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator

from .archives import EXTRACT_SUFFIX, expand_zips
from .bates import extract_bates
from .config import Settings
from .db import Database
from .hashing import sha256_file, sniff_ext
from .media_ffmpeg import AUDIO_VIDEO_EXTS

LogFn = Callable[[str], None]

JUNK_EXTS = frozenset(
    {
        ".lnk",
        ".ifo",
        ".bup",
        ".ds_store",
        ".tmp",
        ".temp",
        ".exe",
        ".msi",
        ".dll",
        ".ax",
        ".ocx",
    }
)
CONTAINER_EXTS = frozenset({".zip", ".tar", ".gz", ".tgz", ".7z", ".rar"})
LOADFILE_HINTS = ("loadfile", "load_file", "dat.opt", "opticon")


@dataclass
class ManifestRecord:
    rel_path: str
    name: str
    ext: str
    detected_ext: str | None
    size: int
    mtime: float | None
    sha256: str
    mime: str | None
    bates: str | None
    kind: str  # evidence | container | unzipped | loadfile | junk | sidecar
    ingestible: bool
    parent_zip: str | None = None
    produced_from: str = "native"  # native | zip_expand


@dataclass
class ManifestResult:
    root: str
    slug: str | None
    created_at: str
    zip_stats: dict[str, int]
    summary: dict[str, Any]
    records: list[ManifestRecord] = field(default_factory=list)
    jsonl_path: str | None = None
    summary_path: str | None = None


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _rel(root: Path, path: Path) -> str:
    return str(path.relative_to(root)).replace("\\", "/")


def _parent_zip(root: Path, path: Path) -> str | None:
    """If path lives under Foo__unzipped/, return relative path to Foo.zip when present."""
    parts = path.relative_to(root).parts
    for i, part in enumerate(parts):
        if part.endswith(EXTRACT_SUFFIX):
            stem = part[: -len(EXTRACT_SUFFIX)]
            zip_path = root.joinpath(*parts[:i], f"{stem}.zip")
            if zip_path.is_file():
                return _rel(root, zip_path)
            return "/".join([*parts[:i], f"{stem}.zip"]) or None
    return None


def _classify(
    *,
    ext: str,
    name: str,
    rel: str,
    parent_zip: str | None,
    ingestible: bool,
) -> tuple[str, str]:
    """Return (kind, produced_from)."""
    produced = "zip_expand" if parent_zip else "native"
    low_name = name.lower()
    low_rel = rel.lower()
    if ext in CONTAINER_EXTS:
        return "container", produced
    if ext in JUNK_EXTS or low_name in {"thumbs.db", "desktop.ini"}:
        return "junk", produced
    if any(h in low_name or h in low_rel for h in LOADFILE_HINTS):
        return "loadfile", produced
    if parent_zip:
        return "unzipped", produced
    if not ingestible and ext in {".json", ".log", ".sh", ".py"}:
        return "sidecar", produced
    return "evidence", produced


def iter_all_files(root: Path) -> Iterator[Path]:
    for p in sorted(root.rglob("*"), key=lambda x: str(x).lower()):
        if not p.is_file():
            continue
        if p.name.startswith("."):
            continue
        # Skip our own artifact dir contents from re-hash loops except allow listing once
        if ".marshaloss" in p.parts:
            continue
        yield p


def build_manifest_records(
    root: Path,
    settings: Settings,
    *,
    log: LogFn | None = None,
) -> list[ManifestRecord]:
    _log = log or (lambda _m: None)
    allowed = settings.allowed_extensions
    records: list[ManifestRecord] = []
    for path in iter_all_files(root):
        try:
            st = path.stat()
        except OSError as e:
            _log(f"[yellow]manifest skip[/yellow] {path}: {e}")
            continue
        rel = _rel(root, path)
        ext = path.suffix.lower() or "(none)"
        detected = None
        if ext == "(none)" or ext not in allowed:
            detected = sniff_ext(path)
        effective = detected or (None if ext == "(none)" else ext)
        digest = sha256_file(path)
        mime = mimetypes.guess_type(path.name)[0]
        if not mime and detected:
            mime = mimetypes.guess_type(f"x{detected}")[0]
        parent_zip = _parent_zip(root, path)
        ingestible = bool(
            (effective and effective in allowed)
            or (ext in allowed)
            or (detected and detected in allowed)
            or (detected and detected in AUDIO_VIDEO_EXTS)
        )
        # Containers themselves are not "ingested" as docs
        if ext in CONTAINER_EXTS:
            ingestible = False
        kind, produced = _classify(
            ext=ext if ext != "(none)" else (detected or ""),
            name=path.name,
            rel=rel,
            parent_zip=parent_zip,
            ingestible=ingestible,
        )
        if ext in CONTAINER_EXTS:
            kind = "container"
            ingestible = False
        records.append(
            ManifestRecord(
                rel_path=rel,
                name=path.name,
                ext=ext,
                detected_ext=detected,
                size=st.st_size,
                mtime=st.st_mtime,
                sha256=digest,
                mime=mime,
                bates=extract_bates(rel, settings.bates_pattern),
                kind=kind,
                ingestible=ingestible,
                parent_zip=parent_zip,
                produced_from=produced,
            )
        )
        if len(records) % 500 == 0:
            _log(f"[dim]manifest[/dim] hashed {len(records)} files…")
    return records


def summarize_records(records: list[ManifestRecord]) -> dict[str, Any]:
    by_ext = Counter(r.ext for r in records)
    by_kind = Counter(r.kind for r in records)
    ingestible = [r for r in records if r.ingestible]
    return {
        "file_count": len(records),
        "total_bytes": sum(r.size for r in records),
        "ingestible_count": len(ingestible),
        "ingestible_bytes": sum(r.size for r in ingestible),
        "unique_sha256": len({r.sha256 for r in records}),
        "by_kind": dict(by_kind.most_common()),
        "by_ext": dict(by_ext.most_common(40)),
        "bates_labeled": sum(1 for r in records if r.bates),
        "from_zip": sum(1 for r in records if r.produced_from == "zip_expand"),
    }


def write_manifest_artifacts(
    root: Path,
    records: list[ManifestRecord],
    *,
    zip_stats: dict[str, int],
    slug: str | None,
) -> tuple[Path, Path, dict[str, Any]]:
    out_dir = root / ".marshaloss"
    out_dir.mkdir(parents=True, exist_ok=True)
    jsonl_path = out_dir / "manifest.jsonl"
    summary_path = out_dir / "manifest_summary.json"
    summary = {
        "schema": "marshaloss.production_manifest.v1",
        "created_at": _utc_now(),
        "root": str(root),
        "slug": slug,
        "zip_expand": zip_stats,
        **summarize_records(records),
    }
    with jsonl_path.open("w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(asdict(r), ensure_ascii=False) + "\n")
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    # Convenience copy for UI: small index of productions-ish top folders
    by_top: Counter[str] = Counter()
    for r in records:
        top = r.rel_path.split("/", 1)[0] if "/" in r.rel_path else r.rel_path
        by_top[top] += 1
    summary["by_top_folder"] = dict(by_top.most_common(50))
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return jsonl_path, summary_path, summary


def prepare_production(
    root: Path,
    settings: Settings,
    *,
    db: Database | None = None,
    slug: str | None = None,
    display_name: str | None = None,
    expand: bool = True,
    log: LogFn | None = None,
) -> ManifestResult:
    """
    Initial ingestion workflow step 0–1:
      1) expand zips
      2) hash every file into a production manifest
    """
    _log = log or (lambda _m: None)
    root = root.resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"root not found: {root}")

    zip_stats = {"archives": 0, "extracted": 0, "skipped": 0, "errors": 0}
    if expand and settings.expand_zips:
        _log("[cyan]prepare[/cyan] expanding zips…")
        zip_stats = expand_zips(
            root,
            max_depth=settings.expand_zip_max_depth,
            log=_log,
        )

    _log("[cyan]prepare[/cyan] hashing files for production manifest…")
    records = build_manifest_records(root, settings, log=_log)
    jsonl_path, summary_path, summary = write_manifest_artifacts(
        root, records, zip_stats=zip_stats, slug=slug
    )
    _log(
        f"[green]manifest[/green] {summary['file_count']} files → {jsonl_path.relative_to(root)}"
    )

    case_slug = slug
    if db is not None and slug:
        name = display_name or slug.replace("-", " ").title()
        case = db.upsert_case(slug, name, str(root))
        db.set_active_case(case.id)
        db.replace_production_manifest(case.id, records)
        db.set_setting(f"manifest_path:{case.id}", str(summary_path))
        case_slug = case.slug

    return ManifestResult(
        root=str(root),
        slug=case_slug,
        created_at=summary["created_at"],
        zip_stats=zip_stats,
        summary=summary,
        records=records,
        jsonl_path=str(jsonl_path),
        summary_path=str(summary_path),
    )
