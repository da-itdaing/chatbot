"""
관리자 API 라우터 (v13).

캐시 통계, 시스템 상태, Warm-up 등 운영 관리 기능을 제공합니다.
"""
from __future__ import annotations

import asyncio
from typing import Any, Dict, List

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.utils.cache import (
    get_all_cache_stats,
    clear_all_caches,
)
from app.tools.retrieval import _rag_cache
from app.utils.db_cache import (
    get_db_cache_stats,
    clear_expired_cache,
)

router = APIRouter(prefix="/admin", tags=["admin"])


class CacheStats(BaseModel):
    """캐시 통계 응답 모델."""
    l1_memory: Dict[str, Any] = Field(..., description="L1 메모리 캐시 (워커별)")
    l2_db: Dict[str, Any] = Field(..., description="L2 DB 캐시 (워커 간 공유)")
    embedding: Dict[str, Any] = Field(..., description="임베딩 캐시 통계")
    response: Dict[str, Any] = Field(..., description="응답 캐시 통계")


class WarmupRequest(BaseModel):
    """Warm-up 요청 모델."""
    queries: List[str] = Field(
        default_factory=list,
        description="캐시 예열할 쿼리 목록 (없으면 기본 쿼리 사용)"
    )


class WarmupResponse(BaseModel):
    """Warm-up 응답 모델."""
    success: int = Field(..., description="성공한 쿼리 수")
    failed: int = Field(..., description="실패한 쿼리 수")
    queries: List[str] = Field(..., description="예열된 쿼리 목록")


# 기본 Warm-up 쿼리 (자주 사용되는 질문 패턴)
DEFAULT_WARMUP_QUERIES = [
    "플리마켓 추천해줘",
    "주말에 갈만한 플리마켓",
    "동구 플리마켓",
    "서구 플리마켓",
    "광산구 플리마켓",
    "야시장 추천",
    "축제 뭐 있어",
    "팝업스토어 추천",
    "아이랑 갈만한 마켓",
    "데이트하기 좋은 곳",
    "무료 입장 마켓",
    "오늘 열리는 마켓",
    "핸드메이드 마켓",
    "빈티지 마켓",
    "푸드트럭 있는 곳",
]


@router.get(
    "/cache/stats",
    response_model=CacheStats,
    summary="캐시 통계 조회",
    description="L1(메모리), L2(DB), 임베딩, 응답 캐시의 현재 상태와 히트율을 조회합니다.",
)
async def get_cache_stats() -> CacheStats:
    """캐시 통계를 반환합니다."""
    general_stats = get_all_cache_stats()
    l1_stats = _rag_cache.stats
    l2_stats = await get_db_cache_stats()
    
    return CacheStats(
        l1_memory=l1_stats,
        l2_db=l2_stats,
        embedding=general_stats.get("embedding", {}),
        response=general_stats.get("response", {}),
    )


@router.post(
    "/cache/clear",
    summary="캐시 초기화",
    description="모든 캐시를 비웁니다. 주의: 캐시 히트율이 초기화됩니다.",
)
async def clear_cache() -> Dict[str, str]:
    """모든 캐시를 비웁니다."""
    clear_all_caches()
    _rag_cache.clear()
    return {"status": "cleared", "message": "All caches have been cleared"}


@router.post(
    "/warmup",
    response_model=WarmupResponse,
    summary="캐시 Warm-up",
    description="자주 사용되는 쿼리를 미리 실행하여 캐시를 예열합니다.",
)
async def warmup_cache(request: WarmupRequest) -> WarmupResponse:
    """
    캐시 Warm-up을 실행합니다.
    
    서버 시작 후 또는 캐시 초기화 후 호출하여
    자주 사용되는 쿼리의 응답 시간을 개선합니다.
    """
    from app.tools.retrieval import consumer_retrieve_async
    
    queries = request.queries if request.queries else DEFAULT_WARMUP_QUERIES
    success_count = 0
    failed_count = 0
    warmed_queries = []
    
    for query in queries:
        try:
            # RAG 검색 실행 (캐시에 저장됨)
            await consumer_retrieve_async.ainvoke({"query": query})
            success_count += 1
            warmed_queries.append(query)
        except Exception:
            failed_count += 1
    
    return WarmupResponse(
        success=success_count,
        failed=failed_count,
        queries=warmed_queries,
    )


@router.get(
    "/health/detailed",
    summary="상세 헬스체크",
    description="캐시 상태, DB 연결 등 상세 시스템 상태를 조회합니다.",
)
async def detailed_health() -> Dict[str, Any]:
    """상세 시스템 상태를 반환합니다."""
    cache_stats = get_all_cache_stats()
    l1_stats = _rag_cache.stats
    l2_stats = await get_db_cache_stats()
    
    return {
        "status": "ok",
        "cache": {
            "l1_memory": l1_stats,
            "l2_db": l2_stats,
            "embedding": cache_stats.get("embedding", {}),
            "response": cache_stats.get("response", {}),
        },
        "version": "v13",
    }


@router.post(
    "/cache/cleanup",
    summary="만료된 캐시 정리",
    description="만료된 DB 캐시를 삭제합니다.",
)
async def cleanup_cache() -> Dict[str, Any]:
    """만료된 캐시를 삭제합니다."""
    deleted = await clear_expired_cache()
    return {"status": "cleaned", "deleted_entries": deleted}


__all__ = ["router"]

