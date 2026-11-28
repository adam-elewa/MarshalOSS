from pathlib import Path

from marshal_oss.db import Database, sanitize_fts5_query


def test_sanitize_strips_periods_and_ops():
    assert sanitize_fts5_query("What happened on Aug. 9?") == "happened OR Aug OR 9"
    assert sanitize_fts5_query('officer (Smith) AND "gun"') == "officer OR Smith OR gun"
    assert sanitize_fts5_query("NEAR:foo * bar") == "foo OR bar"
    assert sanitize_fts5_query("Smith") == "Smith"
    assert sanitize_fts5_query("   ") == ""


def test_fts_search_accepts_natural_language(tmp_path: Path):
    db = Database(tmp_path / "t.db")
    case = db.upsert_case("demo-case", "Demo", str(tmp_path))
    fid = db.upsert_file(
        case.id, "r.txt", "r.txt", None, "text/plain", 10, None, "abc", status="ready"
    )
    db.replace_chunks(
        fid,
        case.id,
        "r.txt",
        [{"text": "Officer Smith fired on Aug. 9 near Main St."}],
    )

    hits = db.fts_search(case.id, "What happened on Aug. 9 near Main St.?")
    assert hits
    assert "Smith" in hits[0]["text"]
