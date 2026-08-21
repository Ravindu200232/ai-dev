"""Application settings."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

from ..bridge import (
    LOCODE_ROOT,
    SRS_DB,
    SRS_STAGING,
    mongo_uri,
    srs_model,
)


REPO_ROOT = LOCODE_ROOT


class Settings(BaseSettings):

    model_config = SettingsConfigDict(
        env_file_encoding="utf-8",
        extra="ignore",
    )

    ollama_base_url: str = "http://localhost:11434"
    ollama_primary_model: str = ""
    ollama_fallback_model: str = ""
    ollama_request_timeout: int = 600
    ollama_num_ctx: int = 8192
    llm_allow_offline_fallback: bool = True

    llm_max_repair_attempts: int = 2

    mongodb_uri: str = ""
    mongodb_db: str = SRS_DB
    mongodb_allow_memory_fallback: bool = True

    storage_dir: str = str(SRS_STAGING)
    api_host: str = "127.0.0.1"
    api_port: int = 7826
    cors_origins: str = "http://localhost:3000"

    mermaid_cli: str = "mmdc"

    def model_post_init(self, __context) -> None:

        if not self.mongodb_uri:
            self.mongodb_uri = mongo_uri()
        if not self.ollama_primary_model:
            self.ollama_primary_model = srs_model()
        if not self.ollama_fallback_model:
            self.ollama_fallback_model = self.ollama_primary_model

    @property
    def storage_path(self) -> Path:
        p = Path(self.storage_dir)
        if not p.is_absolute():
            p = REPO_ROOT / self.storage_dir
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    def project_dir(self, project_id: str) -> Path:
        p = self.storage_path / project_id
        p.mkdir(parents=True, exist_ok=True)
        return p


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
