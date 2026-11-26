"""Case notebook helpers: wiki links, git-backed skills, optional witness recipe."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .config import Settings
from .db import Case, Database
from .search import file_urls


def repo_skills_root() -> Path:
    """Repo ``skills/`` directory (git-backed playbooks)."""
    here = Path(__file__).resolve().parent
    # src/marshal_oss → repo root
    return here.parents[1] / "skills"


def notebook_skills_dir() -> Path:
    """Repo ``skills/`` — single folder for Cowork playbook + notebook recipes."""
    return repo_skills_root()


def cowork_playbook_path() -> Path:
    return repo_skills_root() / "cowork.md"


def load_cowork_playbook(*, strip_frontmatter: bool = True) -> str:
    path = cowork_playbook_path()
    if not path.is_file():
        return (
            "# MarshalOSS Cowork\n\n"
            "search / ask → get_file (page if needed) → notebook_upsert with [[doc:]]. "
            "Discovery is read-only; writable: notebook, user_note, user_tags.\n"
        )
    text = path.read_text(encoding="utf-8")
    return _strip_frontmatter(text) if strip_frontmatter else text


def playbook_pointer(public_base: str) -> dict[str, str]:
    """Compact pointer so hosts can load the full playbook only when needed."""
    base = (public_base or "").rstrip("/") or "http://127.0.0.1:7340"
    return {
        "hint": (
            "Skills live in repo skills/. Call notebook_skill for the list; "
            "get_instructions(full=true) or notebook_skill(name='cowork') for the Cowork playbook."
        ),
        "mcp": "get_instructions",
        "mcp_skill_list": "notebook_skill",
        "http": f"{base}/api/mcp/instructions",
        "http_full": f"{base}/api/mcp/instructions?full=1",
        "skills_list_http": f"{base}/api/notebook/skill",
        "skills_dir": "skills/",
    }


def _strip_frontmatter(text: str) -> str:
    if not text.startswith("---"):
        return text
    end = text.find("\n---", 3)
    if end == -1:
        return text
    return text[end + 4 :].lstrip("\n")


def _parse_frontmatter(text: str) -> dict[str, str]:
    meta: dict[str, str] = {}
    if not text.startswith("---"):
        return meta
    end = text.find("\n---", 3)
    if end == -1:
        return meta
    block = text[3:end]
    key = None
    buf: list[str] = []
    for line in block.splitlines():
        if line.startswith("  ") and key:
            buf.append(line.strip())
            continue
        if ":" in line and not line.startswith(" "):
            if key:
                meta[key] = " ".join(buf).strip().strip("\"'")
            k, _, v = line.partition(":")
            key = k.strip()
            v = v.strip()
            if v == ">|" or v == ">":
                buf = []
            elif v.startswith(">-"):
                buf = [v[2:].strip()]
            elif v.startswith(">"):
                buf = [v[1:].strip()]
            else:
                buf = [v.strip("\"'")] if v else []
        elif key:
            buf.append(line.strip())
    if key:
        meta[key] = " ".join(buf).strip().strip("\"'")
    return meta


def list_notebook_skills() -> list[dict[str, str]]:
    """List every skill Markdown under ``skills/`` (excludes README)."""
    root = notebook_skills_dir()
    out: list[dict[str, str]] = []
    if not root.is_dir():
        return out
    for path in sorted(root.glob("*.md")):
        if path.name.lower() == "readme.md":
            continue
        raw = path.read_text(encoding="utf-8")
        meta = _parse_frontmatter(raw)
        name = meta.get("name") or path.stem
        # Prefer short stem for MCP name when frontmatter uses a long id
        stem = path.stem
        kind = "playbook" if stem in {"cowork", "playbook"} else "recipe"
        out.append(
            {
                "name": stem,
                "aliases": sorted({stem, name.lower()} - {""}),
                "file": path.name,
                "path": str(path),
                "title": stem.replace("-", " ").title(),
                "description": meta.get("description") or "",
                "kind": kind,
                "wiki_path": f"Meta/Skills/{stem}",
            }
        )
    return out


def load_notebook_skill_recipe(name: str, *, strip_frontmatter: bool = True) -> str:
    """Load one skill by stem, alias, or frontmatter name from ``skills/``."""
    root = notebook_skills_dir()
    needle = name.strip().lower().replace(" ", "-")
    # Allow marshaloss-cowork → cowork
    if needle.startswith("marshaloss-"):
        needle = needle[len("marshaloss-") :]
    if not root.is_dir():
        raise FileNotFoundError(f"skills directory missing: {root}")
    for path in root.glob("*.md"):
        if path.name.lower() == "readme.md":
            continue
        raw = path.read_text(encoding="utf-8")
        meta = _parse_frontmatter(raw)
        aliases = {path.stem.lower(), (meta.get("name") or "").lower()}
        if needle in aliases:
            return _strip_frontmatter(raw) if strip_frontmatter else raw
    raise FileNotFoundError(f"skill not found: {name} (skills/: {[p.stem for p in root.glob('*.md') if p.name.lower() != 'readme.md']})")


def notebook_skill_path() -> Path:
    """Prefer packaged overview; fall back to project skill / docs."""
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
    """Overview playbook (points at repo ``skills/``)."""
    path = notebook_skill_path()
    if not path.is_file():
        return (
            "# MarshalOSS Notebook\n\n"
            "Dynamic case wiki. Use notebook_list / notebook_get / notebook_upsert.\n"
            "Load recipes with notebook_skill(name). Link docs with [[doc:path|label]].\n"
        )
    text = path.read_text(encoding="utf-8")
    if strip_frontmatter:
        text = _strip_frontmatter(text)
    return text


def sync_skill_wiki_pages(db: Database, case_id: int) -> list[str]:
    """
    Mirror git recipes into Meta/Skills/* so the UI stays raw wiki.
    Returns wiki paths written.
    """
    written: list[str] = []
    skills = list_notebook_skills()

    meta_body = (
        "# Meta\n\n"
        "**Meta** holds how-to guides for using Marshal — not your case notes.\n\n"
        "Think of it as the instruction binder for the software. These pages show "
        "Claude (and you) how to build timelines, witness folders, and similar "
        "sections. They are copies of the built-in guides that ship with Marshal; "
        "when you reopen the Notebook, Marshal refreshes them from those guides.\n\n"
        "Put your actual case work — theory, chronology, cross outlines — in other "
        "folders such as `Timeline`, `Witnesses`, or `Theory`. Don’t keep lasting "
        "case notes under Meta; they may get overwritten when the guides refresh.\n\n"
        "## What’s here\n\n"
        f"- {page_wiki_link('Meta/Skills', 'Skills')} — the built-in playbooks "
        "(Cowork workflow, cross-examination, timeline, and others)\n"
    )
    db.upsert_notebook_page(case_id, "Meta", "Meta", meta_body)
    written.append("Meta")

    index = [
        "# Skills",
        "",
        "All MarshalOSS skills from repo folder **`skills/`** "
        "(Cowork playbook + notebook recipes). "
        "Load the same text via MCP `notebook_skill` (lists skills when called with no name). "
        "These pages refresh from git when the notebook opens — edit the repo files, not these mirrors.",
        "",
    ]
    if not skills:
        index.append("_No skill files found under `skills/`._")
    else:
        for s in skills:
            kind = s.get("kind") or "recipe"
            index.append(
                f"- **{page_wiki_link(s['wiki_path'], s['title'])}** "
                f"(`{s['name']}`, {kind}) — {s['description'] or 'see page'}"
            )
            body = load_notebook_skill_recipe(s["name"], strip_frontmatter=True)
            db.upsert_notebook_page(case_id, s["wiki_path"], s["title"], body)
            written.append(s["wiki_path"])
    index.append("")
    db.upsert_notebook_page(case_id, "Meta/Skills", "Skills", "\n".join(index) + "\n")
    written.append("Meta/Skills")
    return written


def home_notebook_body(case_name: str) -> str:
    skills = list_notebook_skills()
    skill_lines = []
    for s in skills:
        skill_lines.append(
            f"- {page_wiki_link(s['wiki_path'], s['title'])} — "
            f"MCP: `notebook_skill` with name `{s['name']}`"
        )
    if not skill_lines:
        skill_lines.append("- _(add `.md` files under `skills/` in the repo)_")

    return (
        f"# {case_name}\n\n"
        "This **Notebook** is a blank Markdown wiki for the case — raw pages all the "
        "way down. Humans and AI invent folders as needed (people, timeline, theory, "
        "motions, …). There is no fixed layout and no special “wizard” chrome: create "
        "and edit pages like any wiki.\n\n"
        "## Discovery links\n\n"
        "Cite production files inline with `[[doc:relative/path|Label]]`. In the web UI, "
        "those links open a **side viewer** next to this page so you can read the wiki "
        "and the exhibit together. Link other wiki pages with `[[page:Path|Label]]`.\n\n"
        "## Skills (`skills/`)\n\n"
        "Call MCP `notebook_skill` with **no name** for the skill list. "
        "Repo folder `skills/` is mirrored here:\n\n"
        + "\n".join(skill_lines)
        + "\n\n"
        "Ask Cowork / Computer / Claude to load a skill (`notebook_skill` with a name), "
        "then scaffold pages with `notebook_upsert` using real paths from `search` / "
        "`browse_case_files`. Prefer organizing by **person / entity**, with direct "
        "cites on every material claim.\n\n"
        "## Editing\n\n"
        "- Use Preview / Edit in the toolbar to change a page, then Save.\n"
        "- Prefer AI or MCP to add new paths; keep the tree as ordinary wiki pages.\n"
    )


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
        if len(toks) >= 2:
            return f"{toks[0]} {toks[-1]}"
        return toks[0]

    junk = re.compile(r"^(speaker|unknown|officer|detective|trooper|defendant|plaintiff)\b", re.I)

    for p in ranked:
        name = str(p["canonical"]).strip()
        if junk.match(name):
            continue
        toks = [t for t in re.sub(r"[^a-z0-9]+", " ", name.lower()).split() if len(t) > 1]
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
    """Optional MCP recipe helper: Witnesses index + per-person cross outline stubs."""
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

    lines = [
        "# Witnesses",
        "",
        "Each person has a **Cross outline** underneath. "
        f"Built from person entities (min mentions: {min_count}). "
        "Prefer refining with the `cross-examination` skill.",
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
                "_Exam stub. Keep `[[doc:…]]` links next to questions._",
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
