from __future__ import annotations

import json
from typing import Any, Dict, List

from langchain_core.tools import tool

from app.config import Settings, get_settings
from app.utils.search import WebSearchClient


def _serialize_results(results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    serialized: List[Dict[str, Any]] = []
    for row in results:
        serialized.append(
            {
                "page_content": row.get("page_content", ""),
                "metadata": dict(row.get("metadata", {})),
            }
        )
    return serialized


class _WebSearchTool:
    def __init__(self, settings: Settings) -> None:
        self._client = WebSearchClient(settings)

    def run(self, query: str) -> List[Dict[str, Any]]:
        docs = self._client.search_sync(query)
        return [  # Document → dict
            {"page_content": doc.page_content, "metadata": dict(doc.metadata or {})}
            for doc in docs
        ]

    async def arun(self, query: str) -> List[Dict[str, Any]]:
        docs = await self._client.search_async(query)
        return [
            {"page_content": doc.page_content, "metadata": dict(doc.metadata or {})}
            for doc in docs
        ]


_settings = get_settings()
_web_tool = _WebSearchTool(_settings)


def _format_payload(query: str, docs: List[Dict[str, Any]]) -> str:
    payload = {
        "type": "web_search",
        "query": query,
        "count": len(docs),
        "documents": _serialize_results(docs),
    }
    return json.dumps(payload, ensure_ascii=False)


@tool("web_search", return_direct=False)
def web_search(query: str) -> str:
    """DuckDuckGo 기반 최신 정보를 검색한다. 광주 외 지역/최신 뉴스/일반 정보를 얻을 때 사용한다."""
    if not query.strip():
        return json.dumps(
            {"type": "web_search", "query": query, "count": 0, "documents": []},
            ensure_ascii=False,
        )
    docs = _web_tool.run(query.strip())
    return _format_payload(query, docs)


@tool("web_search_async", return_direct=False)
async def web_search_async(query: str) -> str:
    """(Async) DuckDuckGo 기반 최신 정보를 검색한다."""
    if not query.strip():
        return json.dumps(
            {"type": "web_search", "query": query, "count": 0, "documents": []},
            ensure_ascii=False,
        )
    docs = await _web_tool.arun(query.strip())
    return _format_payload(query, docs)


__all__ = ["web_search", "web_search_async"]

