"""
Load annotated cost/quality knobs from TOML.

Precedence (highest wins):
  1. Environment ``MARSHALOSS_*`` / dotenv (handled by Settings)
  2. User file: ``$MARSHALOSS_KNOBS_FILE`` or ``~/.marshaloss/knobs.toml``
  3. Packaged ``marshal_oss/knobs.toml``
  4. Hardcoded Settings field defaults

``pricing`` / ``estimate`` / ``profiles`` / ``meta`` tables are not Settings
fields — use :func:`load_pricing` and :func:`load_estimate_assumptions`.
"""
from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Any

# Section key → Settings field name
_SECTION_MAP: dict[str, dict[str, str]] = {
    "asr": {
        "provider": "asr_provider",
        "model": "asr_model",
        "hq": "asr_hq",
        "language": "asr_language",
        "escalate": "asr_escalate",
        "escalate_min_score": "asr_escalate_min_score",
        "max_windows": "asr_max_windows",
        "escalate_prompt": "asr_escalate_prompt",
        "hq_prompt": "asr_hq_prompt",
    },
    "ocr": {
        "escalate": "ocr_escalate",
        "escalate_provider": "ocr_escalate_provider",
        "escalate_openai_model": "ocr_escalate_openai_model",
        "escalate_anthropic_model": "ocr_escalate_anthropic_model",
        "escalate_max_pages": "ocr_escalate_max_pages",
        "escalate_min_score": "ocr_escalate_min_score",
    },
    "visual": {
        "analyze": "visual_analyze",
        "analyze_images": "visual_analyze_images",
        "analyze_video": "visual_analyze_video",
        "frame_interval_sec": "visual_frame_interval_sec",
        "max_frames": "visual_max_frames",
        "skip_if_ocr_chars": "visual_skip_if_ocr_chars",
    },
    "entities": {
        "extract": "entity_extract",
        "extract_provider": "entity_extract_provider",
        "extract_openai_model": "entity_extract_openai_model",
        "extract_anthropic_model": "entity_extract_anthropic_model",
        "extract_max_chars": "entity_extract_max_chars",
        "extract_segment_chars": "entity_extract_segment_chars",
    },
    "embed": {
        "backend": "embed_backend",
        "model": "embed_model",
    },
    "ingest": {
        "workers": "ingest_workers",
        "expand_zips": "expand_zips",
        "expand_zip_max_depth": "expand_zip_max_depth",
    },
}

_NON_SETTINGS_TABLES = frozenset({"pricing", "estimate", "profiles", "meta"})


def packaged_knobs_path() -> Path:
    return Path(__file__).with_name("knobs.toml")


def user_knobs_path() -> Path | None:
    override = os.environ.get("MARSHALOSS_KNOBS_FILE", "").strip()
    if override:
        return Path(override).expanduser()
    default = Path.home() / ".marshaloss" / "knobs.toml"
    return default if default.is_file() else None


def _read_toml(path: Path) -> dict[str, Any]:
    with path.open("rb") as f:
        return tomllib.load(f)


def flatten_knob_sections(data: dict[str, Any]) -> dict[str, Any]:
    """Map nested [asr]/[ocr]/… tables into flat Settings kwargs."""
    out: dict[str, Any] = {}
    for section, mapping in _SECTION_MAP.items():
        block = data.get(section)
        if not isinstance(block, dict):
            continue
        for short, field in mapping.items():
            if short in block:
                out[field] = block[short]
    return out


def _apply_profile(data: dict[str, Any], flat: dict[str, Any]) -> dict[str, Any]:
    profile = str(data.get("active_profile") or "balanced").strip().lower()
    if profile in ("", "balanced", "custom"):
        return flat
    profiles = data.get("profiles")
    if not isinstance(profiles, dict):
        return flat
    overlay = profiles.get(profile)
    if not isinstance(overlay, dict):
        return flat
    merged = dict(flat)
    for k, v in overlay.items():
        if k.startswith("_"):
            continue
        merged[k] = v
    return merged


def load_raw_knobs() -> tuple[dict[str, Any], list[str]]:
    """
    Merge packaged + user TOML. Returns (merged_dict, source_paths).
    User keys override packaged keys at the top level / table level.
    """
    sources: list[str] = []
    merged: dict[str, Any] = {}

    packaged = packaged_knobs_path()
    if packaged.is_file():
        merged = _read_toml(packaged)
        sources.append(str(packaged))

    user = user_knobs_path()
    if user is not None and user.is_file():
        user_data = _read_toml(user)
        sources.append(str(user))
        for key, val in user_data.items():
            if isinstance(val, dict) and isinstance(merged.get(key), dict):
                merged[key] = {**merged[key], **val}
            else:
                merged[key] = val

    return merged, sources


def load_settings_overrides() -> dict[str, Any]:
    """Flat kwargs suitable for ``Settings(**overrides)`` before env apply."""
    raw, _ = load_raw_knobs()
    flat = flatten_knob_sections(raw)
    return _apply_profile(raw, flat)


def load_pricing() -> dict[str, float]:
    raw, _ = load_raw_knobs()
    block = raw.get("pricing") or {}
    out: dict[str, float] = {}
    if isinstance(block, dict):
        for k, v in block.items():
            try:
                out[k] = float(v)
            except (TypeError, ValueError):
                continue
    return out


def load_estimate_assumptions() -> dict[str, float]:
    raw, _ = load_raw_knobs()
    block = raw.get("estimate") or {}
    out: dict[str, float] = {}
    if isinstance(block, dict):
        for k, v in block.items():
            try:
                out[k] = float(v)
            except (TypeError, ValueError):
                continue
    return out


def knobs_summary() -> dict[str, Any]:
    """Human/LLM-oriented snapshot of knob sources and active profile."""
    raw, sources = load_raw_knobs()
    return {
        "active_profile": raw.get("active_profile", "balanced"),
        "sources": sources,
        "user_knobs_path": str(user_knobs_path()) if user_knobs_path() else None,
        "packaged_knobs_path": str(packaged_knobs_path()),
        "settings_overrides": load_settings_overrides(),
        "pricing": load_pricing(),
        "estimate_assumptions": load_estimate_assumptions(),
    }
