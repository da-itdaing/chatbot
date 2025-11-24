from __future__ import annotations

import asyncio
import logging
from typing import List, Sequence

from langchain_community.tools import DuckDuckGoSearchRun
from langchain_core.documents import Document

from app.config import Settings, get_settings

logger = logging.getLogger(__name__)


class WebSearchClient:
    """
    Thin wrapper around DuckDuckGoSearchRun (or future providers).

    - Settings 플래그(`WEBSEARCH_ENABLED`)로 활성화/비활성화 제어
    - 동기/비동기 실행 함수를 모두 제공해 LangGraph sync/async 그래프에서 재사용
    """

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._enabled = bool(self._settings.websearch_enabled)
        self._provider = (self._settings.websearch_provider or "duckduckgo").lower()
        self._top_k = max(1, int(self._settings.websearch_top_k or 1))

        if not self._enabled:
            self._duck: DuckDuckGoSearchRun | None = None
            return

        if self._provider != "duckduckgo":
            logger.warning(
                "Unsupported WEBSEARCH_PROVIDER=%s. Falling back to DuckDuckGo.",
                self._provider,
            )
        self._duck = DuckDuckGoSearchRun(k=self._top_k)

    @property
    def enabled(self) -> bool:
        return self._enabled and self._duck is not None

    def search_sync(self, query: str) -> List[Document]:
        if not self.enabled or not query.strip():
            return []
        try:
            raw = self._duck.run(query.strip())  # type: ignore[union-attr]
        except Exception as exc:  # pragma: no cover - network errors
            logger.warning("DuckDuckGo search failed: %s", exc)
            return []
        return self._to_documents(raw, query)

    async def search_async(self, query: str) -> List[Document]:
        if not self.enabled or not query.strip():
            return []
        loop = asyncio.get_running_loop()
        try:
            raw = await loop.run_in_executor(
                None,
                self._duck.run,  # type: ignore[union-attr]
                query.strip(),
            )
        except Exception as exc:  # pragma: no cover - network errors
            logger.warning("DuckDuckGo search failed (async): %s", exc)
            return []
        return self._to_documents(raw, query)

    def _to_documents(self, raw: str, query: str) -> List[Document]:
        text = (raw or "").strip()
        if not text:
            return []

        # DuckDuckGoSearchRun은 다수의 URL/요약을 한 문자열로 반환하므로,
        # 빈 줄을 기준으로 segment를 나눈 뒤 최대 top_k 조각만 사용한다.
        segments: Sequence[str] = [
            segment.strip() for segment in text.split("\n\n") if segment.strip()
        ]
        if not segments:
            segments = [text]

        documents: List[Document] = []
        for rank, segment in enumerate(segments[: self._top_k], start=1):
            documents.append(
                Document(
                    page_content=segment,
                    metadata={
                        "source": "duckduckgo",
                        "query": query,
                        "rank": rank,
                    },
                )
            )
        return documents


__all__ = ["WebSearchClient"]


