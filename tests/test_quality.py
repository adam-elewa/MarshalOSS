from marshal_oss.quality import score_extraction


def test_good_legal_text_ok():
    text = (
        "The defendant was advised of his Miranda rights prior to questioning. "
        "Officer Gigante observed the vehicle near Bergen Avenue. " * 20
    )
    r = score_extraction(text, file_size=200_000, page_count=5)
    assert not r.escalate
    assert r.score >= 0.72


def test_empty_escalates():
    r = score_extraction("", file_size=5_000_000, page_count=40)
    assert r.escalate
    assert "near_empty_text" in r.reasons


def test_weird_symbols_escalate():
    text = "Normal header\n" + ("□□�¦¦��||||" * 40) + "\nmore"
    r = score_extraction(text, file_size=100_000, page_count=2)
    assert r.escalate
    assert any(x.startswith("weird_symbols") for x in r.reasons)


def test_docling_poor_grade():
    text = "Some short text that is otherwise fine." * 5
    r = score_extraction(
        text,
        file_size=1_000_000,
        page_count=10,
        confidence={"mean_grade": "poor", "low_grade": "poor", "ocr_score": 0.2},
    )
    assert r.escalate
    assert any("docling_mean_grade=poor" in x for x in r.reasons)


def test_sparse_vs_large_scan():
    text = "Page one barely readable."
    r = score_extraction(text, file_size=8_000_000, page_count=30)
    assert r.escalate
