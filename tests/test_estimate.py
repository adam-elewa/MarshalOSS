from pathlib import Path

from marshal_oss.config import Settings
from marshal_oss.estimate import (
    FileTally,
    estimate_costs,
    format_estimate,
)


def test_estimate_ingest_hq_includes_asr_and_voyage():
    tallies = [
        FileTally("a.pdf", "document", size_bytes=2_000_000, pages_est=40),
        FileTally("cam.mp4", "video", size_bytes=50_000_000, duration_sec=600.0),
        FileTally("still.jpg", "image", size_bytes=400_000),
    ]
    settings = Settings(
        asr_provider="openai",
        asr_hq=True,
        ocr_escalate=True,
        visual_analyze=True,
        entity_extract=True,
        embed_backend="voyage",
        visual_frame_interval_sec=5.0,
        visual_max_frames=48,
    )
    est = estimate_costs(tallies, settings, mode="ingest", root_label="/tmp/case")
    stages = {ln.stage for ln in est.lines}
    assert "asr_hq" in stages
    assert "embed" in stages
    assert est.usd_high >= est.usd_low
    assert est.tallies["media_minutes"] == 10.0
    text = format_estimate(est)
    assert "TOTAL" in text
    assert "openai" in text.lower()


def test_estimate_retranscribe_asr_only():
    tallies = [
        FileTally("cam.mp4", "video", duration_sec=120.0),
        FileTally("a.pdf", "document", pages_est=10, size_bytes=1000),
    ]
    settings = Settings(asr_hq=False, asr_provider="openai")
    est = estimate_costs(tallies, settings, mode="retranscribe", root_label="db:x")
    stages = {ln.stage for ln in est.lines}
    assert stages == {"asr_diarize"} or "asr_diarize" in stages
    assert "embed" not in stages
    assert "ocr_escalate" not in stages


def test_estimate_local_asr_zero_api(tmp_path: Path):
    tallies = [FileTally("cam.mp4", "video", duration_sec=60.0)]
    settings = Settings(
        asr_provider="local",
        embed_backend="hash",
        ocr_escalate=False,
        visual_analyze=False,
        entity_extract=False,
    )
    est = estimate_costs(tallies, settings, mode="ingest", root_label=str(tmp_path))
    assert est.usd_high == 0.0
