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
    키 로테이션 지원.
    """
    from app.utils.key_rotation import get_key_manager

    cfg = settings or get_settings()

    def _api_key_provider() -> str:
        """키 매니저에서 현재 활성 키 반환 (로테이션)"""
        key_manager = get_key_manager()
        return key_manager.get_current_key()

    # NOTE:
    # Runtime ChatOpenAI __init__ signature(printed via inspect) supports
    # model / temperature / api_key / max_completion_tokens. Some type
    # checkers ship older stubs that don't know these keyword names, so we
    # explicitly ignore call-arg type errors here.
    llm = ChatOpenAI(  # type: ignore[call-arg]
        model=cfg.openai_rag_model or cfg.openai_model,
        temperature=0,
        api_key=_api_key_provider,
        max_completion_tokens=cfg.rag_max_completion_tokens,
    )

    return SELLER_RAG_PROMPT | llm


__all__ = ["build_seller_rag_chain"]

