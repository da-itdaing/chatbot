from __future__ import annotations

"""
Intent 및 Policy 결정을 표현하는 공용 스키마.

- IntentDecision: 사용자의 질문이 어떤 종류인지(high-level intent) 분류
- PolicyDecision: 지역/정책/기능 관점에서 처리 가능 여부를 코드로 표현
"""

from typing import Literal

from pydantic import BaseModel, Field


class IntentDecision(BaseModel):
    """
    High-level intent classifier output for a single user utterance.
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
        "",
        description=(
            "RAG/추론에 사용하기 좋은 형태로 다시 쓴 한국어 질문. "
            "비어 있으면 원본 query 를 그대로 사용합니다."
        ),
    )


class PolicyDecision(BaseModel):
    """
    Guardrail/feasibility evaluator output.
    """

    code: Literal[
        "OK",
        "META_SERVICE",
        "OUT_OF_SCOPE_REGION",
        "OUT_OF_SCOPE_TOPIC",
        "NOT_IMPLEMENTED",
        "INSUFFICIENT_DATA",
        "POLICY_RESTRICTED",
    ] = "OK"
    reason: str = Field(
        "",
        description="간단한 한국어 이유/설명 (사용자에게 일부 노출 가능)",
    )
    severity: Literal["low", "medium", "high"] = "low"


__all__ = ["IntentDecision", "PolicyDecision"]


