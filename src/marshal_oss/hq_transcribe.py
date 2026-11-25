"""
High-quality API-only transcription for BWC / long-form noisy audio.

Pipeline (no local GPU required):

  1. Extract 16 kHz mono audio (caller / ffmpeg).
  2. ``gpt-4o-transcribe-diarize`` with ``chunking_strategy="auto"`` —
     OpenAI normalizes loudness and applies server-side VAD, returning
     timestamped speech/speaker regions.
  3. Merge tiny adjacent regions, cut each window, re-transcribe with
     ``gpt-4o-transcribe`` + ``include=["logprobs"]`` and case vocabulary.
  4. Reject empty / repetitive / known-hallucination / low-confidence
     windows. Borderline windows are verified with ``whisper-1``
     ``verbose_json`` so quiet real speech is not discarded solely by
     one score.

Artifacts written under ``artifact_dir`` (when provided):

  - ``raw/``     immutable API JSON payloads
  - ``accepted.json``  segments exposed to viewers / search
  - ``rejected.json``  dropped segments with reason + scores
"""
from __future__ import annotations

import json
import logging
import math
import re
import shutil
import subprocess
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

DIARIZE_MODEL = "gpt-4o-transcribe-diarize"
TRANSCRIBE_MODEL = "gpt-4o-transcribe"
VERIFY_MODEL = "whisper-1"

# Merge / windowing
MIN_SPEECH_SEC = 0.35
MERGE_GAP_SEC = 1.75
MAX_WINDOW_SEC = 60.0
WINDOW_PAD_SEC = 0.20
DIARIZE_MAX_SEC = 1200.0  # model hard-cap is 1400s; stay under
DIARIZE_OVERLAP_SEC = 1.0

# Confidence gates (token avg logprob — higher/less-negative is better)
ACCEPT_AVG_LOGPROB = -0.55
BORDERLINE_AVG_LOGPROB = -1.15
# Whisper verifier gates
VERIFY_MIN_AVG_LOGPROB = -1.0
VERIFY_MAX_NO_SPEECH = 0.65
VERIFY_MAX_COMPRESSION = 2.6

_DEFAULT_PROMPT = (
    "Body-worn camera and emergency radio audio from a Jersey City police "
    "incident. Speakers may include officers, family members, and dispatch. "
    "Use proper names when clear: Washington, Jersey City, Mobile Crisis, "
    "Randolph. Prefer verbatim speech; do not invent dialogue over silence "
    "or noise. Keep filler words when audible."
)

_WHISPER_JUNK_RE = re.compile(r"^[.\s,…\-–—]+$")
_HALLUCINATION_PHRASES = (
    "thanks for watching",
    "thank you for watching",
    "please subscribe",
    "like and subscribe",
    "www.",
    "http://",
    "https://",
    "amara.org",
    "subtitle",
    "transcript by",
    "music playing",
    "[music]",
    "(music)",
    "♪",
)


@dataclass
class HQTranscriptSegment:
    """Timestamped transcript segment with quality metadata."""

    start_sec: float
    end_sec: float
    text: str
    speaker: Optional[str] = None
    avg_logprob: Optional[float] = None
    confidence: Optional[float] = None
    accepted: bool = True
    reject_reason: Optional[str] = None
    source: str = "gpt-4o-transcribe"

    def to_viewer_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "start": round(self.start_sec, 3),
            "end": round(self.end_sec, 3),
            "type": "transcript",
            "text": self.text,
        }
        if self.speaker:
            out["speaker"] = self.speaker
        if self.confidence is not None:
            out["confidence"] = round(self.confidence, 4)
        return out


@dataclass
class SpeechWindow:
    start_sec: float
    end_sec: float
    speaker: Optional[str] = None
    diarize_text: str = ""


@dataclass
class HQTranscriptResult:
    accepted: list[HQTranscriptSegment] = field(default_factory=list)
    rejected: list[HQTranscriptSegment] = field(default_factory=list)
    windows: list[SpeechWindow] = field(default_factory=list)
    artifact_dir: Optional[Path] = None
    model_stack: dict[str, str] = field(default_factory=dict)

    @property
    def segments(self) -> list[HQTranscriptSegment]:
        """Backward-compatible alias — accepted segments only."""
        return self.accepted


def _require_openai():
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise ImportError("openai package required: pip install openai") from exc
    return OpenAI


def _probe_duration_sec(path: Path) -> float:
    result = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=nw=1:nk=1",
            str(path),
        ],
        capture_output=True, text=True, check=True,
    )
    return float(result.stdout.strip())


def _cut_audio_chunk(
    src: Path,
    dest: Path,
    start_sec: float,
    duration_sec: float,
) -> None:
    if not shutil.which("ffmpeg"):
        raise RuntimeError("ffmpeg not found on PATH")
    dest.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-ss", f"{start_sec:.3f}",
            "-t", f"{duration_sec:.3f}",
            "-i", str(src),
            "-vn", "-ar", "16000", "-ac", "1", "-b:a", "48k",
            str(dest),
        ],
        check=True, capture_output=True,
    )


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _serialize_response(response: Any) -> Any:
    if hasattr(response, "model_dump"):
        return response.model_dump()
    if hasattr(response, "to_dict"):
        return response.to_dict()
    return json.loads(json.dumps(response, default=str))


def logprob_to_confidence(avg_logprob: Optional[float]) -> Optional[float]:
    """Map avg token logprob into a rough 0–1 confidence."""
    if avg_logprob is None:
        return None
    # Soft map: -0.2 ≈ 0.95, -0.55 ≈ 0.80, -1.15 ≈ 0.55, -2.0 ≈ 0.25
    return max(0.0, min(1.0, 1.0 / (1.0 + math.exp(-2.2 * (avg_logprob + 0.7)))))


def mean_token_logprob(logprobs: Any) -> Optional[float]:
    if not logprobs:
        return None
    values: list[float] = []
    for item in logprobs:
        lp = getattr(item, "logprob", None)
        if lp is None and isinstance(item, dict):
            lp = item.get("logprob")
        if lp is not None:
            values.append(float(lp))
    if not values:
        return None
    return sum(values) / len(values)


def is_junk_text(text: str) -> bool:
    t = (text or "").strip()
    if not t or _WHISPER_JUNK_RE.fullmatch(t):
        return True
    # Extremely short non-word noise
    if len(re.sub(r"\W+", "", t)) < 2:
        return True
    return False


def is_hallucination_phrase(text: str) -> bool:
    low = (text or "").strip().lower()
    if not low:
        return False
    return any(p in low for p in _HALLUCINATION_PHRASES)


def is_repetitive_text(text: str) -> bool:
    """Detect looped / high-compression nonsense."""
    t = re.sub(r"\s+", " ", (text or "").strip().lower())
    if len(t) < 24:
        return False
    words = t.split()
    if len(words) >= 8:
        unique = len(set(words))
        if unique / len(words) < 0.35:
            return True
        # Same 2–4 gram looping
        for n in (2, 3, 4):
            if len(words) < n * 3:
                continue
            gram = " ".join(words[:n])
            repeats = sum(
                1
                for i in range(0, len(words) - n + 1, n)
                if " ".join(words[i : i + n]) == gram
            )
            if repeats >= 3:
                return True
    # Character-level loops: "hahahaha", "....."
    if re.search(r"(.)\1{7,}", t):
        return True
    if re.search(r"(.{2,6})\1{4,}", t):
        return True
    return False


def merge_speech_windows(
    windows: list[SpeechWindow],
    *,
    merge_gap_sec: float = MERGE_GAP_SEC,
    max_window_sec: float = MAX_WINDOW_SEC,
    cross_speaker_gap_sec: float = 0.85,
) -> list[SpeechWindow]:
    """
    Merge adjacent diarize regions into ASR-sized windows.

    Same-speaker regions merge across larger gaps; different speakers still
    merge when nearly contiguous so BWC crosstalk does not explode into
    hundreds of tiny API calls.
    """
    if not windows:
        return []
    ordered = sorted(windows, key=lambda w: (w.start_sec, w.end_sec))
    merged: list[SpeechWindow] = [SpeechWindow(
        start_sec=ordered[0].start_sec,
        end_sec=ordered[0].end_sec,
        speaker=ordered[0].speaker,
        diarize_text=ordered[0].diarize_text,
    )]
    for win in ordered[1:]:
        prev = merged[-1]
        same_speaker = (prev.speaker or "") == (win.speaker or "")
        gap = win.start_sec - prev.end_sec
        duration_if_merged = win.end_sec - prev.start_sec
        allowed_gap = merge_gap_sec if same_speaker else cross_speaker_gap_sec
        if gap <= allowed_gap and duration_if_merged <= max_window_sec:
            prev.end_sec = max(prev.end_sec, win.end_sec)
            if not same_speaker and prev.speaker and win.speaker and prev.speaker != win.speaker:
                prev.speaker = "mixed"
            if win.diarize_text:
                prev.diarize_text = (
                    f"{prev.diarize_text} {win.diarize_text}".strip()
                    if prev.diarize_text else win.diarize_text
                )
        else:
            merged.append(SpeechWindow(
                start_sec=win.start_sec,
                end_sec=win.end_sec,
                speaker=win.speaker,
                diarize_text=win.diarize_text,
            ))
    return merged


def _diarize_one_file(
    client,
    audio_path: Path,
    *,
    language: str | None,
    time_offset: float = 0.0,
) -> tuple[Any, list[SpeechWindow]]:
    """Diarize a single audio file/chunk; shift timestamps by ``time_offset``."""
    kwargs: dict[str, Any] = dict(
        model=DIARIZE_MODEL,
        file=audio_path.open("rb"),
        response_format="diarized_json",
        chunking_strategy="auto",
    )
    if language:
        kwargs["language"] = language
    try:
        response = client.audio.transcriptions.create(**kwargs)
    finally:
        kwargs["file"].close()

    windows: list[SpeechWindow] = []
    for seg in (getattr(response, "segments", None) or []):
        start = float(getattr(seg, "start", 0.0) or 0.0) + time_offset
        end = float(getattr(seg, "end", start - time_offset) or (start - time_offset)) + time_offset
        text = (getattr(seg, "text", "") or "").strip()
        speaker = getattr(seg, "speaker", None) or None
        if end - start < MIN_SPEECH_SEC and not text:
            continue
        if end <= start:
            continue
        windows.append(SpeechWindow(
            start_sec=start,
            end_sec=end,
            speaker=str(speaker) if speaker else None,
            diarize_text=text,
        ))
    return response, windows


def diarize_speech_windows(
    client,
    audio_path: Path,
    *,
    language: str | None = "en",
    artifact_dir: Optional[Path] = None,
) -> list[SpeechWindow]:
    """
    Run server-VAD diarization; return speech windows with speakers.

    Long files are split under ``DIARIZE_MAX_SEC`` because
    ``gpt-4o-transcribe-diarize`` rejects audio longer than ~1400s.
    """
    duration = _probe_duration_sec(audio_path)
    raw_parts: list[Any] = []
    windows: list[SpeechWindow] = []

    if duration <= DIARIZE_MAX_SEC + 1:
        response, windows = _diarize_one_file(
            client, audio_path, language=language, time_offset=0.0,
        )
        raw_parts.append(_serialize_response(response))
    else:
        with tempfile.TemporaryDirectory(prefix="marshal_diarize_") as td:
            td_path = Path(td)
            t = 0.0
            part = 0
            while t < duration - 0.05:
                part += 1
                dur = min(DIARIZE_MAX_SEC, duration - t)
                chunk = td_path / f"diarize_{part:03d}.mp3"
                _cut_audio_chunk(audio_path, chunk, t, dur)
                response, part_windows = _diarize_one_file(
                    client, chunk, language=language, time_offset=t,
                )
                raw_parts.append({
                    "offset_sec": t,
                    "duration_sec": dur,
                    "response": _serialize_response(response),
                })
                windows.extend(part_windows)
                # Advance with small overlap so boundary speech is not lost
                if t + dur >= duration:
                    break
                t = t + dur - DIARIZE_OVERLAP_SEC

    if artifact_dir is not None:
        payload: Any = raw_parts[0] if len(raw_parts) == 1 else {"parts": raw_parts}
        _write_json(artifact_dir / "raw" / "diarize.json", payload)

    return merge_speech_windows(windows)


def _transcribe_window_gpt4o(
    client,
    chunk_path: Path,
    *,
    language: str | None,
    prompt: str,
) -> tuple[str, Optional[float]]:
    kwargs: dict[str, Any] = dict(
        model=TRANSCRIBE_MODEL,
        file=chunk_path.open("rb"),
        response_format="json",
        include=["logprobs"],
        prompt=prompt,
        temperature=0,
    )
    if language:
        kwargs["language"] = language
    try:
        response = client.audio.transcriptions.create(**kwargs)
    finally:
        kwargs["file"].close()
    text = (getattr(response, "text", "") or "").strip()
    avg_lp = mean_token_logprob(getattr(response, "logprobs", None))
    return text, avg_lp


def _verify_with_whisper(
    client,
    chunk_path: Path,
    *,
    language: str | None,
) -> tuple[bool, Optional[float], Optional[float], str]:
    """
    Return (keep, avg_logprob, no_speech_prob, text) using whisper-1 metrics.
    """
    kwargs: dict[str, Any] = dict(
        model=VERIFY_MODEL,
        file=chunk_path.open("rb"),
        response_format="verbose_json",
        timestamp_granularities=["segment"],
        temperature=0,
    )
    if language:
        kwargs["language"] = language
    try:
        response = client.audio.transcriptions.create(**kwargs)
    finally:
        kwargs["file"].close()

    segs = list(getattr(response, "segments", None) or [])
    if not segs:
        text = (getattr(response, "text", "") or "").strip()
        if not text or is_junk_text(text) or is_hallucination_phrase(text):
            return False, None, None, text
        return True, None, None, text

    # Aggregate across segments weighted by duration
    total_dur = 0.0
    weighted_lp = 0.0
    max_no_speech = 0.0
    max_compression = 0.0
    texts: list[str] = []
    for seg in segs:
        text = (getattr(seg, "text", "") or "").strip()
        if text:
            texts.append(text)
        start = float(getattr(seg, "start", 0.0) or 0.0)
        end = float(getattr(seg, "end", start) or start)
        dur = max(0.01, end - start)
        avg_lp = getattr(seg, "avg_logprob", None)
        if avg_lp is not None:
            weighted_lp += float(avg_lp) * dur
            total_dur += dur
        nsp = getattr(seg, "no_speech_prob", None)
        if nsp is not None:
            max_no_speech = max(max_no_speech, float(nsp))
        cr = getattr(seg, "compression_ratio", None)
        if cr is not None:
            max_compression = max(max_compression, float(cr))

    joined = " ".join(texts).strip()
    avg_lp = (weighted_lp / total_dur) if total_dur else None
    if is_junk_text(joined) or is_hallucination_phrase(joined) or is_repetitive_text(joined):
        return False, avg_lp, max_no_speech, joined
    if avg_lp is not None and avg_lp < VERIFY_MIN_AVG_LOGPROB:
        return False, avg_lp, max_no_speech, joined
    if max_no_speech > VERIFY_MAX_NO_SPEECH:
        return False, avg_lp, max_no_speech, joined
    if max_compression > VERIFY_MAX_COMPRESSION:
        return False, avg_lp, max_no_speech, joined
    return True, avg_lp, max_no_speech, joined


def classify_segment_quality(
    text: str,
    avg_logprob: Optional[float],
) -> tuple[str, Optional[str]]:
    """
    Return (decision, reject_reason) where decision is
    accept | reject | borderline.
    """
    if is_junk_text(text):
        return "reject", "empty_or_junk"
    if is_hallucination_phrase(text):
        return "reject", "hallucination_phrase"
    if is_repetitive_text(text):
        return "reject", "repetitive"
    if avg_logprob is None:
        # No score — keep but mark as borderline for verifier
        return "borderline", None
    if avg_logprob < BORDERLINE_AVG_LOGPROB:
        return "reject", "low_logprob"
    if avg_logprob < ACCEPT_AVG_LOGPROB:
        return "borderline", None
    return "accept", None



_FALLBACK_CHUNK_SEC = 600.0


def _whisper_verbose_segments(
    client,
    audio_path: Path,
    *,
    language: str | None,
    time_offset: float = 0.0,
) -> list[tuple[float, float, str]]:
    """Return (start, end, text) from whisper-1 verbose_json for one file/chunk."""
    kwargs: dict[str, Any] = dict(
        model=VERIFY_MODEL,
        file=audio_path.open("rb"),
        response_format="verbose_json",
        timestamp_granularities=["segment"],
        temperature=0,
    )
    if language:
        kwargs["language"] = language
    try:
        response = client.audio.transcriptions.create(**kwargs)
    finally:
        kwargs["file"].close()

    out: list[tuple[float, float, str]] = []
    for seg in list(getattr(response, "segments", None) or []):
        text = (getattr(seg, "text", "") or "").strip()
        start = float(getattr(seg, "start", 0.0) or 0.0) + time_offset
        end = float(getattr(seg, "end", start - time_offset) or (start - time_offset)) + time_offset
        if text:
            out.append((start, end, text))
    if not out:
        text = (getattr(response, "text", "") or "").strip()
        if text:
            out.append((time_offset, time_offset + 1.0, text))
    return out


def _fallback_whisper_segments(
    client,
    audio_path: Path,
    *,
    language: str | None,
    artifact_dir: Optional[Path] = None,
) -> tuple[list[HQTranscriptSegment], list[HQTranscriptSegment]]:
    """
    When server VAD finds no speech windows, fall back to whisper-1
    verbose segments with confidence filtering. Protects against VAD
    false-negatives on quiet BWC speech.
    """
    duration = _probe_duration_sec(audio_path)
    legacy: list[tuple[float, float, str]] = []
    if duration <= _FALLBACK_CHUNK_SEC + 1:
        legacy = _whisper_verbose_segments(client, audio_path, language=language)
    else:
        with tempfile.TemporaryDirectory(prefix="marshaloss_hq_fb_chunk_") as td:
            td_path = Path(td)
            t = 0.0
            part = 0
            while t < duration - 0.05:
                part += 1
                dur = min(_FALLBACK_CHUNK_SEC, duration - t)
                chunk = td_path / f"fb_chunk_{part:03d}.mp3"
                _cut_audio_chunk(audio_path, chunk, t, dur)
                legacy.extend(
                    _whisper_verbose_segments(
                        client, chunk, language=language, time_offset=t
                    )
                )
                t += dur

    accepted: list[HQTranscriptSegment] = []
    rejected: list[HQTranscriptSegment] = []

    with tempfile.TemporaryDirectory(prefix="marshaloss_hq_fb_") as td:
        td_path = Path(td)
        for idx, (start, end, text) in enumerate(legacy):
            text = (text or "").strip()
            if end <= start:
                end = start + 1.0
            decision, reason = classify_segment_quality(text, None)
            chunk = td_path / f"fb_{idx:04d}.mp3"
            avg_lp = None
            try:
                _cut_audio_chunk(audio_path, chunk, max(0.0, start), max(0.05, end - start))
                keep, avg_lp, _nsp, v_text = _verify_with_whisper(
                    client, chunk, language=language,
                )
                if decision in ("accept", "borderline"):
                    final = (v_text or text).strip()
                    # Fallback path is stricter: require verify keep + decent logprob
                    # and enough lexical content (drops beeps / one-word noise).
                    words = re.findall(r"[A-Za-z']+", final)
                    if (
                        keep
                        and not is_junk_text(final)
                        and not is_hallucination_phrase(final)
                        and not is_repetitive_text(final)
                        and avg_lp is not None
                        and avg_lp >= ACCEPT_AVG_LOGPROB
                        and len(words) >= 2
                    ):
                        decision, reason = "accept", None
                        text = final
                    else:
                        decision, reason = "reject", reason or "fallback_verify_reject"
            except Exception as exc:
                if decision != "reject":
                    decision, reason = "reject", f"fallback_error:{exc}"

            out = HQTranscriptSegment(
                start_sec=start,
                end_sec=end,
                text=text,
                avg_logprob=avg_lp,
                confidence=logprob_to_confidence(avg_lp),
                accepted=(decision == "accept"),
                reject_reason=reason,
                source="whisper-1-fallback",
            )
            (accepted if out.accepted else rejected).append(out)

    if artifact_dir is not None:
        _write_json(artifact_dir / "raw" / "fallback_whisper.json", {
            "legacy_count": len(legacy),
            "accepted": [asdict(s) for s in accepted],
            "rejected": [asdict(s) for s in rejected],
        })
    return accepted, rejected


def transcribe_audio_hq(
    audio_path: Path,
    openai_api_key: str,
    *,
    language: str | None = "en",
    prompt: str = _DEFAULT_PROMPT,
    artifact_dir: Optional[Path] = None,
    progress_print=None,
) -> HQTranscriptResult:
    """
    High-quality API transcription with server VAD + confidence gating.

    Cost is higher than a single whisper-1 pass (diarize + per-window
    gpt-4o-transcribe + occasional whisper verify), but substantially
    reduces silence/noise hallucinations for BWC review.
    """
    OpenAI = _require_openai()
    client = OpenAI(api_key=openai_api_key)
    audio_path = Path(audio_path)
    if not audio_path.exists():
        raise FileNotFoundError(audio_path)

    if artifact_dir is not None:
        artifact_dir = Path(artifact_dir)
        artifact_dir.mkdir(parents=True, exist_ok=True)
        (artifact_dir / "raw").mkdir(exist_ok=True)

    def log(msg: str) -> None:
        if progress_print:
            progress_print(msg)
        else:
            logger.info(msg)

    duration = _probe_duration_sec(audio_path)
    log(f"HQ transcribe {audio_path.name} ({duration/60:.1f} min) — diarize/VAD…")

    windows = diarize_speech_windows(
        client, audio_path, language=language, artifact_dir=artifact_dir,
    )
    log(f"  {len(windows)} speech window(s) after merge")

    accepted: list[HQTranscriptSegment] = []
    rejected: list[HQTranscriptSegment] = []
    raw_window_dir = (artifact_dir / "raw" / "windows") if artifact_dir else None
    if raw_window_dir:
        raw_window_dir.mkdir(parents=True, exist_ok=True)

    if not windows:
        log("  diarize found no speech — whisper-1 confidence fallback")
        fb_acc, fb_rej = _fallback_whisper_segments(
            client, audio_path, language=language, artifact_dir=artifact_dir,
        )
        accepted.extend(fb_acc)
        rejected.extend(fb_rej)
        windows = []  # skip per-window loop

    with tempfile.TemporaryDirectory(prefix="marshal_hq_") as td:
        td_path = Path(td)
        for idx, win in enumerate(windows):
            start = max(0.0, win.start_sec - WINDOW_PAD_SEC)
            end = min(duration, win.end_sec + WINDOW_PAD_SEC)
            dur = max(0.05, end - start)
            chunk_path = td_path / f"win_{idx:04d}.mp3"
            try:
                _cut_audio_chunk(audio_path, chunk_path, start, dur)
            except Exception as exc:
                rejected.append(HQTranscriptSegment(
                    start_sec=win.start_sec,
                    end_sec=win.end_sec,
                    text=win.diarize_text,
                    speaker=win.speaker,
                    accepted=False,
                    reject_reason=f"ffmpeg_cut_failed:{exc}",
                    source="diarize",
                ))
                continue

            try:
                text, avg_lp = _transcribe_window_gpt4o(
                    client, chunk_path, language=language, prompt=prompt,
                )
            except Exception as exc:
                # Fall back to diarize text if second pass fails
                text, avg_lp = win.diarize_text, None
                log(f"  window {idx} gpt-4o failed ({exc}); using diarize text")

            if raw_window_dir is not None:
                _write_json(raw_window_dir / f"{idx:04d}.json", {
                    "start_sec": win.start_sec,
                    "end_sec": win.end_sec,
                    "speaker": win.speaker,
                    "diarize_text": win.diarize_text,
                    "gpt4o_text": text,
                    "avg_logprob": avg_lp,
                })

            decision, reason = classify_segment_quality(text, avg_lp)
            conf = logprob_to_confidence(avg_lp)

            if decision == "borderline":
                keep, v_lp, v_nsp, v_text = _verify_with_whisper(
                    client, chunk_path, language=language,
                )
                if keep:
                    # Prefer gpt-4o text when non-empty; else verifier text
                    final_text = text if text and not is_junk_text(text) else v_text
                    if is_junk_text(final_text):
                        decision, reason = "reject", "borderline_empty_after_verify"
                    else:
                        decision, reason = "accept", None
                        if avg_lp is None and v_lp is not None:
                            avg_lp = v_lp
                            conf = logprob_to_confidence(avg_lp)
                        text = final_text
                else:
                    decision, reason = "reject", "borderline_failed_verify"
                    if not text:
                        text = v_text or win.diarize_text

            seg = HQTranscriptSegment(
                start_sec=win.start_sec,
                end_sec=win.end_sec,
                text=(text or "").strip(),
                speaker=win.speaker,
                avg_logprob=avg_lp,
                confidence=conf,
                accepted=(decision == "accept"),
                reject_reason=reason,
                source="gpt-4o-transcribe",
            )
            if seg.accepted:
                accepted.append(seg)
            else:
                rejected.append(seg)

    accepted.sort(key=lambda s: (s.start_sec, s.end_sec))
    rejected.sort(key=lambda s: (s.start_sec, s.end_sec))

    result = HQTranscriptResult(
        accepted=accepted,
        rejected=rejected,
        windows=windows,
        artifact_dir=artifact_dir,
        model_stack={
            "diarize": DIARIZE_MODEL,
            "transcribe": TRANSCRIBE_MODEL,
            "verify": VERIFY_MODEL,
            "chunking_strategy": "auto",
        },
    )

    if artifact_dir is not None:
        _write_json(artifact_dir / "accepted.json", [asdict(s) for s in accepted])
        _write_json(artifact_dir / "rejected.json", [asdict(s) for s in rejected])
        _write_json(artifact_dir / "windows.json", [asdict(w) for w in windows])
        _write_json(artifact_dir / "summary.json", {
            "audio": str(audio_path),
            "duration_sec": duration,
            "windows": len(windows),
            "accepted": len(accepted),
            "rejected": len(rejected),
            "model_stack": result.model_stack,
        })

    log(f"  accepted {len(accepted)} / rejected {len(rejected)}")
    return result


def hq_segments_to_minute_text(
    segments: list[HQTranscriptSegment],
) -> dict[int, str]:
    """Group accepted HQ segments into 1-minute buckets for marshal pages."""
    by_minute: dict[int, list[str]] = {}
    for seg in segments:
        if not seg.accepted or not seg.text.strip():
            continue
        minute = int(seg.start_sec // 60)
        by_minute.setdefault(minute, []).append(seg.text.strip())
    return {m: " ".join(texts) for m, texts in sorted(by_minute.items())}


def hq_result_to_chunks(
    result: HQTranscriptResult,
    *,
    bates: str | None = None,
) -> list[dict[str, Any]]:
    """Convert accepted HQ segments into MarshalOSS ingest chunk dicts."""
    from .asr_escalate import _chunk_segments
    from .media_info import fmt_clock

    segs: list[dict[str, Any]] = []
    for s in result.accepted:
        text = (s.text or "").strip()
        if not text:
            continue
        speaker = None
        if s.speaker:
            sp = str(s.speaker)
            speaker = sp if sp.lower().startswith("speaker") else f"Speaker {sp}"
        item: dict[str, Any] = {
            "start": float(s.start_sec),
            "end": float(s.end_sec),
            "text": text,
            "speaker": speaker,
        }
        if s.confidence is not None:
            item["confidence"] = float(s.confidence)
        segs.append(item)
    if not segs:
        return []
    source = (result.model_stack or {}).get("transcribe") or TRANSCRIBE_MODEL
    chunks = _chunk_segments(segs, bates=bates, source=f"hq:{source}")
    for ch in chunks:
        ch.setdefault("meta", {})
        ch["meta"]["hq"] = True
        ch["meta"]["model_stack"] = dict(result.model_stack or {})
        if ch.get("start_sec") is not None and ch.get("end_sec") is not None:
            ch["heading"] = (
                f"HQ transcript @{fmt_clock(ch['start_sec'])}–{fmt_clock(ch['end_sec'])}"
            )
    return chunks


def ai_transcribe_file_hq(
    path: Path,
    settings: Any,
    *,
    bates: str | None = None,
    artifact_dir: Path | None = None,
    log: Any = None,
) -> list[dict[str, Any]]:
    """Run HQ stack and return MarshalOSS transcript chunks."""
    from .asr_escalate import _openai_key

    key = _openai_key(settings)
    if not key:
        raise RuntimeError("OPENAI_API_KEY required for HQ transcription")
    prompt = getattr(settings, "asr_hq_prompt", None) or _DEFAULT_PROMPT
    result = transcribe_audio_hq(
        path,
        key,
        language=getattr(settings, "asr_language", None) or "en",
        prompt=prompt,
        artifact_dir=artifact_dir,
        progress_print=log,
    )
    return hq_result_to_chunks(result, bates=bates)
