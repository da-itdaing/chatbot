"""
캐싱 유틸리티 (v12).

임베딩 캐싱과 응답 캐싱을 제공하여 API 호출과 latency를 줄입니다.
Redis 없이 메모리 기반 LRU 캐시를 사용합니다.
"""
from __future__ import annotations

import hashlib
import time
from functools import lru_cache
from typing import Any, Dict, List, Optional, Tuple
from collections import OrderedDict
import threading
import logging

logger = logging.getLogger(__name__)


class TTLCache:
    """TTL(Time-To-Live)이 있는 LRU 캐시."""
    
    def __init__(self, maxsize: int = 1000, ttl_seconds: int = 3600):
        """
        Args:
            maxsize: 최대 캐시 항목 수
            ttl_seconds: 캐시 만료 시간 (초)
        """
        self.maxsize = maxsize
        self.ttl_seconds = ttl_seconds
        self._cache: OrderedDict[str, Tuple[Any, float]] = OrderedDict()
        self._lock = threading.Lock()
        self._hits = 0
        self._misses = 0
    
    def get(self, key: str) -> Optional[Any]:
        """캐시에서 값을 가져옵니다."""
        with self._lock:
            if key not in self._cache:
                self._misses += 1
                return None
            
            value, timestamp = self._cache[key]
            
            # TTL 확인
            if time.time() - timestamp > self.ttl_seconds:
                del self._cache[key]
                self._misses += 1
                return None
            
            # LRU: 최근 사용 항목을 맨 뒤로 이동
            self._cache.move_to_end(key)
            self._hits += 1
            return value
    
    def set(self, key: str, value: Any) -> None:
        """캐시에 값을 저장합니다."""
        with self._lock:
            # 이미 존재하면 업데이트
            if key in self._cache:
                self._cache.move_to_end(key)
                self._cache[key] = (value, time.time())
                return
            
            # 용량 초과 시 가장 오래된 항목 제거
            while len(self._cache) >= self.maxsize:
                self._cache.popitem(last=False)
            
            self._cache[key] = (value, time.time())
    
    def clear(self) -> None:
        """캐시를 비웁니다."""
        with self._lock:
            self._cache.clear()
            self._hits = 0
            self._misses = 0
    
    @property
    def stats(self) -> Dict[str, Any]:
        """캐시 통계를 반환합니다."""
        with self._lock:
            total = self._hits + self._misses
            hit_rate = self._hits / total if total > 0 else 0
            return {
                "size": len(self._cache),
                "maxsize": self.maxsize,
                "hits": self._hits,
                "misses": self._misses,
                "hit_rate": f"{hit_rate:.2%}",
            }


# 전역 캐시 인스턴스
_embedding_cache = TTLCache(maxsize=2000, ttl_seconds=7200)  # 2시간 TTL
_response_cache = TTLCache(maxsize=500, ttl_seconds=1800)     # 30분 TTL


def _hash_text(text: str) -> str:
    """텍스트를 해시합니다."""
    return hashlib.md5(text.encode()).hexdigest()


def _hash_query(query: str, mode: str = "consumer") -> str:
    """쿼리와 모드를 조합하여 해시합니다."""
    return hashlib.md5(f"{mode}:{query}".encode()).hexdigest()


# =============================================================================
# 임베딩 캐싱
# =============================================================================

def get_cached_embedding(text: str) -> Optional[List[float]]:
    """캐시된 임베딩을 가져옵니다."""
    key = _hash_text(text)
    return _embedding_cache.get(key)


def cache_embedding(text: str, embedding: List[float]) -> None:
    """임베딩을 캐시합니다."""
    key = _hash_text(text)
    _embedding_cache.set(key, embedding)


def get_embedding_cache_stats() -> Dict[str, Any]:
    """임베딩 캐시 통계를 반환합니다."""
    return _embedding_cache.stats


# =============================================================================
# 응답 캐싱
# =============================================================================

def get_cached_response(query: str, mode: str = "consumer") -> Optional[Dict[str, Any]]:
    """
    캐시된 응답을 가져옵니다.
    
    Args:
        query: 사용자 쿼리
        mode: consumer 또는 seller
    
    Returns:
        캐시된 응답 또는 None
    """
    key = _hash_query(query, mode)
    return _response_cache.get(key)


def cache_response(query: str, response: Dict[str, Any], mode: str = "consumer") -> None:
    """
    응답을 캐시합니다.
    
    Args:
        query: 사용자 쿼리
        response: 챗봇 응답 (answer, recommendations 등)
        mode: consumer 또는 seller
    """
    key = _hash_query(query, mode)
    _response_cache.set(key, response)


def get_response_cache_stats() -> Dict[str, Any]:
    """응답 캐시 통계를 반환합니다."""
    return _response_cache.stats


# =============================================================================
# 캐시 관리
# =============================================================================

def clear_all_caches() -> None:
    """모든 캐시를 비웁니다."""
    _embedding_cache.clear()
    _response_cache.clear()
    logger.info("All caches cleared")


def get_all_cache_stats() -> Dict[str, Dict[str, Any]]:
    """모든 캐시 통계를 반환합니다."""
    return {
        "embedding": get_embedding_cache_stats(),
        "response": get_response_cache_stats(),
    }


# =============================================================================
# 백그라운드 프리페치 (추측적 실행)
# =============================================================================

import asyncio
from concurrent.futures import ThreadPoolExecutor

_prefetch_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="prefetch")
_pending_prefetches: Dict[str, asyncio.Task] = {}


async def prefetch_rag_async(query: str, mode: str = "consumer") -> None:
    """
    RAG 검색을 백그라운드에서 미리 실행합니다.
    
    extract_query 노드에서 호출하여, full_classify가 실행되는 동안
    RAG 검색을 병렬로 수행합니다.
    """
    from app.tools.retrieval import consumer_retrieve_async, seller_retrieve_async
    
    key = _hash_query(query, mode)
    
    # 이미 캐시에 있거나 프리페치 중이면 스킵
    if _response_cache.get(key) is not None:
        return
    if key in _pending_prefetches:
        return
    
    async def _do_prefetch():
        try:
            if mode == "consumer":
                await consumer_retrieve_async.ainvoke({"query": query})
            else:
                await seller_retrieve_async.ainvoke({"query": query})
            logger.debug(f"Prefetch completed: {query[:30]}...")
        except Exception as e:
            logger.warning(f"Prefetch failed: {e}")
        finally:
            _pending_prefetches.pop(key, None)
    
    task = asyncio.create_task(_do_prefetch())
    _pending_prefetches[key] = task


def is_likely_consumer_query(query: str) -> bool:
    """
    consumer_query일 가능성이 높은지 휴리스틱으로 판단.
    
    True이면 RAG 프리페치를 시작합니다.
    """
    if not query:
        return False
    
    lowered = query.lower()
    
    # 확실히 non-consumer인 경우
    non_consumer_patterns = (
        "안녕", "하이", "hi", "hello",
        "뭐 해?", "넌 뭐야", "누구야",
        "서울", "부산", "대구", "인천",  # 타 지역
        "날씨", "뉴스", "주식",  # 범위 외
    )
    for pattern in non_consumer_patterns:
        if pattern in lowered:
            return False
    
    # consumer_query 가능성이 높은 경우
    consumer_patterns = (
        "플리마켓", "마켓", "야시장", "축제", "팝업",
        "추천", "알려", "어디", "뭐 있",
        "동구", "서구", "남구", "북구", "광산구",
        "광주", "주말", "오늘", "내일",
    )
    for pattern in consumer_patterns:
        if pattern in lowered:
            return True
    
    return False


__all__ = [
    "TTLCache",
    "get_cached_embedding",
    "cache_embedding",
    "get_embedding_cache_stats",
    "get_cached_response",
    "cache_response",
    "get_response_cache_stats",
    "clear_all_caches",
    "get_all_cache_stats",
    "prefetch_rag_async",
    "is_likely_consumer_query",
]

