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


async def get_zone_cell_stats(zone_id: int, pool: Optional[asyncpg.Pool] = None) -> dict:
    """
    존의 셀 통계를 조회합니다.
    
    Args:
        zone_id: zone_area.id
        pool: asyncpg 커넥션 풀 (없으면 새로 생성)
    
    Returns:
        {
            "total_cells": int,  # 전체 셀 수 (승인된 셀)
            "available_cells": int,  # 빈 셀 수 (현재 승인된 팝업이 없는 셀)
        }
    
    Note:
        "빈 셀" = 현재 승인된 팝업이 없는 셀 (owner_id는 NOT NULL 제약이 있음)
    """
    settings = get_settings()
    # pgvector_connection에서 asyncpg 호환 DSN 추출
    dsn = settings.pgvector_connection.replace('postgresql+psycopg://', 'postgresql://')
    
    close_pool = False
    if pool is None:
        pool = await asyncpg.create_pool(dsn=dsn)
        close_pool = True
    
    try:
        # 전체 셀 수 (승인된 셀만)
        total_query = """
            SELECT COUNT(*) FROM zone_cell 
            WHERE zone_area_id = $1 AND status = 'APPROVED'
        """
        total_cells = await pool.fetchval(total_query, zone_id) or 0
        
        # 빈 셀 수: 승인된 팝업이 없는 셀
        available_query = """
            SELECT COUNT(*) FROM zone_cell zc
            LEFT JOIN popup p ON zc.id = p.zone_cell_id AND p.approval_status = 'APPROVED'
            WHERE zc.zone_area_id = $1 
              AND zc.status = 'APPROVED'
              AND p.id IS NULL
        """
        available_cells = await pool.fetchval(available_query, zone_id) or 0
        
        return {
            "total_cells": total_cells,
            "available_cells": available_cells,
        }
    finally:
        if close_pool:
            await pool.close()


async def get_zone_geometry(zone_id: int, pool: Optional[asyncpg.Pool] = None) -> Optional[str]:
    """
    존의 geometry_data(GeoJSON 폴리곤)를 조회합니다.
    
    Args:
        zone_id: zone_area.id
        pool: asyncpg 커넥션 풀 (없으면 새로 생성)
    
    Returns:
        geometry_data (JSON 문자열) 또는 None
    """
    settings = get_settings()
    dsn = settings.pgvector_connection.replace('postgresql+psycopg://', 'postgresql://')
    
    close_pool = False
    if pool is None:
        pool = await asyncpg.create_pool(dsn=dsn)
        close_pool = True
    
    try:
        query = "SELECT geometry_data FROM zone_area WHERE id = $1"
        row = await pool.fetchrow(query, zone_id)
        return row["geometry_data"] if row else None
    finally:
        if close_pool:
            await pool.close()


def _calculate_polygon_center(geometry_data: str) -> tuple[Optional[float], Optional[float]]:
    """
    GeoJSON 폴리곤에서 중심점 좌표를 계산합니다.
    
    Args:
        geometry_data: GeoJSON 문자열 (Polygon)
    
    Returns:
        (lat, lng) 튜플 또는 (None, None)
    """
    import json
    
    try:
        geo = json.loads(geometry_data)
        
        if geo.get("type") == "Polygon" and geo.get("coordinates"):
            coords = geo["coordinates"][0]  # 외곽선 좌표
            if coords:
                # 단순 평균으로 중심점 계산
                lngs = [c[0] for c in coords]
                lats = [c[1] for c in coords]
                center_lng = sum(lngs) / len(lngs)
                center_lat = sum(lats) / len(lats)
                return (center_lat, center_lng)
        
        return (None, None)
    except (json.JSONDecodeError, KeyError, TypeError, IndexError):
        return (None, None)


async def get_zone_geometry_with_center(
    zone_id: int, 
    pool: Optional[asyncpg.Pool] = None
) -> dict:
    """
    존의 geometry_data와 중심점 좌표를 함께 조회합니다.
    
    Args:
        zone_id: zone_area.id
        pool: asyncpg 커넥션 풀 (없으면 새로 생성)
    
    Returns:
        {"polygon": ..., "lat": ..., "lng": ...} 또는 빈 딕셔너리
    """
    settings = get_settings()
    dsn = settings.pgvector_connection.replace('postgresql+psycopg://', 'postgresql://')
    
    close_pool = False
    if pool is None:
        pool = await asyncpg.create_pool(dsn=dsn)
        close_pool = True
    
    try:
        query = "SELECT geometry_data FROM zone_area WHERE id = $1"
        row = await pool.fetchrow(query, zone_id)
        
        if not row or not row["geometry_data"]:
            return {}
        
        geometry_data = row["geometry_data"]
        lat, lng = _calculate_polygon_center(geometry_data)
        
        result = {"polygon": geometry_data}
        if lat is not None and lng is not None:
            result["lat"] = lat
            result["lng"] = lng
        
        return result
    finally:
        if close_pool:
            await pool.close()


__all__ = [
    "create_async_pool",
    "create_langgraph_checkpointer",
    "get_markets_vectorstore",
    "get_zones_vectorstore",
    "get_zone_cell_stats",
    "get_zone_geometry",
    "get_zone_geometry_with_center",
]


