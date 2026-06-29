from functools import lru_cache

from pydantic import computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    camera_host: str
    camera_rtsp_port: int = 554
    camera_user: str = "admin"
    camera_password: str
    camera_channel: int = 1
    camera_stream: str = "main"
    camera_onvif_port: int = 8000

    app_host: str = "0.0.0.0"
    app_port: int = 8080
    debug: bool = False

    @computed_field  # type: ignore[prop-decorator]
    @property
    def rtsp_url(self) -> str:
        """Build IPCAM-compatible RTSP URL from individual fields."""
        return (
            f"rtsp://{self.camera_user}:{self.camera_password}"
            f"@{self.camera_host}:{self.camera_rtsp_port}"
            f"/h264/ch{self.camera_channel}/{self.camera_stream}/av_stream"
        )


@lru_cache
def get_settings() -> Settings:
    """Return cached Settings singleton. Call get_settings.cache_clear() in tests."""
    return Settings()
