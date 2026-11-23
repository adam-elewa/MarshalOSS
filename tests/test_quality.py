from marshal_oss.quality import score_extraction, score_transcript


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


def test_transcript_hallucination_escalates():
    r = score_transcript(
        "thanks for watching",
        file_size=50_000_000,
        duration_sec=600,
    )
    assert r.escalate
    assert any("hallucination" in x or "near_empty" in x for x in r.reasons)


def test_transcript_good_dialogue_ok():
    # Varied depo-like dialogue (not a template loop)
    speakers = ["Rivera", "Chen", "Brooks", "Patel", "Nguyen", "Garcia"]
    topics = [
        "hallway camera footage from Tuesday night",
        "who else was in the stairwell",
        "whether anyone knocked before entering",
        "the time the fire alarm started",
        "what the neighbor said about the bag",
        "how long the argument lasted",
        "whether lights were on in 4B",
        "the description of the second man",
    ]
    lines = []
    for i in range(40):
        who = speakers[i % len(speakers)]
        topic = topics[i % len(topics)]
        lines.append(
            f"Q: Officer {who}, tell us about {topic}. "
            f"A: Around {6 + (i % 6)}:{10 + (i % 40):02d} I saw two people near unit {i + 12}. "
            f"I asked them to wait and called for backup from sector {i % 9}."
        )
    text = " ".join(lines)
    r = score_transcript(text, file_size=5_000_000, duration_sec=120)
    assert not r.escalate, r
    assert r.score >= 0.72


def test_transcript_quiet_video_not_sparse_by_filesize():
    # A large video with light, normal speech (~300 chars/min) is fine.
    text = " ".join(
        f"Unit {i}, I'm outside by door {i + 3}. Did someone bring the keys for car {i * 7}? "
        f"They're on desk {i + 1}, I'll pull around front at {i}:{i * 4:02d}."
        for i in range(7)
    )
    r = score_transcript(text, file_size=100_000_000, duration_sec=200)
    assert not r.escalate, r
    assert not any(x.startswith("sparse_vs_filesize") for x in r.reasons)
