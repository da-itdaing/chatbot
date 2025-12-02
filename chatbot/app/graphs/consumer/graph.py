from __future__ import annotations

"""
Consumer LangGraph 빌더 (Async-only).

2025-12 v7 최적화:
- classify_and_assess + classify_case_and_plan → full_classify_async (LLM 호출 2회 → 1회)
- 총 LLM 호출: 기존 3-4회 → 2-3회로 감소

2025-12 v11 추가:
- 하이브리드 RAG + SQL: popup_sql_lookup_async, zone_sql_lookup_async 도구 추가
- 정확한 날짜/시간/상태 정보가 필요할 때 SQL 직접 조회
"""

from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode

from app.tools import (
    consumer_retrieve_async,
    web_search_async,
)
from app.tools.sql_lookup import (
    popup_sql_lookup_async,
)

from app.graphs.consumer.state import AgentState
from app.graphs.consumer.nodes import (
    basic_generate_async,
    check_hallucination_async,
    extract_user_query,
    format_answer_message,
    full_classify_async,
    full_router,
    generate_async,
    hallucination_router,
    schedule_consumer_tool_async,
    consumer_tool_router,
    consumer_tool_followup_router,
    consume_consumer_tool_result,
    rewrite_async,
    summarize_messages_async,
    truncate_messages,
)


def build_consumer_graph_async(checkpointer=None):
    """
    Async-first LangGraph 빌더 (유일한 Consumer 그래프 빌더).

    v7 최적화:
    - full_classify_async: intent + feasibility + safety + case + plan을 단일 LLM 호출로 처리
    - 총 LLM 호출: 기존 3-4회 → 2-3회로 감소 (~8.9초 → ~6초 예상)

    Args:
        checkpointer: External checkpointer (e.g. AsyncPostgresSaver)
    """

    graph_builder = StateGraph(AgentState)
    # v11: 하이브리드 RAG + SQL 도구
    tool_node = ToolNode([
        consumer_retrieve_async,  # RAG 검색
        web_search_async,         # 웹 검색
        popup_sql_lookup_async,   # 정형 DB 조회 (정확한 날짜/시간)
    ])

    # 노드 등록
    graph_builder.add_node("extract_query", extract_user_query)
    graph_builder.add_node("full_classify", full_classify_async)
    graph_builder.add_node("schedule_tool", schedule_consumer_tool_async)
    graph_builder.add_node("consume_tool", consume_consumer_tool_result)
    graph_builder.add_node("tool_executor", tool_node)
    graph_builder.add_node("generate", generate_async)
    graph_builder.add_node("check_hallucination", check_hallucination_async)
    graph_builder.add_node("rewrite", rewrite_async)
    graph_builder.add_node("basic_generate", basic_generate_async)
    graph_builder.add_node("format_answer", format_answer_message)
    graph_builder.add_node("summarize_messages", summarize_messages_async)
    graph_builder.add_node("truncate_messages", truncate_messages)

    # 엣지 연결
    graph_builder.add_edge(START, "extract_query")
    graph_builder.add_edge("extract_query", "full_classify")
    graph_builder.add_conditional_edges(
        "full_classify",
        full_router,
        {
            "rag_answer": "schedule_tool",  # full_classify에서 이미 plan 설정됨
            "general_answer": "basic_generate",
        },
    )
    graph_builder.add_conditional_edges(
        "schedule_tool",
        consumer_tool_router,
        {
            "tools": "tool_executor",
            "resume": "generate",
        },
    )
    graph_builder.add_edge("tool_executor", "consume_tool")
    graph_builder.add_conditional_edges(
        "consume_tool",
        consumer_tool_followup_router,
        {
            "more_tools": "schedule_tool",
            "continue": "generate",
        },
    )
    graph_builder.add_edge("generate", "check_hallucination")
    graph_builder.add_conditional_edges(
        "check_hallucination",
        hallucination_router,
        {
            "not hallucinated": "format_answer",
            "hallucinated": "rewrite",
        },
    )
    graph_builder.add_edge("rewrite", "schedule_tool")
    graph_builder.add_edge("basic_generate", "format_answer")
    graph_builder.add_edge("format_answer", "summarize_messages")
    graph_builder.add_edge("summarize_messages", "truncate_messages")
    graph_builder.add_edge("truncate_messages", END)

    return graph_builder.compile(checkpointer=checkpointer)


# Backward compatibility: build_consumer_graph는 async 버전을 반환
build_consumer_graph = build_consumer_graph_async


__all__ = ["build_consumer_graph", "build_consumer_graph_async", "AgentState"]
