from __future__ import annotations

from typing import Optional

from langchain_core.runnables import Runnable
from langchain_openai import ChatOpenAI

from app.config import Settings, get_settings
from app.chains.shared import CONSUMER_RAG_PROMPT


def build_consumer_rag_chain(settings: Optional[Settings] = None) -> Runnable:
    """
    기존 consumer LangGraph에서 사용하던 RAG 응답 체인을 그대로 래핑한다.
    프롬프트/LLM/실행 순서를 변경하지 않고 모듈화만 수행한다.
    """

    cfg = settings or get_settings()

    def _api_key_provider() -> str:
        return cfg.openai_api_key

    llm = ChatOpenAI(
        model=cfg.openai_model,
        temperature=0,
        api_key=_api_key_provider,
    )

    return CONSUMER_RAG_PROMPT | llm


__all__ = ["build_consumer_rag_chain"]

