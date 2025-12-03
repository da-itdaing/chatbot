"""
Consumer 챗봇용 LLM 체인 정의.

LLM 인스턴스와 체인 조합을 정의한다.
"""
from __future__ import annotations

from langchain_core.output_parsers import StrOutputParser
from langchain_openai import ChatOpenAI

from app.chains.consumer import build_consumer_rag_chain
from app.config import get_settings
from app.graphs.shared import StructuredRetrievalPlan

from .prompts import (
    basic_prompt,
    case_with_plan_prompt,
    full_classification_prompt,
    hallucination_prompt,
    rewrite_prompt,
    unified_classify_prompt,
)
from .schemas import (
    CaseWithPlan,
    FullClassification,
    HallucinationLabel,
    UnifiedIntentFeasibility,
)


settings = get_settings()


def _llm(temperature: float = 0.0) -> ChatOpenAI:
    """LLM 인스턴스 생성 헬퍼 (키 로테이션 지원)."""
    from app.utils.key_rotation import get_key_manager

    def _api_key_provider() -> str:
        """키 매니저에서 현재 활성 키 반환 (로테이션)"""
        key_manager = get_key_manager()
        return key_manager.get_current_key()

    return ChatOpenAI(  # type: ignore[call-arg]
        model=settings.openai_model,
        temperature=temperature,
        api_key=_api_key_provider,
    )


# ---------------------------------------------------------------------------
# LLM 인스턴스들
# ---------------------------------------------------------------------------

hallucination_llm = _llm(temperature=0)
rewrite_llm = _llm(temperature=0)
basic_llm = _llm(temperature=0.3)
summary_llm = _llm(temperature=0)
unified_classify_llm = _llm(temperature=0)
case_with_plan_llm = _llm(temperature=0)
full_classification_llm = _llm(temperature=0)


# ---------------------------------------------------------------------------
# 체인 정의
# ---------------------------------------------------------------------------

# 통합 분류 체인 (Intent + Feasibility + Safety)
unified_classify_chain = (
    unified_classify_prompt
    | unified_classify_llm.with_structured_output(UnifiedIntentFeasibility)
)

# Case + Plan 통합 체인
case_with_plan_chain = (
    case_with_plan_prompt
    | case_with_plan_llm.with_structured_output(CaseWithPlan)
)

# 완전 통합 체인 (Intent + Feasibility + Safety + Case + Plan)
full_classification_chain = (
    full_classification_prompt
    | full_classification_llm.with_structured_output(FullClassification)
)

# 할루시네이션 검사 체인
hallucination_chain = (
    hallucination_prompt
    | hallucination_llm.with_structured_output(HallucinationLabel)
)

# 쿼리 재작성 체인
rewrite_chain = rewrite_prompt | rewrite_llm | StrOutputParser()

# 기본 응답 체인 (non-RAG)
basic_chain = basic_prompt | basic_llm | StrOutputParser()

# RAG 체인
rag_chain = build_consumer_rag_chain(settings)


__all__ = [
    # LLM 인스턴스
    "hallucination_llm",
    "rewrite_llm",
    "basic_llm",
    "summary_llm",
    "unified_classify_llm",
    "case_with_plan_llm",
    "full_classification_llm",
    # 체인
    "unified_classify_chain",
    "case_with_plan_chain",
    "full_classification_chain",
    "hallucination_chain",
    "rewrite_chain",
    "basic_chain",
    "rag_chain",
]

