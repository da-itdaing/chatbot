from __future__ import annotations

from typing import Any, Optional

import asyncpg
from langchain_openai import OpenAIEmbeddings
from langchain_postgres.vectorstores import PGVector
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.checkpoint.serde.encrypted import EncryptedSerializer

from app.config import Settings, get_settings


async def create_async_pool(dsn: Optional[str] = None) -> asyncpg.Pool:
    """
    Create an asyncpg connection pool.

    This is intended for light operational queries / health checks.
    Vector and LangGraph layers use their own drivers.
    """

    settings = get_settings()
    db_url = dsn or settings.checkpoint_db_url
    return await asyncpg.create_pool(dsn=db_url)


async def create_langgraph_checkpointer(
    settings: Optional[Settings] = None,
) -> AsyncPostgresSaver:
    """
    Initialise an AsyncPostgresSaver with encrypted serde for LangGraph.

    NOTE: This function does *not* manage lifecycle (closing the pool).
    Callers should keep the returned instance for the app lifetime.
    """

    cfg = settings or get_settings()
    serde = EncryptedSerializer.from_pycryptodome_aes()

    # Deprecated: prefer managing the context in `app.main` so that the
    # AsyncPostgresSaver lifetime matches the FastAPI app lifetime.
    saver_cm = AsyncPostgresSaver.from_conn_string(
        cfg.checkpoint_conn_string,
        serde=serde,
    )
    checkpointer = await saver_cm.__aenter__()  # type: ignore[union-attr]
    await checkpointer.setup()
    return checkpointer


def get_markets_vectorstore(
    settings: Optional[Settings] = None,
    **kwargs: Any,
) -> PGVector:
    """
    Return a PGVector instance for consumer markets RAG.

    This assumes the collection has already been populated by a loader script.
    """

    cfg = settings or get_settings()
    # Wrap api_key in a callable to satisfy the type checker while still
    # passing a concrete string at runtime.
    def _api_key_provider() -> str:
        return cfg.openai_api_key

    embeddings = OpenAIEmbeddings(
        model=cfg.openai_embedding_model,
        api_key=_api_key_provider,
    )
    return PGVector(
        connection=cfg.pgvector_connection,
        embeddings=embeddings,
        collection_name=cfg.consumer_collection,
        **kwargs,
    )


def get_zones_vectorstore(
    settings: Optional[Settings] = None,
    **kwargs: Any,
) -> PGVector:
    """
    Return a PGVector instance for seller zone RAG.

    Falls back to the consumer PGVector connection if a separate
    zone connection is not configured.
    """

    cfg = settings or get_settings()
    conn = cfg.pgvector_zone_connection or cfg.pgvector_connection

    def _api_key_provider() -> str:
        return cfg.openai_api_key

    embeddings = OpenAIEmbeddings(
        model=cfg.openai_embedding_model,
        api_key=_api_key_provider,
    )
    return PGVector(
        connection=conn,
        embeddings=embeddings,
        collection_name=cfg.seller_zone_collection,
        **kwargs,
    )


__all__ = [
    "create_async_pool",
    "create_langgraph_checkpointer",
    "get_markets_vectorstore",
    "get_zones_vectorstore",
]


