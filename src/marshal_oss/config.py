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

    bates_pattern: str = (
        r"(?i)(?:(?:NSBHF[_ ]?Washington|Washington|HCPO|Bates)[_\s-]*"
        r"(\d{3,7}(?:\s*[-–]\s*\d{3,7})?)"
        r"|\b([A-Z]{2,8}[_-]?\d{4,7}(?:\s*[-–]\s*\d{3,7})?)\b)"
    )

    max_file_bytes: int = 500 * 1024 * 1024
    ingest_extensions: str = (
        ".pdf,.docx,.doc,.pptx,.ppt,.xlsx,.xls,.csv,.txt,.md,"
        ".html,.htm,.eml,.msg,.png,.jpg,.jpeg,.tif,.tiff,.bmp,.webp,"
        ".mp3,.wav,.m4a,.aac,.ogg,.flac,.mp4,.mov,.avi,.mkv,.webm"
    )

    @property
    def database_path(self) -> Path:
        return self.db_path or (self.data_dir / "marshal_oss.db")

    @property
    def allowed_extensions(self) -> set[str]:
        return {e.strip().lower() for e in self.ingest_extensions.split(",") if e.strip()}


def get_settings() -> Settings:
    return Settings()
