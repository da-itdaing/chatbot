"""
Consumer 챗봇 그래프 모듈.

모듈 구조:
- state.py: AgentState 정의
- schemas.py: Pydantic 스키마 (UnifiedIntentFeasibility, CaseWithPlan 등)
- prompts.py: 프롬프트 템플릿
- chains.py: LLM 인스턴스 및 체인
- nodes.py: 그래프 노드 함수
- graph.py: LangGraph 빌더
"""
from app.graphs.consumer.state import AgentState
from app.graphs.consumer.schemas import (
    CaseWithPlan,
    FeasibilityDecision,
    HallucinationLabel,
    Route,
    UnifiedIntentFeasibility,
)
from app.graphs.consumer.prompts import (
    BASIC_SYSTEM_PROMPT,
    CASE_WITH_PLAN_SYSTEM_PROMPT,
    UNIFIED_CLASSIFY_SYSTEM_PROMPT,
)
from app.graphs.consumer.chains import (
    basic_chain,
    case_with_plan_chain,
    hallucination_chain,
    rag_chain,
    rewrite_chain,
    unified_classify_chain,
)
from app.graphs.consumer.graph import build_consumer_graph, build_consumer_graph_async

__all__ = [
    # State
    "AgentState",
    # Schemas
    "CaseWithPlan",
    "FeasibilityDecision",
    "HallucinationLabel",
    "Route",
    "UnifiedIntentFeasibility",
    # Prompts
    "BASIC_SYSTEM_PROMPT",
    "CASE_WITH_PLAN_SYSTEM_PROMPT",
    "UNIFIED_CLASSIFY_SYSTEM_PROMPT",
    # Chains
    "basic_chain",
    "case_with_plan_chain",
    "hallucination_chain",
    "rag_chain",
    "rewrite_chain",
    "unified_classify_chain",
    # Graph
    "build_consumer_graph",
    "build_consumer_graph_async",
]

