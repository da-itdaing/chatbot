from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, List, Sequence

from langchain_core.documents import Document
from langchain_core.tools import tool

from app.config import Settings, get_settings
from app.db.postgres import get_markets_vectorstore, get_zones_vectorstore
from app.graphs.shared import (
    extend_with_web_results,
    extend_with_web_results_async,
)
from app.utils.search import WebSearchClient


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
) -> str:
    payload = {
        "type": label,
        "query": query,
        "count": len(docs),
        "documents": _serialize_documents(docs),
    }
    return json.dumps(payload, ensure_ascii=False)


@tool("consumer_retrieve", return_direct=False)
def consumer_retrieve(query: str) -> str:
    """광주 플리마켓/팝업 정보를 찾는다. 마켓 설명, 위치, 분위기, 운영 정보를 반환한다."""
    if not query.strip():
        return json.dumps(
            {"type": "consumer_retrieve", "query": query, "count": 0, "documents": []},
            ensure_ascii=False,
        )
    docs = _consumer.run(query.strip())
    return _format_result(query, docs, "consumer_retrieve")


@tool("consumer_retrieve_async", return_direct=False)
async def consumer_retrieve_async(query: str) -> str:
    """(Async) 광주 플리마켓/팝업 정보를 찾는다."""
    if not query.strip():
        return json.dumps(
            {"type": "consumer_retrieve", "query": query, "count": 0, "documents": []},
            ensure_ascii=False,
        )
    docs = await _consumer.arun(query.strip())
    return _format_result(query, docs, "consumer_retrieve")


@tool("seller_retrieve", return_direct=False)
def seller_retrieve(query: str) -> str:
    """셀러 전용 존/상권 데이터를 찾는다. 인구, 카테고리 적합도, 추천 존을 반환한다."""
    if not query.strip():
        return json.dumps(
            {"type": "seller_retrieve", "query": query, "count": 0, "documents": []},
            ensure_ascii=False,
        )
    docs = _seller.run(query.strip())
    return _format_result(query, docs, "seller_retrieve")


@tool("seller_retrieve_async", return_direct=False)
async def seller_retrieve_async(query: str) -> str:
    """(Async) 셀러 전용 존/상권 데이터를 찾는다."""
    if not query.strip():
        return json.dumps(
            {"type": "seller_retrieve", "query": query, "count": 0, "documents": []},
            ensure_ascii=False,
        )
    docs = await _seller.arun(query.strip())
    return _format_result(query, docs, "seller_retrieve")


__all__ = [
    "consumer_retrieve",
    "consumer_retrieve_async",
    "seller_retrieve",
    "seller_retrieve_async",
]

