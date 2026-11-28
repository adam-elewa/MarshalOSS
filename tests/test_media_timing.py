from pathlib import Path

from marshal_oss.asr_escalate import _chunk_segments, _offset_segments, _split_text, use_ai_asr
from marshal_oss.config import Settings
from marshal_oss.db import Database
from marshal_oss.media_info import describe_media, fmt_clock
from marshal_oss.visual import effective_frame_interval


def test_fmt_clock():
    assert fmt_clock(0) == "0:00"
    assert fmt_clock(75.9) == "1:15"
    assert fmt_clock(3725) == "1:02:05"
    assert fmt_clock(None) == "?"


def test_axon_filenames(tmp_path: Path):
    body = tmp_path / "Axon_Body_3_Video_2024-08-09_1505_X60AA001A.mp4"
    body.write_bytes(b"")
    info = describe_media(body, body.name)
    assert info["kind"] == "bodycam"
    assert info["device"] == "Axon Body 3"
    assert info["device_serial"] == "X60AA001A"
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


def test_diarized_windows_offset_and_label():
    one = _offset_segments([(0.0, 600.0, [{"start": 1.0, "end": 2.0, "speaker": "A", "text": "911."}])])
    assert one[0]["speaker"] == "Speaker A"

    two = _offset_segments(
        [
            (0.0, 600.0, [{"start": 1.0, "end": 2.0, "speaker": "A", "text": "Stop."}]),
            (600.0, 900.0, [{"start": 3.0, "end": 4.5, "speaker": "A", "text": "Okay."}]),
        ]
    )
    assert two[0]["speaker"] == "Speaker A, part 1"
    assert (two[1]["start"], two[1]["end"], two[1]["speaker"]) == (603.0, 604.5, "Speaker A, part 2")


def test_chunk_segments_groups_with_times():
    segs = [
        {"start": float(i), "end": i + 0.9, "speaker": "Speaker B", "text": "x" * 500}
        for i in range(6)
    ]
    chunks = _chunk_segments(segs, bates=None, source="m", max_chars=1200)
    assert [len(c["segments"]) for c in chunks] == [2, 2, 2]
    assert (chunks[1]["start_sec"], chunks[1]["end_sec"]) == (2.0, 3.9)
    assert chunks[0]["text"].startswith("Speaker B: ")
    assert chunks[0]["heading"] == "Transcript @0:00–0:01"


def test_use_ai_asr_needs_provider_and_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("MARSHALOSS_OPENAI_API_KEY", raising=False)
    assert not use_ai_asr(Settings(asr_provider="openai", openai_api_key=None))
    assert use_ai_asr(Settings(asr_provider="openai", openai_api_key="k"))
    assert not use_ai_asr(Settings(asr_provider="local", openai_api_key="k"))


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
