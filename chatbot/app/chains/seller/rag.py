from __future__ import annotations

from typing import Optional

from langchain_core.runnables import Runnable
from langchain_openai import ChatOpenAI

from app.config import Settings, get_settings
from app.chains.shared import SELLER_RAG_PROMPT


def build_seller_rag_chain(settings: Optional[Settings] = None) -> Runnable:
    """
    Seller LangGraph에서 사용하던 RAG 생성 체인을 모듈화한 함수.
    프롬프트/LLM 구성은 기존 그래프 정의와 동일하다.
    """

    cfg = settings or get_settings()

    def _api_key_provider() -> str:
        return cfg.openai_api_key

    llm = ChatOpenAI(
        model=cfg.openai_model,
        temperature=0,
        api_key=_api_key_provider,
    )

    return SELLER_RAG_PROMPT | llm


__all__ = ["build_seller_rag_chain"]

