from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from typing import Any, Dict, List, Optional, Sequence

from langchain_core.documents import Document
from langchain_core.tools import tool

from pydantic import ValidationError

from app.config import Settings, get_settings
from app.graphs.shared import StructuredRetrievalPlan, apply_structured_plan
from app.db.postgres import get_markets_vectorstore, get_zones_vectorstore
from app.graphs.shared import (
    extend_with_web_results,
    extend_with_web_results_async,
)
from app.utils.search import WebSearchClient
from app.utils.cache import TTLCache
from app.utils.db_cache import (
    get_from_db_cache,
    set_to_db_cache,
)

logger = logging.getLogger(__name__)

# RAG 결과 캐싱 (쿼리 → 검색 결과)
# L1: 메모리 캐시 (빠름, 워커별)
# L2: DB 캐시 (느리지만 워커 간 공유)
_rag_cache = TTLCache(maxsize=500, ttl_seconds=1800)  # 30분 TTL


def _serialize_documents(docs: Sequence[Document]) -> List[Dict[str, Any]]:
    serialized: List[Dict[str, Any]] = []
    for doc in docs:
        serialized.append(
            {
                "page_content": doc.page_content,
                "metadata": dict(doc.metadata or {}),
            }
        )
    return serialized


def _cache_key(query: str, mode: str, plan_hash: str = "") -> str:
    """캐시 키 생성."""
    return hashlib.md5(f"{mode}:{query}:{plan_hash}".encode()).hexdigest()


def _get_cached_docs(key: str) -> Optional[List[Document]]:
    """캐시된 문서 목록 반환."""
    cached = _rag_cache.get(key)
    if cached is None:
        return None
    # 역직렬화: dict → Document
    return [
        Document(page_content=d["page_content"], metadata=d["metadata"])
        for d in cached
    ]


def _cache_docs(key: str, docs: Sequence[Document]) -> None:
    """문서 목록 캐싱."""
    _rag_cache.set(key, _serialize_documents(docs))


class _ConsumerRetriever:
    def __init__(self, settings: Settings) -> None:
        vectorstore = get_markets_vectorstore(settings)
        self._retriever = vectorstore.as_retriever(
            search_type="similarity",
            search_kwargs={"k": settings.rag_top_k},
        )
        self._web = WebSearchClient(settings)

    def run(self, query: str) -> List[Document]:
        docs = self._retriever.invoke(query)
        return extend_with_web_results(docs, query, self._web)

    async def arun(self, query: str) -> List[Document]:
        # PGVector retriever의 async 경로는 langchain_postgres의 async 엔진 설정이
        # 필요하고, 현재 설정은 동기 엔진 기준이므로 안전하게 동기 호출을
        # 스레드 풀에서 실행한다.
        loop = asyncio.get_running_loop()
        docs = await loop.run_in_executor(None, self._retriever.invoke, query)
        return await extend_with_web_results_async(docs, query, self._web)


class _SellerRetriever:
    def __init__(self, settings: Settings) -> None:
        vectorstore = get_zones_vectorstore(settings)
        self._retriever = vectorstore.as_retriever(
            search_type="similarity",
            search_kwargs={"k": settings.zone_rag_top_k},
        )
        self._web = WebSearchClient(settings)

    def run(self, query: str) -> List[Document]:
        docs = self._retriever.invoke(query)
        return extend_with_web_results(docs, query, self._web)

    async def arun(self, query: str) -> List[Document]:
        # Seller RAG 역시 PGVector 동기 엔진을 사용하므로, async API 대신
        # 동기 검색을 스레드 풀에서 실행한다.
        loop = asyncio.get_running_loop()
        docs = await loop.run_in_executor(None, self._retriever.invoke, query)
        return await extend_with_web_results_async(docs, query, self._web)


_settings = get_settings()
_consumer = _ConsumerRetriever(_settings)
_seller = _SellerRetriever(_settings)


def _format_result(
    query: str,
    docs: Sequence[Document],
    label: str,
    extras: Dict[str, Any] | None = None,
) -> str:
    payload = {
        "type": label,
        "query": query,
        "count": len(docs),
        "documents": _serialize_documents(docs),
    }
    if extras:
        payload.update(extras)
    return json.dumps(payload, ensure_ascii=False)


def _apply_structured_plan_arg(
    docs: Sequence[Document],
    structured_plan: Dict[str, Any] | None,
) -> tuple[List[Document], Dict[str, Any], str]:
    plan_obj: StructuredRetrievalPlan | None = None
    if structured_plan:
        try:
            plan_obj = StructuredRetrievalPlan.model_validate(structured_plan)
        except (ValidationError, ValueError):
            plan_obj = None
    result = apply_structured_plan(docs, plan_obj)
    extras: Dict[str, Any] = {
        "structured_plan_result": result.label,
    }
    if plan_obj:
        extras["structured_plan"] = plan_obj.model_dump()
    return result.documents, extras, result.label


@tool("consumer_retrieve", return_direct=False)
def consumer_retrieve(
    query: str,
    structured_plan: Dict[str, Any] | None = None,
) -> str:
    """광주 플리마켓/팝업 정보를 찾는다. 마켓 설명, 위치, 분위기, 운영 정보를 반환한다."""
    if not query.strip():
        return json.dumps(
            {"type": "consumer_retrieve", "query": query, "count": 0, "documents": []},
            ensure_ascii=False,
        )
    docs = _consumer.run(query.strip())
    docs, extras, _ = _apply_structured_plan_arg(docs, structured_plan)
    return _format_result(query, docs, "consumer_retrieve", extras)


@tool("consumer_retrieve_async", return_direct=False)
async def consumer_retrieve_async(
    query: str,
    structured_plan: Dict[str, Any] | None = None,
) -> str:
    """
    (Async) 광주 플리마켓/팝업 정보를 찾는다.
    
    v13: 2단계 캐싱 적용
    - L1: 메모리 캐시 (빠름, 워커별)
    - L2: DB 캐시 (느리지만 워커 간 공유)
    """
    if not query.strip():
        return json.dumps(
            {"type": "consumer_retrieve", "query": query, "count": 0, "documents": []},
            ensure_ascii=False,
        )
    
    query_clean = query.strip()
    plan_hash = hashlib.md5(json.dumps(structured_plan or {}, sort_keys=True).encode()).hexdigest()[:8]
    cache_key = _cache_key(query_clean, "consumer", plan_hash)
    
    # L1: 메모리 캐시 확인
    cached_docs = _get_cached_docs(cache_key)
    if cached_docs is not None:
        logger.debug(f"L1 cache hit: {query_clean[:30]}...")
        docs, extras, _ = _apply_structured_plan_arg(cached_docs, structured_plan)
        extras["cache_hit"] = "L1_memory"
        return _format_result(query, docs, "consumer_retrieve", extras)
    
    # L2: DB 캐시 확인
    try:
        db_cached = await get_from_db_cache(cache_key)
        if db_cached is not None:
            logger.debug(f"L2 cache hit: {query_clean[:30]}...")
            # DB 캐시에서 가져온 데이터를 메모리 캐시에 저장 (L1 채우기)
            _rag_cache.set(cache_key, db_cached)
            cached_docs = [
                Document(page_content=d["page_content"], metadata=d["metadata"])
                for d in db_cached
            ]
            docs, extras, _ = _apply_structured_plan_arg(cached_docs, structured_plan)
            extras["cache_hit"] = "L2_db"
            return _format_result(query, docs, "consumer_retrieve", extras)
    except Exception as e:
        logger.warning(f"L2 cache read error: {e}")
    
    # 캐시 미스: RAG 검색 실행
    docs = await _consumer.arun(query_clean)
    
    # 캐싱 (structured_plan 적용 전 원본 문서)
    serialized = _serialize_documents(docs)
    _cache_docs(cache_key, docs)  # L1 캐싱
    
    # L2 캐싱 (비동기, 백그라운드)
    try:
        await set_to_db_cache(cache_key, serialized, ttl_seconds=1800)
    except Exception as e:
        logger.warning(f"L2 cache write error: {e}")
    
    docs, extras, _ = _apply_structured_plan_arg(docs, structured_plan)
    extras["cache_hit"] = False
    return _format_result(query, docs, "consumer_retrieve", extras)


@tool("seller_retrieve", return_direct=False)
def seller_retrieve(
    query: str,
    structured_plan: Dict[str, Any] | None = None,
) -> str:
    """셀러 전용 존/상권 데이터를 찾는다. 인구, 카테고리 적합도, 추천 존을 반환한다."""
    if not query.strip():
        return json.dumps(
            {"type": "seller_retrieve", "query": query, "count": 0, "documents": []},
            ensure_ascii=False,
        )
    docs = _seller.run(query.strip())
    docs, extras, _ = _apply_structured_plan_arg(docs, structured_plan)
    return _format_result(query, docs, "seller_retrieve", extras)


@tool("seller_retrieve_async", return_direct=False)
async def seller_retrieve_async(
    query: str,
    structured_plan: Dict[str, Any] | None = None,
) -> str:
    """
    (Async) 셀러 전용 존/상권 데이터를 찾는다.
    
    v13: 2단계 캐싱 적용
    - L1: 메모리 캐시 (빠름, 워커별)
    - L2: DB 캐시 (느리지만 워커 간 공유)
    """
    if not query.strip():
        return json.dumps(
            {"type": "seller_retrieve", "query": query, "count": 0, "documents": []},
            ensure_ascii=False,
        )
    
    query_clean = query.strip()
    plan_hash = hashlib.md5(json.dumps(structured_plan or {}, sort_keys=True).encode()).hexdigest()[:8]
    cache_key = _cache_key(query_clean, "seller", plan_hash)
    
    # L1: 메모리 캐시 확인
    cached_docs = _get_cached_docs(cache_key)
    if cached_docs is not None:
        logger.debug(f"L1 cache hit: {query_clean[:30]}...")
        docs, extras, _ = _apply_structured_plan_arg(cached_docs, structured_plan)
        extras["cache_hit"] = "L1_memory"
        return _format_result(query, docs, "seller_retrieve", extras)
    
    # L2: DB 캐시 확인
    try:
        db_cached = await get_from_db_cache(cache_key)
        if db_cached is not None:
            logger.debug(f"L2 cache hit: {query_clean[:30]}...")
            _rag_cache.set(cache_key, db_cached)
            cached_docs = [
                Document(page_content=d["page_content"], metadata=d["metadata"])
                for d in db_cached
            ]
            docs, extras, _ = _apply_structured_plan_arg(cached_docs, structured_plan)
            extras["cache_hit"] = "L2_db"
            return _format_result(query, docs, "seller_retrieve", extras)
    except Exception as e:
        logger.warning(f"L2 cache read error: {e}")
    
    # 캐시 미스: RAG 검색 실행
    docs = await _seller.arun(query_clean)
    
    # 캐싱
    serialized = _serialize_documents(docs)
    _cache_docs(cache_key, docs)  # L1
    
    try:
        await set_to_db_cache(cache_key, serialized, ttl_seconds=1800)  # L2
    except Exception as e:
        logger.warning(f"L2 cache write error: {e}")
    
    docs, extras, _ = _apply_structured_plan_arg(docs, structured_plan)
    extras["cache_hit"] = False
    return _format_result(query, docs, "seller_retrieve", extras)


__all__ = [
    "consumer_retrieve",
    "consumer_retrieve_async",
    "seller_retrieve",
    "seller_retrieve_async",
]

