"""Case notebook helpers: wiki links, skill text, and optional witness recipe."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .config import Settings
from .db import Case, Database
from .search import file_urls


def notebook_skill_path() -> Path:
    """Prefer packaged copy; fall back to project skill / docs."""
    here = Path(__file__).resolve().parent
    candidates = [
        here / "notebook_skill.md",
        here.parents[1] / ".cursor" / "skills" / "marshal-notebook" / "SKILL.md",
        here.parents[1] / "docs" / "notebook-skill.md",
    ]
    for p in candidates:
        if p.is_file():
            return p
    return candidates[0]


def load_notebook_skill_text(*, strip_frontmatter: bool = True) -> str:
    path = notebook_skill_path()
    if not path.is_file():
        return (
            "# MarshalOSS Notebook\n\n"
            "Dynamic case wiki. Use notebook_list / notebook_get / notebook_upsert.\n"
            "Link docs with [[doc:path|label]] and pages with [[page:Path|label]].\n"
        )
    text = path.read_text(encoding="utf-8")
    if strip_frontmatter and text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            text = text[end + 4 :].lstrip("\n")
    return text

_DOC_LINK = re.compile(
    r"\[\[doc:([^\]|]+)(?:\|([^\]]+))?\]\]",
    re.IGNORECASE,
)
_PAGE_LINK = re.compile(
    r"\[\[page:([^\]|]+)(?:\|([^\]]+))?\]\]",
    re.IGNORECASE,
)


def doc_wiki_link(rel_path: str, label: str | None = None) -> str:
    label = (label or rel_path.split("/")[-1]).replace("]", "")
    return f"[[doc:{rel_path}|{label}]]"


def page_wiki_link(path: str, label: str | None = None) -> str:
    label = (label or path.split("/")[-1]).replace("]", "")
    return f"[[page:{path}|{label}]]"


def dedupe_person_entities(persons: list[dict[str, Any]], *, min_count: int = 2) -> list[dict[str, Any]]:
    """Collapse near-duplicate person names; keep highest-count canonical."""
    ranked = sorted(
        (p for p in persons if (p.get("count") or 0) >= min_count and (p.get("canonical") or "").strip()),
        key=lambda p: (-int(p.get("count") or 0), str(p.get("canonical") or "")),
    )
    kept: list[dict[str, Any]] = []
    seen_keys: set[str] = set()

    def core_key(name: str) -> str:
        toks = [t for t in re.sub(r"[^a-z0-9]+", " ", name.lower()).split() if len(t) > 1]
        if not toks:
            return ""
        # first + last collapses "David E. Jones" / "David Eli Jones" → david jones
        if len(toks) >= 2:
            return f"{toks[0]} {toks[-1]}"
        return toks[0]

    junk = re.compile(r"^(speaker|unknown|officer|detective|trooper|defendant|plaintiff)\b", re.I)

    for p in ranked:
        name = str(p["canonical"]).strip()
        if junk.match(name):
            continue
        toks = [t for t in re.sub(r"[^a-z0-9]+", " ", name.lower()).split() if len(t) > 1]
        # Require first + last (skip "Tracy", "Mom", etc.)
        if len(toks) < 2:
            continue
        k = core_key(name)
        if not k or k in seen_keys:
            continue
        kept.append({"type": "person", "canonical": name, "count": int(p.get("count") or 0)})
        seen_keys.add(k)
    return kept


def prune_stale_witness_pages(
    db: Database,
    case_id: int,
    keep_names: set[str],
) -> list[str]:
    """Delete Witnesses/* pages that are not for the current witness set."""
    removed: list[str] = []
    keep_roots = {f"Witnesses/{n}" for n in keep_names}
    for page in db.list_notebook_pages(case_id):
        path = page["path"]
        if path == "Witnesses" or not path.startswith("Witnesses/"):
            continue
        # path is Witnesses/{Name} or Witnesses/{Name}/...
        rest = path[len("Witnesses/") :]
        witness_name = rest.split("/", 1)[0]
        root = f"Witnesses/{witness_name}"
        if root not in keep_roots:
            if db.delete_notebook_page(case_id, path):
                removed.append(path)
    return removed


def build_witness_pack(
    db: Database,
    settings: Settings,
    case: Case,
    *,
    min_count: int = 2,
    max_witnesses: int = 40,
    max_docs_per_witness: int = 15,
) -> dict[str, Any]:
    """Create/refresh Witnesses index + per-witness page and cross-outline stubs."""
    stats = db.case_entity_tag_stats(case.id, top_n=500)
    persons = (stats.get("entities_by_type") or {}).get("person") or []
    witnesses = dedupe_person_entities(persons, min_count=min_count)[:max_witnesses]
    keep_names = {w["canonical"] for w in witnesses}

    db.ensure_notebook_home(case.id, case.display_name)
    removed = prune_stale_witness_pages(db, case.id, keep_names)

    created: list[str] = []
    updated: list[str] = []

    def write(path: str, title: str, body: str) -> None:
        prev = db.get_notebook_page(case.id, path)
        db.upsert_notebook_page(case.id, path, title, body)
        (updated if prev else created).append(path)

    # Index — one row per witness → cross outline (clean hierarchy)
    lines = [
        "# Witnesses",
        "",
        "Each witness has a **Cross outline** underneath. "
        f"Built from person entities (min mentions: {min_count}).",
        "",
    ]
    if not witnesses:
        lines.append("_No person entities met the threshold._")
    else:
        for w in witnesses:
            name = w["canonical"]
            outline = f"Witnesses/{name}/Cross outline"
            lines.append(
                f"- **{page_wiki_link(f'Witnesses/{name}', name)}** → "
                f"{page_wiki_link(outline, 'Cross outline')} ({w['count']} mentions)"
            )
    write("Witnesses", "Witnesses", "\n".join(lines) + "\n")

    for w in witnesses:
        name = w["canonical"]
        files = db.find_files_by_facet(
            case.id,
            entity=name,
            entity_type="person",
            limit=max_docs_per_witness,
        )
        for f in files:
            f.update(file_urls(settings, case, f["rel_path"]))

        overview_path = f"Witnesses/{name}"
        outline_path = f"Witnesses/{name}/Cross outline"

        # Witness hub: short — docs + link to outline only
        overview_lines = [
            f"# {name}",
            "",
            f"{w['count']} mentions in the production.",
            "",
            f"→ {page_wiki_link(outline_path, 'Cross outline')}",
            "",
            "## Linked documents",
            "",
        ]
        if files:
            for f in files:
                label = f.get("bates") or f.get("name") or f["rel_path"]
                overview_lines.append(f"- {doc_wiki_link(f['rel_path'], str(label))}")
        else:
            overview_lines.append("_No matching files yet._")
        overview_lines.append("")
        write(overview_path, name, "\n".join(overview_lines))

        existing_outline = db.get_notebook_page(case.id, outline_path)
        if not existing_outline:
            q_lines = [
                f"# Cross outline — {name}",
                "",
                "_Court exam stub. Keep `[[doc:…]]` links next to questions._",
                "",
                "## Questions",
                "",
            ]
            if files:
                for i, f in enumerate(files[:8], 1):
                    label = f.get("bates") or f.get("name") or f["rel_path"]
                    link = doc_wiki_link(f["rel_path"], str(label))
                    q_lines.append(f"{i}. [ ] _______________________________  → {link}")
                    q_lines.append("")
            else:
                q_lines.append("1. [ ] _______________________________")
                q_lines.append("")
            write(outline_path, "Cross outline", "\n".join(q_lines))

    return {
        "case": case.__dict__,
        "witness_count": len(witnesses),
        "witnesses": [w["canonical"] for w in witnesses],
        "created": created,
        "updated": updated,
        "removed": removed,
        "pages": db.list_notebook_pages(case.id),
    }
