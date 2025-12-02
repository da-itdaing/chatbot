"""
Consumer 챗봇용 상태 정의.

LangGraph의 AgentState를 정의한다.
"""
from __future__ import annotations

from typing import Any, Dict, List

from langchain_core.documents import Document
from langgraph.graph import MessagesState
from typing_extensions import NotRequired


class AgentState(MessagesState):
    """Conversation-aware state for the consumer RAG workflow."""

    summary: NotRequired[str]
    query: NotRequired[str]
    case: NotRequired[str]
    paraphrased_query: NotRequired[str]
    context: NotRequired[List[Document]]
    answer: NotRequired[str]
    hallucination_label: NotRequired[str]
    hallucination_reason: NotRequired[str]
    pending_tool_call_id: NotRequired[str]
    pending_tool_name: NotRequired[str]
    pending_tool_query: NotRequired[str]
    needs_web_search: NotRequired[bool]
    web_search_attempted: NotRequired[bool]
    last_tool_payload: NotRequired[Dict[str, Any]]
    recommendations: NotRequired[List[Dict[str, Any]]]
    structured_plan: NotRequired[Dict[str, Any]]
    structured_plan_result: NotRequired[str]
    entity_target: NotRequired[str | None]
    # Lightweight intent label used for routing before full LLM reasoning.
    intent: NotRequired[str]
    policy_code: NotRequired[str | None]
    policy_reason: NotRequired[str | None]
    fallback_code: NotRequired[str | None]
    fallback_detail: NotRequired[str | None]
    risk_level: NotRequired[str | None]
    force_hallucination_check: NotRequired[bool]
    policy_notes: NotRequired[str]
    rewrite_count: NotRequired[int]  # rewrite 루프 횟수 제한용
    is_realtime_query: NotRequired[bool]  # 날씨/실시간 정보 질문 여부
    is_gwangju_general: NotRequired[bool]  # 광주 일반 질문 (관광지 등)


__all__ = ["AgentState"]

