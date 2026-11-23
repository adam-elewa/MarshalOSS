from pathlib import Path

from marshal_oss.asr_escalate import _split_text
from marshal_oss.db import Database
from marshal_oss.media_info import describe_media, fmt_clock
from marshal_oss.visual import effective_frame_interval


def test_fmt_clock():
    assert fmt_clock(0) == "0:00"
    assert fmt_clock(75.9) == "1:15"
    assert fmt_clock(3725) == "1:02:05"
    assert fmt_clock(None) == "?"


def test_axon_filenames(tmp_path: Path):
    body = tmp_path / "Axon_Body_3_Video_2024-08-09_1505_X60AE180D.mp4"
    body.write_bytes(b"")
    info = describe_media(body, body.name)
    assert info["kind"] == "bodycam"
    assert info["device"] == "Axon Body 3"
    assert info["device_serial"] == "X60AE180D"
    assert info["recorded_start"] == "2024-08-09T15:05"

    fleet = tmp_path / "AXON_Fleet_2_IR_Video_2024-08-09_2118.mp4"
    fleet.write_bytes(b"")
    info = describe_media(fleet, fleet.name)
    assert info["kind"] == "dashcam"
    assert info["device"] == "Axon Fleet 2 (IR)"


def test_path_keywords_and_non_media(tmp_path: Path):
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"")
    assert describe_media(clip, "Officer Smith BWC/clip.mp4")["kind"] == "bodycam"
    assert describe_media(clip, "In-Car Video/clip.mp4")["kind"] == "dashcam"
    assert describe_media(clip, "Surveillance/clip.mp4")["kind"] == "video"
    wav = tmp_path / "call.wav"
    wav.write_bytes(b"")
    assert describe_media(wav, "bodycam/call.wav")["kind"] == "audio"
    pdf = tmp_path / "report.pdf"
    pdf.write_bytes(b"")
    assert describe_media(pdf, pdf.name) is None


def test_frame_interval_spreads_long_videos():
    assert effective_frame_interval(120, 5, 48) == 5
    assert effective_frame_interval(3600, 5, 48) == 75
    assert effective_frame_interval(3600, 5, 0) == 5
    assert effective_frame_interval(None, 5, 48) == 5


def test_split_text_keeps_all_words():
    text = " ".join(f"word{i}" for i in range(1000))
    pieces = _split_text(text, 1800)
    assert all(len(p) <= 1800 for p in pieces)
    assert " ".join(pieces) == text


def test_chunk_times_and_segments_roundtrip(tmp_path: Path):
    db = Database(tmp_path / "t.db")
    case = db.upsert_case("demo-case", "Demo", str(tmp_path))
    fid = db.upsert_file(case.id, "a.mp4", "a.mp4", None, "video/mp4", 1, None, "x", status="ready")
    chunks = [
        {
            "text": "Stop. Hands up.",
            "start_sec": 1.0,
            "end_sec": 4.0,
            "segments": [
                {"start": 1.0, "end": 2.0, "text": "Stop."},
                {"start": 2.5, "end": 4.0, "text": "Hands up.", "speaker": "A"},
            ],
        },
        {
            "text": "Officer beside a gray sedan.",
            "start_sec": 0.0,
            "end_sec": 5.0,
            "segments": [{"kind": "visual", "start": 0.0, "end": 5.0, "text": "Officer beside a gray sedan."}],
        },
        {"text": "No timing here."},
    ]
    assert db.replace_chunks(fid, case.id, "a.mp4", chunks) == 3

    segs = db.list_media_segments(fid)
    assert [s["kind"] for s in segs] == ["visual", "speech", "speech"]
    assert db.list_media_segments(fid, kind="speech")[1]["speaker"] == "A"

    hit = db.fts_search(case.id, "hands")[0]
    assert (hit["start_sec"], hit["end_sec"]) == (1.0, 4.0)

    db.replace_chunks(fid, case.id, "a.mp4", [{"text": "re-ingested"}])
    assert db.list_media_segments(fid) == []
    assert db.fts_search(case.id, "hands") == []
    assert len(db.fts_search(case.id, "ingested")) == 1
