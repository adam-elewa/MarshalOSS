from __future__ import annotations

from pathlib import Path
from typing import Any, Tuple, Type

from pydantic.fields import FieldInfo
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)


class _KnobsTomlSource(PydanticBaseSettingsSource):
    """Apply packaged/user knobs.toml below env / dotenv."""

    def get_field_value(
        self, field: FieldInfo, field_name: str
    ) -> Tuple[Any, str, bool]:
        return None, field_name, False

    def __call__(self) -> dict[str, Any]:
        from .knobs import load_settings_overrides

        return load_settings_overrides()


class Settings(BaseSettings):
    """
    Runtime settings for MarshalOSS.

    Cost/quality knobs are documented in ``marshal_oss/knobs.toml``.
    Copy that file to ``~/.marshaloss/knobs.toml`` to tweak; ``MARSHALOSS_*``
    env vars still override. Run ``marshaloss knobs`` / ``marshaloss estimate``.
    """

    model_config = SettingsConfigDict(
        env_prefix="MARSHALOSS_",
        env_file=".env",
        extra="ignore",
    )

    data_dir: Path = Path.home() / ".marshaloss"
    db_path: Path | None = None
    host: str = "0.0.0.0"
    port: int = 7340
    # Base URL clients use for open/download links (localhost, Tailscale, tunnel, …)
    public_base: str = "http://127.0.0.1:7340"

    embed_backend: str = "voyage"  # voyage | hash
    embed_model: str = "voyage-law-2"
    voyage_api_key: str | None = None

    # AI OCR escalation when Docling confidence / text quality is poor
    ocr_escalate: bool = True
    ocr_escalate_provider: str = "auto"  # auto | openai | anthropic
    ocr_escalate_openai_model: str = "gpt-4o-mini"
    ocr_escalate_anthropic_model: str = "claude-haiku-4-5-20251001"
    openai_api_key: str | None = None
    anthropic_api_key: str | None = None
    ocr_escalate_max_pages: int = 25
    ocr_escalate_min_score: float = 0.72  # escalate when quality.score below this AND reasons
    # When Docling marks specific bad pages, only those are sent (else whole doc up to max_pages)

    # Audio/video transcription. "openai": every A/V file goes to asr_model (speaker labels +
    # line times); local Docling Whisper is the fallback with no key or on API error.
    # "local": Docling Whisper first; asr_escalate re-runs poor transcripts on asr_model.
    asr_provider: str = "openai"  # openai | local
    asr_model: str = "gpt-4o-transcribe-diarize"
    # Legacy HQ stack: diarize → gpt-4o-transcribe+logprobs → whisper verify (recommended for BWC)
    asr_hq: bool = True
    asr_language: str = "en"
    asr_escalate: bool = True
    asr_escalate_min_score: float = 0.72
    asr_max_windows: int = 60  # 10-minute upload windows → 10 h per file
    # Used only by models that accept a prompt (not the diarize model)
    asr_escalate_prompt: str = (
        "Body-worn camera, surveillance, or emergency radio audio. "
        "Prefer verbatim speech; do not invent dialogue over silence or noise. "
        "Keep names and radio codes when clear."
    )
    # Prompt for HQ second-pass (gpt-4o-transcribe). Override per matter via env.
    asr_hq_prompt: str = (
        "Body-worn camera and emergency radio audio from a police incident. "
        "Speakers may include officers, civilians, witnesses, and dispatch. "
        "Prefer verbatim speech; do not invent dialogue over silence or noise. "
        "Keep names, badge numbers, and radio codes when clear."
    )

    # Late-pass visual index: AI captions for stills + sampled video frames
    visual_analyze: bool = True
    visual_analyze_images: bool = True
    visual_analyze_video: bool = True
    visual_frame_interval_sec: float = 5.0
    visual_max_frames: int = 48  # longer videos: interval widens to span the whole video; 0 = no cap
    visual_skip_if_ocr_chars: int = 400  # skip stills that already OCR'd well

    # File-level AI entities/tags (full extracted text; map+canonicalize if long)
    entity_extract: bool = True
    entity_extract_provider: str = "auto"  # auto | openai | anthropic
    entity_extract_openai_model: str = "gpt-4o-mini"
    entity_extract_anthropic_model: str = "claude-haiku-4-5-20251001"
    # 0 = use all extracted text; >0 = hard ceiling (rare safety valve)
    entity_extract_max_chars: int = 0
    # Chars per map call when file exceeds one window
    entity_extract_segment_chars: int = 14000

    # Generic Bates / production id from path (override per matter if needed)
    bates_pattern: str = (
        r"(?i)(?:Bates[_\s-]*(\d{3,7}(?:\s*[-–]\s*\d{3,7})?)"
        r"|\b([A-Z]{2,8}[_-]?\d{4,7}(?:\s*[-–]\s*\d{3,7})?)\b)"
    )

    # Parallel file ingest (Docling/ffmpeg/API are I/O-bound). 1 = serial.
    # Tune to hardware / API rate limits (e.g. 2 on a laptop, 8 on a workstation).
    ingest_workers: int = 4

    # Expand production .zip before ingest (stdlib zipfile → <name>__unzipped/)
    expand_zips: bool = True
    expand_zip_max_depth: int = 2

    # Docling-native + ffmpeg-bridge A/V (see media_ffmpeg.AUDIO_VIDEO_EXTS)
    ingest_extensions: str = (
        ".pdf,.docx,.doc,.pptx,.ppt,.xlsx,.xls,.csv,.txt,.md,"
        ".html,.htm,.xml,.kml,.st3,.eml,.msg,.png,.jpg,.jpeg,.tif,.tiff,.bmp,.webp,"
        ".mp3,.wav,.m4a,.aac,.ogg,.flac,.mp4,.mov,.avi,.mkv,.webm,"
        ".wma,.wmv,.flv,.asf,.ts,.m4v,.mpeg,.mpg,.3gp,.caf,.dav,.vob,.h264,.m2ts,.mts,.mxf"
    )

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: Type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> Tuple[PydanticBaseSettingsSource, ...]:
        # Highest → lowest: init kwargs, env, dotenv, knobs.toml, secrets
        return (
            init_settings,
            env_settings,
            dotenv_settings,
            _KnobsTomlSource(settings_cls),
            file_secret_settings,
        )

    @property
    def database_path(self) -> Path:
        return self.db_path or (self.data_dir / "marshal_oss.db")

    @property
    def allowed_extensions(self) -> set[str]:
        return {e.strip().lower() for e in self.ingest_extensions.split(",") if e.strip()}


def get_settings() -> Settings:
    return Settings()
