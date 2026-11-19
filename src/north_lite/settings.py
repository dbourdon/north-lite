"""Runtime configuration, read from environment variables (and an optional .env)."""

from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_DOCS = Path(__file__).resolve().parents[2] / "data" / "handbook.json"


class Settings(BaseSettings):
    # Every field maps to NL_<NAME> except the API key, which keeps the name the
    # Cohere SDK and docs use (CO_API_KEY) so one export works for both.
    model_config = SettingsConfigDict(env_prefix="NL_", env_file=".env", extra="ignore")

    co_api_key: SecretStr | None = Field(
        default=None, validation_alias=AliasChoices("CO_API_KEY", "NL_CO_API_KEY")
    )
    backend: Literal["cohere", "fake"] = "cohere"
    # Current IDs from docs.cohere.com/docs/models (checked 2026-11-19).
    chat_model: str = "command-a-plus-05-2026"
    embed_model: str = "embed-v5.0-pro"
    rerank_model: str = "rerank-v5.0-pro"
    timeout_s: float = Field(default=30.0, gt=0)
    embed_concurrency: int = Field(default=4, ge=1)
    docs_path: Path = DEFAULT_DOCS

    # Feature flags (NL_FEATURE_*). Each deployed environment is one combination;
    # the Helm chart (Day 4) sets them from values, CI tests every combination.
    feature_rerank: bool = True
    feature_streaming: bool = True
    feature_cache: bool = False

    # Human-in-the-loop for side-effecting tools (NL_REQUIRE_APPROVAL).
    require_approval: bool = True
