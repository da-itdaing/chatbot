from __future__ import annotations

from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode

from app.tools import (
    seller_retrieve,
    seller_retrieve_async,
    web_search,
    web_search_async,
)

from .nodes import (
    AgentState,
    analyze_zone_performance,
    basic_generate,
    basic_generate_async,
    case_classification,
    case_classification_async,
    assess_feasibility,
    assess_feasibility_async,
    check_hallucination,
    check_hallucination_async,
    check_allowed_categories,
    check_allowed_categories_async,
    classify_intent_node,
    extract_user_query,
    format_answer_message,
    generate,
    generate_async,
    hallucination_router,
    plan_structured_search,
    plan_structured_search_async,
    schedule_seller_tool,
    schedule_seller_tool_async,
    seller_tool_router,
    seller_tool_followup_router,
    consume_seller_tool_result,
    rewrite,
    rewrite_async,
    router,
    router_async,
    summarize_messages,
    summarize_messages_async,
    truncate_messages,
    # 통합 노드
    full_classify_seller_async,
    full_seller_router,
)


def build_seller_graph(checkpointer=None):
    """
    Build a LangGraph StateGraph for the seller chatbot, preserving the original
    bot4s flow while allowing an external checkpointer (예: AsyncPostgresSaver)를 주입.
    """

    graph_builder = StateGraph(AgentState)
    tool_node = ToolNode([seller_retrieve, web_search])

    graph_builder.add_node("extract_query", extract_user_query)
    graph_builder.add_node("classify_intent", classify_intent_node)
    graph_builder.add_node("assess_feasibility", assess_feasibility)
    graph_builder.add_node("case_classification", case_classification)
    graph_builder.add_node("plan_structured_search", plan_structured_search)
    graph_builder.add_node("schedule_tool", schedule_seller_tool)
    graph_builder.add_node("consume_tool", consume_seller_tool_result)
    graph_builder.add_node("tool_executor", tool_node)
    graph_builder.add_node("analyze_zone_performance", analyze_zone_performance)
    graph_builder.add_node("check_allowed_categories", check_allowed_categories)
    graph_builder.add_node("generate", generate)
    graph_builder.add_node("check_hallucination", check_hallucination)
    graph_builder.add_node("rewrite", rewrite)
    graph_builder.add_node("basic_generate", basic_generate)
    graph_builder.add_node("format_answer", format_answer_message)
    graph_builder.add_node("summarize_messages", summarize_messages)
    graph_builder.add_node("truncate_messages", truncate_messages)

    graph_builder.add_edge(START, "extract_query")
    graph_builder.add_edge("extract_query", "classify_intent")
    graph_builder.add_edge("classify_intent", "assess_feasibility")
    graph_builder.add_conditional_edges(
        "assess_feasibility",
        router,
        {
            "rag_answer": "case_classification",
            "general_answer": "basic_generate",
        },
    )
    graph_builder.add_edge("case_classification", "plan_structured_search")
    graph_builder.add_edge("plan_structured_search", "schedule_tool")
    graph_builder.add_conditional_edges(
        "schedule_tool",
        seller_tool_router,
        {
            "tools": "tool_executor",
            "resume": "generate",
        },
    )
    graph_builder.add_edge("tool_executor", "consume_tool")
    graph_builder.add_conditional_edges(
        "consume_tool",
        seller_tool_followup_router,
        {
            "more_tools": "schedule_tool",
            "continue": "analyze_zone_performance",
        },
    )
    graph_builder.add_edge("analyze_zone_performance", "check_allowed_categories")
    graph_builder.add_edge("check_allowed_categories", "generate")
    # NOTE: 판매자 챗봇은 hallucination 체크 없이 바로 format_answer로 연결 (무한 루프 방지)
    graph_builder.add_edge("generate", "format_answer")
    graph_builder.add_edge("basic_generate", "format_answer")
    graph_builder.add_edge("format_answer", "summarize_messages")
    graph_builder.add_edge("summarize_messages", "truncate_messages")
    graph_builder.add_edge("truncate_messages", END)

    return graph_builder.compile(checkpointer=checkpointer)


def build_seller_graph_async(checkpointer=None):
    """
    Async-first seller graph 빌더. LangGraph `astream`/`astream_events` 시나리오에 최적화.
    
    v2: full_classify_seller_async 통합 노드 사용
    - classify_intent + assess_feasibility + case_classification + plan_structured_search → 단일 노드
    - LLM 호출 4회 → 1회로 감소
    - 예상 latency: ~15초 → ~5초
    """

    graph_builder = StateGraph(AgentState)
    tool_node = ToolNode([seller_retrieve_async, web_search_async])

    # 노드 정의 (통합 노드 사용)
    graph_builder.add_node("extract_query", extract_user_query)
    graph_builder.add_node("full_classify", full_classify_seller_async)  # 통합 노드
    graph_builder.add_node("schedule_tool", schedule_seller_tool_async)
    graph_builder.add_node("consume_tool", consume_seller_tool_result)
    graph_builder.add_node("tool_executor", tool_node)
    graph_builder.add_node("analyze_zone_performance", analyze_zone_performance)
    graph_builder.add_node("check_allowed_categories", check_allowed_categories_async)
    graph_builder.add_node("generate", generate_async)
    graph_builder.add_node("basic_generate", basic_generate_async)
    graph_builder.add_node("format_answer", format_answer_message)
    graph_builder.add_node("summarize_messages", summarize_messages_async)
    graph_builder.add_node("truncate_messages", truncate_messages)

    # 엣지 정의 (단순화된 흐름)
    graph_builder.add_edge(START, "extract_query")
    graph_builder.add_edge("extract_query", "full_classify")  # 통합 노드로 직행
    graph_builder.add_conditional_edges(
        "full_classify",
        full_seller_router,  # 통합 라우터
        {
            "rag_answer": "schedule_tool",  # case_classification, plan 생략 (통합됨)
            "general_answer": "basic_generate",
        },
    )
    graph_builder.add_conditional_edges(
        "schedule_tool",
        seller_tool_router,
        {
            "tools": "tool_executor",
            "resume": "generate",
        },
    )
    graph_builder.add_edge("tool_executor", "consume_tool")
    graph_builder.add_conditional_edges(
        "consume_tool",
        seller_tool_followup_router,
        {
            "more_tools": "schedule_tool",
            "continue": "analyze_zone_performance",
        },
    )
    graph_builder.add_edge("analyze_zone_performance", "check_allowed_categories")
    graph_builder.add_edge("check_allowed_categories", "generate")
    graph_builder.add_edge("generate", "format_answer")
    graph_builder.add_edge("basic_generate", "format_answer")
    graph_builder.add_edge("format_answer", "summarize_messages")
    graph_builder.add_edge("summarize_messages", "truncate_messages")
    graph_builder.add_edge("truncate_messages", END)

    return graph_builder.compile(checkpointer=checkpointer)


__all__ = ["build_seller_graph", "build_seller_graph_async", "AgentState"]

