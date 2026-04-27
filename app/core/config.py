from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "TOP5 Hot AI Project"
    env: str = "development"
    database_url: str = "sqlite:///./data/radar.db"
    github_token: str | None = None
    github_rest_endpoint: str = "https://api.github.com"
    admin_token: str | None = "change-me"
    score_version: str = "v1.0.0"
    candidate_limit: int = Field(default=120, ge=10, le=1000)
    min_stars: int = Field(default=100, ge=0)
    lookback_days: int = Field(default=7, ge=1, le=30)
    use_mock_when_no_token: bool = True
    collect_weekday: int = Field(default=0, ge=0, le=6)
    collect_hour: int = Field(default=1, ge=0, le=23)
    cache_ttl_seconds: int = Field(default=900, ge=0)
    http_timeout_seconds: float = Field(default=20.0, ge=1.0)
    http_max_retries: int = Field(default=2, ge=0, le=5)
    report_output_dir: str = "output/reports"
    public_base_url: str = "http://127.0.0.1:8000"
    log_level: str = "INFO"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")

    @property
    def sqlite_path(self) -> Path | None:
        if not self.is_sqlite:
            return None
        prefix = "sqlite:///"
        if not self.database_url.startswith(prefix):
            return None
        raw_path = self.database_url.removeprefix(prefix)
        return Path(raw_path)


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    sqlite_path = settings.sqlite_path
    if sqlite_path is not None and sqlite_path.parent != Path("."):
        sqlite_path.parent.mkdir(parents=True, exist_ok=True)
    Path(settings.report_output_dir).mkdir(parents=True, exist_ok=True)
    return settings
