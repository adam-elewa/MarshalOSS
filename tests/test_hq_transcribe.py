"""Tests for HQ transcription quality gating (no live API calls)."""
from __future__ import annotations

from types import SimpleNamespace
from pathlib import Path

import pytest

from marshal_oss import hq_transcribe as hq


def test_junk_and_hallucination_detection():
    assert hq.is_junk_text("")
    assert hq.is_junk_text("   ")
    assert hq.is_junk_text(". . . .")
    assert hq.is_junk_text("…")
    assert hq.is_hallucination_phrase("Thanks for watching!")
    assert hq.is_hallucination_phrase("please subscribe to our channel")
    assert not hq.is_junk_text("Andrew, open the door")
    assert not hq.is_hallucination_phrase("Thank you, officer")


def test_repetitive_text_detection():
    assert hq.is_repetitive_text("go go go go go go go go go go")
    assert hq.is_repetitive_text("ha" * 20)
    assert not hq.is_repetitive_text("He'll kill himself first? No, no, no.")


def test_merge_speech_windows_same_speaker():
    windows = [
        hq.SpeechWindow(0.0, 1.0, "A", "hi"),
        hq.SpeechWindow(1.2, 2.0, "A", "there"),
        hq.SpeechWindow(5.0, 6.0, "B", "hello"),
    ]
    merged = hq.merge_speech_windows(windows)
    assert len(merged) == 2
    assert merged[0].start_sec == 0.0
    assert merged[0].end_sec == 2.0
    assert "hi" in merged[0].diarize_text and "there" in merged[0].diarize_text
    assert merged[1].speaker == "B"


def test_merge_respects_max_window():
    windows = [
        hq.SpeechWindow(0.0, 80.0, "A", "a"),
        hq.SpeechWindow(80.2, 100.0, "A", "b"),  # would exceed 90s
    ]
    merged = hq.merge_speech_windows(windows, max_window_sec=90.0)
    assert len(merged) == 2


def test_classify_segment_quality_thresholds():
    assert hq.classify_segment_quality("thanks for watching", -0.1)[0] == "reject"
    assert hq.classify_segment_quality("Real speech here", -0.2)[0] == "accept"
    assert hq.classify_segment_quality("Quiet maybe", -0.8)[0] == "borderline"
    assert hq.classify_segment_quality("Barely anything", -1.5)[0] == "reject"
    decision, reason = hq.classify_segment_quality(". . .", None)
    assert decision == "reject"
    assert reason == "empty_or_junk"


def test_mean_token_logprob_and_confidence():
    tokens = [
        SimpleNamespace(logprob=-0.1),
        SimpleNamespace(logprob=-0.3),
        SimpleNamespace(logprob=-0.2),
    ]
    avg = hq.mean_token_logprob(tokens)
    assert avg == pytest.approx(-0.2)
    conf = hq.logprob_to_confidence(avg)
    assert conf is not None and 0.7 < conf < 1.0
    assert hq.mean_token_logprob([]) is None
    assert hq.logprob_to_confidence(None) is None


def test_hq_segments_to_minute_text():
    segs = [
        hq.HQTranscriptSegment(10, 20, "one", accepted=True),
        hq.HQTranscriptSegment(70, 80, "two", accepted=True),
        hq.HQTranscriptSegment(75, 90, "nope", accepted=False),
    ]
    by_min = hq.hq_segments_to_minute_text(segs)
    assert by_min[0] == "one"
    assert by_min[1] == "two"
    assert 2 not in by_min


def test_viewer_dict_includes_confidence():
    seg = hq.HQTranscriptSegment(
        1.5, 3.0, "hello", speaker="SPEAKER_0",
        confidence=0.91, accepted=True,
    )
    d = seg.to_viewer_dict()
    assert d["type"] == "transcript"
    assert d["start"] == 1.5
    assert d["speaker"] == "SPEAKER_0"
    assert d["confidence"] == 0.91


def test_diarize_speech_windows_parses_segments(tmp_path, monkeypatch):
    audio = tmp_path / "a.mp3"
    audio.write_bytes(b"x")
    monkeypatch.setattr(hq, "_probe_duration_sec", lambda _p: 12.0)

    class FakeClient:
        class audio:
            class transcriptions:
                @staticmethod
                def create(**kwargs):
                    assert kwargs["model"] == hq.DIARIZE_MODEL
                    assert kwargs["chunking_strategy"] == "auto"
                    return SimpleNamespace(segments=[
                        SimpleNamespace(start=0.0, end=1.0, text="hi", speaker="A"),
                        SimpleNamespace(start=1.1, end=2.0, text="there", speaker="A"),
                        SimpleNamespace(start=5.0, end=5.1, text="", speaker="B"),  # tiny empty skip if < min
                    ])

    # tiny empty 0.1s with no text should be skipped by MIN_SPEECH_SEC
    windows = hq.diarize_speech_windows(FakeClient(), audio, language="en")
    assert len(windows) == 1
    assert windows[0].start_sec == 0.0
    assert windows[0].end_sec == 2.0


def test_transcribe_audio_hq_filters_and_accepts(tmp_path, monkeypatch):
    audio = tmp_path / "clip.mp3"
    audio.write_bytes(b"fake")
    art = tmp_path / "artifacts"

    monkeypatch.setattr(hq, "_probe_duration_sec", lambda _p: 12.0)
    monkeypatch.setattr(hq, "_cut_audio_chunk", lambda *a, **k: Path(a[1]).write_bytes(b"c"))
    monkeypatch.setattr(
        hq,
        "diarize_speech_windows",
        lambda *a, **k: [
            hq.SpeechWindow(0.0, 2.0, "A", "diarize hi"),
            hq.SpeechWindow(3.0, 5.0, "A", "thanks for watching"),
            hq.SpeechWindow(6.0, 8.0, "B", "quiet"),
        ],
    )

    def fake_gpt4o(client, chunk_path, *, language, prompt):
        # Map by window order via call count
        fake_gpt4o.n += 1
        if fake_gpt4o.n == 1:
            return "Officer at the door", -0.2
        if fake_gpt4o.n == 2:
            return "Thanks for watching", -0.1
        return "mmm", -0.9  # borderline

    fake_gpt4o.n = 0
    monkeypatch.setattr(hq, "_transcribe_window_gpt4o", fake_gpt4o)

    def fake_verify(client, chunk_path, *, language):
        # Borderline quiet window — reject as no-speech
        return False, -1.2, 0.9, "mmm"

    monkeypatch.setattr(hq, "_verify_with_whisper", fake_verify)

    class FakeOpenAI:
        def __init__(self, api_key):
            self.api_key = api_key

    monkeypatch.setitem(
        __import__("sys").modules,
        "openai",
        SimpleNamespace(OpenAI=FakeOpenAI),
    )

    result = hq.transcribe_audio_hq(audio, "sk-test", artifact_dir=art)
    assert len(result.accepted) == 1
    assert result.accepted[0].text == "Officer at the door"
    assert len(result.rejected) == 2
    reasons = {r.reject_reason for r in result.rejected}
    assert "hallucination_phrase" in reasons
    assert "borderline_failed_verify" in reasons
    assert (art / "accepted.json").exists()
    assert (art / "rejected.json").exists()
    assert (art / "summary.json").exists()
