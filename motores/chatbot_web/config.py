from pathlib import Path

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_DIR = Path(__file__).resolve().parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_DIR / ".env", env_file_encoding="utf-8", extra="ignore"
    )

    ollama_url: str = "http://localhost:11434"
    embedding_model: str = "qwen3-embedding:0.6b"
    chat_model: str = "qwen3:8b"
    chroma_path: Path = Path("chroma_db")
    web_collection: str = "unamba_web"
    state_path: Path = Path("data/web.sqlite3")

    top_k_candidatos: int = Field(8, ge=1)
    top_k_final: int = Field(4, ge=1)
    max_context_chars: int = Field(4500, ge=300)
    min_similarity: float = Field(0.15, ge=-1, le=1)

    cache_ttl: int = Field(300, ge=1)
    cache_size: int = Field(512, ge=1)

    ollama_concurrency: int = Field(2, ge=1)
    generation_concurrency: int = Field(1, ge=1)
    max_tokens: int = Field(350, ge=1)
    temperature: float = Field(0.1, ge=0, le=2)
    chat_think: bool = False
    direct_link_answers: bool = True

    @field_validator("chroma_path", "state_path", mode="after")
    @classmethod
    def resolve_storage_path(cls, value: Path) -> Path:
        value = value.expanduser()
        return (value if value.is_absolute() else PROJECT_DIR / value).resolve()

    @model_validator(mode="after")
    def validate_contract(self):
        if self.embedding_model != "qwen3-embedding:0.6b":
            raise ValueError("Only qwen3-embedding:0.6b is permitted")
        if self.web_collection != "unamba_web":
            raise ValueError("This service only opens the unamba_web collection")
        if self.top_k_final > self.top_k_candidatos:
            raise ValueError("TOP_K_FINAL cannot be greater than TOP_K_CANDIDATOS")
        return self
