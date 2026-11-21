from __future__ import annotations

from pathlib import Path
from typing import Any

from .config import Settings
from .db import Case, Database
from .search import file_urls


def list_dir(
    db: Database,
    settings: Settings,
    case: Case,
    path: str = "",
) -> dict[str, Any]:
    root = Path(case.files_root)
    rel = path.strip("/")
    target = (root / rel).resolve() if rel else root.resolve()
    if not str(target).startswith(str(root.resolve())):
        raise PermissionError("path escapes root")
    if not target.exists():
        raise FileNotFoundError(rel or ".")

    entries: list[dict[str, Any]] = []
    if target.is_dir():
        for child in sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
            if child.name.startswith("."):
                continue
            child_rel = str(child.relative_to(root)).replace("\\", "/")
            if child.is_dir():
                entries.append(
                    {
                        "name": child.name,
                        "path": child_rel,
                        "type": "dir",
                        "size": 0,
                        "bates": None,
                    }
                )
            else:
                row = db.get_file(case.id, child_rel)
                urls = file_urls(settings, case, child_rel)
                entries.append(
                    {
                        "name": child.name,
                        "path": child_rel,
                        "type": "file",
                        "size": child.stat().st_size,
                        "bates": (row["bates"] if row else None),
                        "status": (row["status"] if row else "not_ingested"),
                        **urls,
                    }
                )

    breadcrumbs = []
    if rel:
        acc: list[str] = []
        for part in Path(rel).parts:
            acc.append(part)
            breadcrumbs.append({"name": part, "path": "/".join(acc)})

    return {
        "case_id": case.id,
        "slug": case.slug,
        "case_name": case.display_name,
        "root": str(root),
        "path": rel,
        "breadcrumbs": breadcrumbs,
        "entries": entries,
        "count": len(entries),
    }
