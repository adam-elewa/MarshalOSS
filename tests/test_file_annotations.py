from marshal_oss.db import Database


def test_user_note_and_tags(tmp_path):
    db = Database(tmp_path / "t.db")
    case = db.upsert_case("demo", "Demo", str(tmp_path / "files"))
    (tmp_path / "files").mkdir()
    rel = "reports/a.pdf"
    db.upsert_file(
        case.id,
        rel,
        "a.pdf",
        None,
        "application/pdf",
        10,
        None,
        None,
        status="ready",
        meta={"tags": ["ingest_tag"], "entities": []},
    )
    db.set_file_user_note(case.id, rel, "Review for 911 timeline")
    db.set_file_user_tags(case.id, rel, ["priority", "911"])
    detail = db.get_file_detail(case.id, rel)
    assert detail is not None
    assert detail["user_note"] == "Review for 911 timeline"
    assert detail["user_tags"] == ["priority", "911"]
    # ingest tags untouched
    assert "ingest_tag" in detail["tags"] or detail["tags"] == ["ingest_tag"]
