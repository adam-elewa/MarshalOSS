from marshal_oss.db import Database
from marshal_oss.notebook import (
    dedupe_person_entities,
    doc_wiki_link,
    list_notebook_skills,
    load_notebook_skill_recipe,
    load_notebook_skill_text,
    page_wiki_link,
)


def test_normalize_path():
    assert Database.normalize_notebook_path("  Witnesses//Jane Doe/ ") == "Witnesses/Jane Doe"


def test_wiki_links():
    assert doc_wiki_link("a/b.pdf", "Bates 1") == "[[doc:a/b.pdf|Bates 1]]"
    assert page_wiki_link("Witnesses/Jane", "Jane") == "[[page:Witnesses/Jane|Jane]]"


def test_dedupe_persons():
    people = [
        {"canonical": "David Jones", "count": 16},
        {"canonical": "David E. Jones", "count": 4},
        {"canonical": "David Eli Jones", "count": 10},
        {"canonical": "Diane Carl", "count": 3},
        {"canonical": "Speaker A", "count": 9},
        {"canonical": "Tracy", "count": 5},
        {"canonical": "Someone", "count": 1},
    ]
    out = dedupe_person_entities(people, min_count=2)
    names = [p["canonical"] for p in out]
    assert "David Jones" in names
    assert "David E. Jones" not in names
    assert "David Eli Jones" not in names
    assert "Diane Carl" in names
    assert "Speaker A" not in names
    assert "Tracy" not in names
    assert "Someone" not in names


def test_skill_text_loads():
    text = load_notebook_skill_text(strip_frontmatter=True)
    assert "wiki" in text.lower()
    assert "notebook_skill" in text or "skills/" in text


def test_cowork_playbook_pointer():
    from marshal_oss.notebook import load_cowork_playbook, playbook_pointer

    ptr = playbook_pointer("http://example:7340")
    assert ptr["mcp"] == "get_instructions"
    assert ptr["http"].endswith("/api/mcp/instructions")
    assert "http_full" in ptr
    body = load_cowork_playbook(strip_frontmatter=True)
    assert "read-only" in body.lower() or "read-only" in body
    assert "get_file" in body


def test_list_and_load_recipes():
    skills = list_notebook_skills()
    names = {s["name"] for s in skills}
    assert {"cowork", "cross-examination", "timeline"} <= names
    cross = load_notebook_skill_recipe("cross-examination", strip_frontmatter=True)
    assert "person" in cross.lower() or "entity" in cross.lower()
    assert "[[doc:" in cross
    timeline = load_notebook_skill_recipe("timeline", strip_frontmatter=True)
    assert "timeline" in timeline.lower()
    assert "[[doc:" in timeline
    cowork = load_notebook_skill_recipe("cowork", strip_frontmatter=True)
    assert "get_file" in cowork


def test_notebook_crud(tmp_path):
    db = Database(tmp_path / "t.db")
    case = db.upsert_case("demo", "Demo", str(tmp_path / "files"))
    (tmp_path / "files").mkdir()
    home = db.ensure_notebook_home(case.id, "Demo")
    assert home["path"] == "Home"
    assert "skills/" in home["body"]
    skills_index = db.get_notebook_page(case.id, "Meta/Skills")
    assert skills_index
    assert {"cowork", "cross-examination", "timeline"} <= {s["name"] for s in list_notebook_skills()}
    cross = db.get_notebook_page(case.id, "Meta/Skills/cross-examination")
    assert cross and "[[doc:" in cross["body"]
    timeline = db.get_notebook_page(case.id, "Meta/Skills/timeline")
    assert timeline and "Timeline" in timeline["body"]
    cowork = db.get_notebook_page(case.id, "Meta/Skills/cowork")
    assert cowork and "get_file" in cowork["body"]
    page = db.upsert_notebook_page(case.id, "Theory/Opening", "Opening", "# hi\n")
    assert page["path"] == "Theory/Opening"
    assert db.get_notebook_page(case.id, "Theory/Opening")["body"] == "# hi\n"
    paths = {p["path"] for p in db.list_notebook_pages(case.id)}
    assert "Home" in paths and "Meta/Skills" in paths and "Theory/Opening" in paths
    assert db.delete_notebook_page(case.id, "Theory/Opening")
    assert db.get_notebook_page(case.id, "Theory/Opening") is None
