"""
PostgreSQL 기반 공유 캐시 (v13).

워커 간 캐시를 공유하여 --workers 2 환경에서도 캐시 히트율을 유지합니다.
메모리 캐시의 보조 레이어로 사용됩니다.

테이블 스키마:
```sql
CREATE TABLE IF NOT EXISTS rag_cache (
    cache_key VARCHAR(64) PRIMARY KEY,
    cache_value JSONB NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
    hit_count INTEGER DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_rag_cache_expires_at ON rag_cache(expires_at);
```
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import asyncpg

from app.config import get_settings

logger = logging.getLogger(__name__)

# 글로벌 커넥션 풀
_pool: Optional[asyncpg.Pool] = None


async def init_db_cache_pool() -> asyncpg.Pool:
    """DB 캐시용 커넥션 풀 초기화."""
    global _pool
    if _pool is None:
        settings = get_settings()
        _pool = await asyncpg.create_pool(
            settings.checkpoint_conn_string,
            min_size=2,
            max_size=5,
            command_timeout=10,
        )
        # 테이블 생성
        await _ensure_cache_table()
    return _pool


async def _ensure_cache_table() -> None:
    """캐시 테이블이 없으면 생성."""
    if _pool is None:
        return
    
    async with _pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS rag_cache (
                cache_key VARCHAR(64) PRIMARY KEY,
                cache_value JSONB NOT NULL,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
                hit_count INTEGER DEFAULT 0
            )
        """)
        await conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_rag_cache_expires_at 
            ON rag_cache(expires_at)
        """)
        logger.info("RAG cache table ensured")


async def get_from_db_cache(cache_key: str) -> Optional[List[Dict[str, Any]]]:
    """
    DB 캐시에서 값을 조회합니다.
    
    Args:
        cache_key: 캐시 키 (MD5 해시)
    
    Returns:
        캐시된 문서 목록 또는 None
    """
    if _pool is None:
        await init_db_cache_pool()
    
    if _pool is None:
        return None
    
    try:
        async with _pool.acquire() as conn:
            # 만료되지 않은 캐시만 조회
            row = await conn.fetchrow(
                """
                SELECT cache_value 
                FROM rag_cache 
                WHERE cache_key = $1 AND expires_at > NOW()
                """,
                cache_key
            )
            
            if row is None:
                return None
            
            # 히트 카운트 증가 (비동기, 실패해도 무시)
            try:
                await conn.execute(
                    """
                    UPDATE rag_cache 
                    SET hit_count = hit_count + 1 
                    WHERE cache_key = $1
                    """,
                    cache_key
                )
            except Exception:
                pass
            
            cache_value = row["cache_value"]
            if isinstance(cache_value, str):
                return json.loads(cache_value)
            return cache_value
    
    except Exception as e:
        logger.warning(f"DB cache read error: {e}")
        return None


async def set_to_db_cache(
    cache_key: str,
    documents: List[Dict[str, Any]],
    ttl_seconds: int = 1800,
) -> None:
    """
    DB 캐시에 값을 저장합니다.
    
    Args:
        cache_key: 캐시 키 (MD5 해시)
        documents: 저장할 문서 목록
        ttl_seconds: TTL (기본 30분)
    """
    if _pool is None:
        await init_db_cache_pool()
    
    if _pool is None:
        return
    
    try:
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)
        cache_value = json.dumps(documents, ensure_ascii=False)
        
        async with _pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO rag_cache (cache_key, cache_value, expires_at)
                VALUES ($1, $2::jsonb, $3)
                ON CONFLICT (cache_key) 
                DO UPDATE SET 
                    cache_value = EXCLUDED.cache_value,
                    expires_at = EXCLUDED.expires_at,
                    created_at = NOW()
                """,
                cache_key,
                cache_value,
                expires_at,
            )
    except Exception as e:
        logger.warning(f"DB cache write error: {e}")


async def clear_expired_cache() -> int:
    """
    만료된 캐시를 삭제합니다.
    
    Returns:
        삭제된 행 수
    """
    if _pool is None:
        return 0
    
    try:
        async with _pool.acquire() as conn:
            result = await conn.execute(
                "DELETE FROM rag_cache WHERE expires_at < NOW()"
            )
            # "DELETE N" 형식에서 N 추출
            deleted = int(result.split()[-1]) if result else 0
            if deleted > 0:
                logger.info(f"Cleared {deleted} expired cache entries")
            return deleted
    except Exception as e:
        logger.warning(f"Cache cleanup error: {e}")
        return 0


async def get_db_cache_stats() -> Dict[str, Any]:
    """
    DB 캐시 통계를 반환합니다.
    """
    if _pool is None:
        return {"status": "not_initialized"}
    
    try:
        async with _pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT 
                    COUNT(*) as total_entries,
                    COUNT(*) FILTER (WHERE expires_at > NOW()) as valid_entries,
                    SUM(hit_count) as total_hits,
                    AVG(hit_count) as avg_hits
                FROM rag_cache
                """
            )
            
            return {
                "total_entries": row["total_entries"] or 0,
                "valid_entries": row["valid_entries"] or 0,
                "total_hits": row["total_hits"] or 0,
                "avg_hits_per_entry": float(row["avg_hits"] or 0),
            }
    except Exception as e:
        logger.warning(f"DB cache stats error: {e}")
        return {"error": str(e)}


async def close_db_cache_pool() -> None:
    """커넥션 풀 종료."""
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
        logger.info("DB cache pool closed")


__all__ = [
    "init_db_cache_pool",
    "get_from_db_cache",
    "set_to_db_cache",
    "clear_expired_cache",
    "get_db_cache_stats",
    "close_db_cache_pool",
]

