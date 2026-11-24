from marshal_oss.db import Database
from marshal_oss.notebook import (
    dedupe_person_entities,
    doc_wiki_link,
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
        {"canonical": "Someone", "count": 1},
    ]
    out = dedupe_person_entities(people, min_count=2)
    names = [p["canonical"] for p in out]
    assert "David Jones" in names
    assert "David E. Jones" not in names
    assert "David Eli Jones" not in names
    assert "Diane Carl" in names
    assert "Speaker A" not in names
    assert "Someone" not in names


def test_skill_text_loads():
    text = load_notebook_skill_text(strip_frontmatter=True)
    assert "dynamic" in text.lower() or "wiki" in text.lower()
    assert "[[doc:" in text or "doc:" in text


def test_notebook_crud(tmp_path):
    db = Database(tmp_path / "t.db")
    case = db.upsert_case("demo", "Demo", str(tmp_path / "files"))
    (tmp_path / "files").mkdir()
    home = db.ensure_notebook_home(case.id, "Demo")
    assert home["path"] == "Home"
    skill = db.get_notebook_page(case.id, "Meta/Notebook skill")
    assert skill and "wiki" in skill["body"].lower()
    page = db.upsert_notebook_page(case.id, "Theory/Opening", "Opening", "# hi\n")
    assert page["path"] == "Theory/Opening"
    assert db.get_notebook_page(case.id, "Theory/Opening")["body"] == "# hi\n"
    paths = {p["path"] for p in db.list_notebook_pages(case.id)}
    assert "Home" in paths and "Meta/Notebook skill" in paths and "Theory/Opening" in paths
    assert db.delete_notebook_page(case.id, "Theory/Opening")
    assert db.get_notebook_page(case.id, "Theory/Opening") is None
