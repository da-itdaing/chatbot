"""
Consumer 챗봇용 Pydantic 스키마 정의.

노드 간 데이터 교환 및 LLM structured output을 위한 모델들.
"""
from __future__ import annotations

from typing import List, Literal

from pydantic import BaseModel, Field

from app.graphs.shared.structured_query import (
    KeywordFilter,
    NumericFilter,
    SortSpec,
)


class FeasibilityDecision(BaseModel):
    """요청 처리 가능성 평가 결과."""

    code: Literal[
        "OK",
        "OUT_OF_SCOPE_REGION",
        "NOT_IMPLEMENTED",
        "INSUFFICIENT_DATA",
        "POLICY_RESTRICTED",
    ] = "OK"
    detail: str = ""
    risk_level: Literal["low", "medium", "high"] = "low"


class UnifiedIntentFeasibility(BaseModel):
    """
    Intent + Feasibility + Safety를 단일 LLM 호출로 수행하기 위한 통합 스키마.

    3가지 판단을 동시에 수행:
    1. Intent 분류 (greeting, consumer_query, safety_violation 등)
    2. Feasibility 평가 (OK, OUT_OF_SCOPE_REGION 등)
    3. Safety 체크 (Content Safety S1-S12, Jailbreak)
    """

    intent: Literal[
        "greeting",
        "bot_about",
        "chitchat",
        "consumer_query",
        "seller_query",
        "out_of_scope",
        "safety_violation",
        "noise",
    ] = "consumer_query"
    normalized_query: str = Field(
        default="",
        description="RAG/추론에 사용하기 좋은 형태로 다시 쓴 한국어 질문",
    )
    feasibility_code: Literal[
        "OK",
        "OUT_OF_SCOPE_REGION",
        "OUT_OF_SCOPE_TOPIC",
        "NOT_IMPLEMENTED",
        "INSUFFICIENT_DATA",
        "POLICY_RESTRICTED",
    ] = "OK"
    safety_category: Literal[
        "SAFE",
        "S1_VIOLENCE",
        "S3_CRIMINAL",
        "S4_WEAPONS",
        "S5_SUBSTANCES",
        "S6_SELF_HARM",
        "S8_HATE",
        "S9_PII",
        "S10_HARASSMENT",
        "S11_THREAT",
        "S12_PROFANITY",
        "JAILBREAK",
    ] = "SAFE"
    risk_level: Literal["low", "medium", "high"] = "low"
    detail: str = Field(
        default="",
        description="짧은 한국어 이유/설명",
    )


class Route(BaseModel):
    """라우팅 결정."""

    target: Literal["rag_answer", "general_answer"] = Field(
        description="Routing target for the user's query"
    )


class CaseWithPlan(BaseModel):
    """
    Case Classification + StructuredRetrievalPlan을 단일 LLM 호출로 통합한 스키마.

    기존 2회 LLM 호출 → 1회로 감소 (latency ~4초 → ~2초)
    """

    # CaseClassification 필드
    case: Literal["region_keyword", "date", "market_info", "amenity", "rating"] = Field(
        default="region_keyword",
        description="질문 유형 분류",
    )
    rewritten_query: str = Field(
        default="",
        description="벡터스토어 검색에 최적화된 한국어 쿼리 (광주광역시 컨텍스트 포함)",
    )

    # StructuredRetrievalPlan 필드
    target_entity: Literal["zone", "store", "either"] = Field(
        default="store",
        description="검색 대상 엔티티",
    )
    keyword_filters: List[KeywordFilter] = Field(
        default_factory=list,
        description="키워드 필터 목록",
    )
    numeric_filters: List[NumericFilter] = Field(
        default_factory=list,
        description="숫자 필터 목록",
    )
    exclude_districts: List[str] = Field(
        default_factory=list,
        description="제외할 광주광역시 구 목록 (동구, 서구, 남구, 북구, 광산구)",
    )
    sort: List[SortSpec] = Field(
        default_factory=list,
        description="정렬 기준",
    )
    allow_broadening: bool = Field(
        default=True,
        description="검색 결과 없을 때 조건 완화 허용 여부",
    )
    risk_level: Literal["low", "medium", "high"] = Field(
        default="low",
        description="타 지역/정책 우려 시 high",
    )
    rationale: str = Field(
        default="",
        description="검색 전략 설명",
    )


class HallucinationLabel(BaseModel):
    """할루시네이션 검사 결과."""

    label: Literal["hallucinated", "not hallucinated"]
    reason: str = ""


class FullClassification(BaseModel):
    """
    Intent + Feasibility + Safety + Case + Plan을 단일 LLM 호출로 통합한 스키마.
    
    classify_and_assess + classify_case_and_plan을 하나로 병합.
    LLM 호출 2회 → 1회로 감소 (~5초 → ~3초 예상)
    """
    
    # === Intent/Feasibility/Safety (모든 경로에서 사용) ===
    intent: Literal[
        "greeting",
        "bot_about",
        "chitchat",
        "consumer_query",
        "seller_query",
        "out_of_scope",
        "safety_violation",
        "noise",
    ] = "consumer_query"
    
    feasibility_code: Literal[
        "OK",
        "OUT_OF_SCOPE_REGION",
        "OUT_OF_SCOPE_TOPIC",
        "NOT_IMPLEMENTED",
        "INSUFFICIENT_DATA",
        "POLICY_RESTRICTED",
    ] = "OK"
    
    safety_category: Literal[
        "SAFE",
        "S1_VIOLENCE",
        "S3_CRIMINAL",
        "S4_WEAPONS",
        "S5_SUBSTANCES",
        "S6_SELF_HARM",
        "S8_HATE",
        "S9_PII",
        "S10_HARASSMENT",
        "S11_THREAT",
        "S12_PROFANITY",
        "JAILBREAK",
    ] = "SAFE"
    
    detail: str = Field(
        default="",
        description="짧은 한국어 이유/설명",
    )
    
    # === Case/Plan (consumer_query일 때만 유효) ===
    case: Literal["region_keyword", "date", "market_info", "amenity", "rating"] = Field(
        default="region_keyword",
        description="질문 유형 분류 (consumer_query일 때만)",
    )
    
    rewritten_query: str = Field(
        default="",
        description="검색에 최적화된 한국어 쿼리 (광주광역시 컨텍스트 포함)",
    )
    
    target_entity: Literal["zone", "store", "either"] = Field(
        default="store",
        description="소비자용이므로 항상 store",
    )
    
    keyword_filters: List[KeywordFilter] = Field(
        default_factory=list,
        description="키워드 필터 (market_ameni: 카페, 주차, 화장실 등)",
    )
    
    exclude_districts: List[str] = Field(
        default_factory=list,
        description="제외할 구 (동구, 서구, 남구, 북구, 광산구)",
    )
    
    allow_broadening: bool = Field(
        default=True,
        description="조건 완화 허용 (항상 True 권장)",
    )
    
    risk_level: Literal["low", "medium", "high"] = Field(
        default="low",
        description="위험 수준",
    )


__all__ = [
    "FeasibilityDecision",
    "UnifiedIntentFeasibility",
    "Route",
    "CaseWithPlan",
    "HallucinationLabel",
    "FullClassification",
]

