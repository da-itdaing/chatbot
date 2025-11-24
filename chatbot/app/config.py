from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


ROOT_DIR = Path(__file__).resolve().parents[1]


class Settings(BaseSettings):
    """
    Central configuration for the chatbot service.

    Values are primarily loaded from `chatbot.env` at the project root,
    but can be overridden by environment variables.
    """

    # --- OpenAI / models ---
    openai_api_key: str = Field(..., alias="OPENAI_API_KEY")
    openai_model: str = Field("gpt-4o-mini", alias="OPENAI_MODEL")
    openai_embedding_model: str = Field("text-embedding-3-small", alias="OPENAI_EMBEDDING_MODEL")

    # --- PGVector (consumer markets RAG) ---
    pgvector_connection: str = Field(..., alias="PGVECTOR_CONNECTION")
    consumer_collection: str = Field("itdaing_popups", alias="VECTOR_COLLECTION")

    # --- PGVector (seller zone RAG) ---
    pgvector_zone_connection: Optional[str] = Field(None, alias="PGVECTOR_ZONE_URL")
    seller_zone_collection: str = Field("itdaing_zone", alias="PGVECTOR_ZONE_COLLECTION")

    # --- LangGraph checkpoint Postgres ---
    # If CHECKPOINT_DB_URL is not provided, fall back to POSTGRES_* fields below.
    checkpoint_db_url: Optional[str] = Field(None, alias="CHECKPOINT_DB_URL")

    # Raw Postgres info (used as fallback for checkpoint DB DSN)
    postgres_user: str = Field(..., alias="POSTGRES_USER")
    postgres_password: str = Field(..., alias="POSTGRES_PASSWORD")
    postgres_db: str = Field(..., alias="POSTGRES_DB")
    postgres_host: str = Field(..., alias="POSTGRES_HOST")
    postgres_port: int = Field(5432, alias="POSTGRES_PORT")

    # --- Seed data paths ---
    markets_seed_path: Path = Field(
        ROOT_DIR.parent / "markets_seed.json",
        alias="MARKETS_SEED_PATH",
    )
    zones_seed_path: Path = Field(
        ROOT_DIR.parent / "zones_seed.json",
        alias="ZONES_SEED_PATH",
    )

    # --- Misc limits ---
    rag_top_k: int = Field(3, alias="RAG_TOP_K")
    zone_rag_top_k: int = Field(3, alias="ZONE_RAG_TOP_K")
    max_message_history: int = Field(6, alias="MAX_MESSAGE_HISTORY")
    zone_max_message_history: int = Field(6, alias="ZONE_MAX_MESSAGE_HISTORY")

    # --- LangSmith / LangChain tracing (optional) ---
    langsmith_api_key: Optional[str] = Field(None, alias="LANGSMITH_API_KEY")
    langsmith_project: Optional[str] = Field(None, alias="LANGSMITH_PROJECT")
    langsmith_tracing: bool = Field(True, alias="LANGSMITH_TRACING")
    langsmith_endpoint: Optional[str] = Field(None, alias="LANGCHAIN_ENDPOINT")

    # --- Web search / external info ---
    websearch_enabled: bool = Field(False, alias="WEBSEARCH_ENABLED")
    websearch_provider: str = Field("duckduckgo", alias="WEBSEARCH_PROVIDER")
    websearch_top_k: int = Field(3, alias="WEBSEARCH_TOP_K")
    tavily_api_key: Optional[str] = Field(None, alias="TAVILY_API_KEY")

    model_config = SettingsConfigDict(
        env_file=str(ROOT_DIR / "chatbot.env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @property
    def checkpoint_conn_string(self) -> str:
        """
        Connection string for LangGraph checkpoint Postgres.

        Prefer explicit CHECKPOINT_DB_URL; otherwise build a standard
        psycopg3-style DSN from POSTGRES_* fields.
        """

        if self.checkpoint_db_url:
            return self.checkpoint_db_url
        return (
            f"postgresql://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """
    Cached accessor for Settings.

    Using an LRU cache keeps Settings as a process-wide singleton while
    avoiding repeated disk I/O for env loading.
    """

    return Settings()  # type: ignore[arg-type]


__all__ = ["Settings", "get_settings", "ROOT_DIR"]


