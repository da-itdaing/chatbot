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
    # 백업 키 (Tier4 - 키 로테이션용)
    openai_api_key2: Optional[str] = Field(None, alias="OPENAI_API_KEY2")
    openai_model: str = Field("gpt-4o-mini", alias="OPENAI_MODEL")
    # RAG 전용 모델을 분리하고 싶을 때 사용 (없으면 openai_model 사용)
    openai_rag_model: Optional[str] = Field(None, alias="OPENAI_RAG_MODEL")
    openai_embedding_model: str = Field("text-embedding-3-small", alias="OPENAI_EMBEDDING_MODEL")
    
    @property
    def openai_api_keys(self) -> list[str]:
        """사용 가능한 모든 OpenAI API 키 목록 반환 (로테이션용)"""
        keys = [self.openai_api_key]
        if self.openai_api_key2:
            keys.append(self.openai_api_key2)
        return keys

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
    rag_top_k: int = Field(2, alias="RAG_TOP_K")
    zone_rag_top_k: int = Field(3, alias="ZONE_RAG_TOP_K")
    # v10: 멀티턴 P99 개선 - 컨텍스트 크기 축소 (6 → 4)
    max_message_history: int = Field(4, alias="MAX_MESSAGE_HISTORY")
    zone_max_message_history: int = Field(4, alias="ZONE_MAX_MESSAGE_HISTORY")
    # RAG 응답에서 사용할 최대 completion 토큰 수 (답변 길이 제한용)
    rag_max_completion_tokens: int = Field(256, alias="RAG_MAX_COMPLETION_TOKENS")

    # --- Conversation summarization ---
    # 요약 기능이 비활성화되면 summarize_messages 노드는 state를 그대로 반환한다.
    summary_enabled: bool = Field(True, alias="SUMMARY_ENABLED")
    # v10: 6으로 상향 - 3턴 이하 대화에서는 요약 스킵 (4 → 6)
    summary_min_messages: int = Field(6, alias="SUMMARY_MIN_MESSAGES")
    # 마지막 answer 길이가 이 값보다 짧으면 요약을 건너뛴다 (0이면 비활성화).
    summary_min_answer_chars: int = Field(0, alias="SUMMARY_MIN_ANSWER_CHARS")

    # --- LangSmith / LangChain tracing (optional) ---
    langsmith_api_key: Optional[str] = Field(None, alias="LANGSMITH_API_KEY")
    # 백업 LangSmith 키 (우선 사용)
    langsmith_api_key2: Optional[str] = Field(None, alias="LANGSMITH_API_KEY2")
    langsmith_project: Optional[str] = Field(None, alias="LANGSMITH_PROJECT")
    langsmith_tracing: bool = Field(True, alias="LANGSMITH_TRACING")
    langsmith_endpoint: Optional[str] = Field(None, alias="LANGCHAIN_ENDPOINT")
    
    # --- LangGraph AES encryption ---
    langgraph_aes_key: Optional[str] = Field(None, alias="LANGGRAPH_AES_KEY")
    # 백업 AES 키 (우선 사용)
    langgraph_aes_key2: Optional[str] = Field(None, alias="LANGGRAPH_AES_KEY2")
    
    @property
    def active_langsmith_key(self) -> Optional[str]:
        """우선순위: langsmith_api_key2 > langsmith_api_key"""
        return self.langsmith_api_key2 or self.langsmith_api_key
    
    @property
    def active_aes_key(self) -> Optional[str]:
        """우선순위: langgraph_aes_key2 > langgraph_aes_key"""
        return self.langgraph_aes_key2 or self.langgraph_aes_key

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


