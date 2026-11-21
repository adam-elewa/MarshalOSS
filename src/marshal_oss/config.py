from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="MARSHALOSS_",
        env_file=".env",
        extra="ignore",
    )

    data_dir: Path = Path.home() / ".marshaloss"
    db_path: Path | None = None
    host: str = "0.0.0.0"
    port: int = 7340
    public_base: str = "http://100.126.162.84:7340"

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

    # ASR escalate when Docling local transcript looks empty/hallucinated/sparse
    asr_escalate: bool = True
    asr_escalate_model: str = "gpt-4o-transcribe"
    asr_escalate_min_score: float = 0.72
    asr_escalate_max_chunks: int = 40  # ~10 min windows → ~6.5h max by default
    asr_escalate_prompt: str = (
        "Body-worn camera, surveillance, or emergency radio audio. "
        "Prefer verbatim speech; do not invent dialogue over silence or noise. "
        "Keep names and radio codes when clear."
    )

    # Late-pass visual index: AI captions for stills + sampled video frames
    visual_analyze: bool = True
    visual_analyze_images: bool = True
    visual_analyze_video: bool = True
    visual_frame_interval_sec: float = 5.0
    visual_max_frames: int = 48  # e.g. 5s × 48 ≈ 4 min of coverage
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

    bates_pattern: str = (
        r"(?i)(?:(?:NSBHF[_ ]?Washington|Washington|HCPO|Bates)[_\s-]*"
        r"(\d{3,7}(?:\s*[-–]\s*\d{3,7})?)"
        r"|\b([A-Z]{2,8}[_-]?\d{4,7}(?:\s*[-–]\s*\d{3,7})?)\b)"
    )

    max_file_bytes: int = 500 * 1024 * 1024

    # Parallel file ingest (Docling/ffmpeg/API are I/O-bound). 1 = serial.
    # Tune to hardware / API rate limits: e.g. 4–8 on Gideon, 2 on a laptop.
    ingest_workers: int = 4

    # Expand production .zip before ingest (stdlib zipfile → <name>__unzipped/)
    expand_zips: bool = True
    expand_zip_max_bytes: int = 8 * 1024 * 1024 * 1024
    expand_zip_max_depth: int = 2

    # Docling-native + ffmpeg-bridge A/V (see media_ffmpeg.AUDIO_VIDEO_EXTS)
    ingest_extensions: str = (
        ".pdf,.docx,.doc,.pptx,.ppt,.xlsx,.xls,.csv,.txt,.md,"
        ".html,.htm,.eml,.msg,.png,.jpg,.jpeg,.tif,.tiff,.bmp,.webp,"
        ".mp3,.wav,.m4a,.aac,.ogg,.flac,.mp4,.mov,.avi,.mkv,.webm,"
        ".wma,.wmv,.flv,.asf,.ts,.m4v,.mpeg,.mpg,.3gp,.caf,.dav,.vob,.h264,.m2ts,.mts,.mxf"
    )

    @property
    def database_path(self) -> Path:
        return self.db_path or (self.data_dir / "marshal_oss.db")

    @property
    def allowed_extensions(self) -> set[str]:
        return {e.strip().lower() for e in self.ingest_extensions.split(",") if e.strip()}


def get_settings() -> Settings:
    return Settings()
