from marshal_oss.db import Database
from marshal_oss.notebook import dedupe_person_entities, doc_wiki_link, page_wiki_link


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


def test_notebook_crud(tmp_path):
    db = Database(tmp_path / "t.db")
    case = db.upsert_case("demo", "Demo", str(tmp_path / "files"))
    (tmp_path / "files").mkdir()
    home = db.ensure_notebook_home(case.id, "Demo")
    assert home["path"] == "Home"
    page = db.upsert_notebook_page(case.id, "Witnesses/Jane/Cross outline", "Cross", "# hi\n")
    assert page["path"] == "Witnesses/Jane/Cross outline"
    assert db.get_notebook_page(case.id, "Witnesses/Jane/Cross outline")["body"] == "# hi\n"
    assert len(db.list_notebook_pages(case.id)) == 2
    assert db.delete_notebook_page(case.id, "Witnesses/Jane/Cross outline")
    assert db.get_notebook_page(case.id, "Witnesses/Jane/Cross outline") is None
