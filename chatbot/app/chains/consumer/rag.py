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
    키 로테이션 지원.
    """
    from app.utils.key_rotation import get_key_manager

    cfg = settings or get_settings()

    def _api_key_provider() -> str:
        """키 매니저에서 현재 활성 키 반환 (로테이션)"""
        key_manager = get_key_manager()
        return key_manager.get_current_key()

    # NOTE:
    # ChatOpenAI의 실제 __init__ 시그니처는 model / temperature / api_key /
    # max_completion_tokens를 모두 지원하지만, 일부 타입 스텁은 이를
    # 모르는 경우가 있어 call-arg 에러를 무시한다.
    llm = ChatOpenAI(  # type: ignore[call-arg]
        # 별도 RAG 모델이 지정되어 있으면 우선 사용하고, 없으면 기본 대화 모델 사용
        model=cfg.openai_rag_model or cfg.openai_model,
        temperature=0,
        api_key=_api_key_provider,
        # LangChain OpenAI Chat에서는 max_completion_tokens가 completion 길이 상한으로 사용된다.
        max_completion_tokens=cfg.rag_max_completion_tokens,
    )

    return CONSUMER_RAG_PROMPT | llm


__all__ = ["build_consumer_rag_chain"]

