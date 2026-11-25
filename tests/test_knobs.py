from pathlib import Path

from marshal_oss.config import Settings
from marshal_oss.knobs import (
    flatten_knob_sections,
    load_pricing,
    load_settings_overrides,
    packaged_knobs_path,
)


def test_packaged_knobs_exist_and_match_defaults():
    path = packaged_knobs_path()
    assert path.is_file()
    overrides = load_settings_overrides()
    # Packaged balanced profile should align with Settings field defaults
    s = Settings()
    assert overrides.get("asr_hq", s.asr_hq) is True
    assert overrides.get("asr_provider", s.asr_provider) == "openai"
    assert overrides.get("embed_backend", s.embed_backend) == "voyage"
    assert overrides.get("visual_max_frames", s.visual_max_frames) == 48


def test_flatten_and_economy_profile(tmp_path: Path, monkeypatch):
    user = tmp_path / "knobs.toml"
    user.write_text(
        """
active_profile = "economy"

[asr]
hq = true

[profiles.economy]
asr_hq = false
visual_max_frames = 8
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("MARSHALOSS_KNOBS_FILE", str(user))
    # Clear dotenv interference for this field
    monkeypatch.delenv("MARSHALOSS_ASR_HQ", raising=False)
    overrides = load_settings_overrides()
    assert overrides["asr_hq"] is False
    assert overrides["visual_max_frames"] == 8


def test_env_wins_over_knobs(tmp_path: Path, monkeypatch):
    user = tmp_path / "knobs.toml"
    user.write_text(
        """
active_profile = "custom"
[asr]
hq = true
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("MARSHALOSS_KNOBS_FILE", str(user))
    monkeypatch.setenv("MARSHALOSS_ASR_HQ", "false")
    s = Settings()
    assert s.asr_hq is False


def test_pricing_loaded():
    pricing = load_pricing()
    assert pricing["openai_transcribe_usd_per_min"] == 0.006
    assert pricing["voyage_law2_usd_per_mTok"] == 0.12


def test_flatten_section_map():
    flat = flatten_knob_sections(
        {"asr": {"hq": False, "provider": "local"}, "visual": {"max_frames": 12}}
    )
    assert flat == {
        "asr_hq": False,
        "asr_provider": "local",
        "visual_max_frames": 12,
    }
