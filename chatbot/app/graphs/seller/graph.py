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
    basic_generate,
    basic_generate_async,
    case_classification,
    case_classification_async,
    check_hallucination,
    check_hallucination_async,
    extract_user_query,
    format_answer_message,
    generate,
    generate_async,
    hallucination_router,
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
)


def build_seller_graph(checkpointer=None):
    """
    Build a LangGraph StateGraph for the seller chatbot, preserving the original
    bot4s flow while allowing an external checkpointer (예: AsyncPostgresSaver)를 주입.
    """

    graph_builder = StateGraph(AgentState)
    tool_node = ToolNode([seller_retrieve, web_search])

    graph_builder.add_node("extract_query", extract_user_query)
    graph_builder.add_node("case_classification", case_classification)
    graph_builder.add_node("schedule_tool", schedule_seller_tool)
    graph_builder.add_node("consume_tool", consume_seller_tool_result)
    graph_builder.add_node("tool_executor", tool_node)
    graph_builder.add_node("generate", generate)
    graph_builder.add_node("check_hallucination", check_hallucination)
    graph_builder.add_node("rewrite", rewrite)
    graph_builder.add_node("basic_generate", basic_generate)
    graph_builder.add_node("format_answer", format_answer_message)
    graph_builder.add_node("summarize_messages", summarize_messages)
    graph_builder.add_node("truncate_messages", truncate_messages)

    graph_builder.add_edge(START, "extract_query")
    graph_builder.add_conditional_edges(
        "extract_query",
        router,
        {
            "rag_answer": "case_classification",
            "general_answer": "basic_generate",
        },
    )
    graph_builder.add_edge("case_classification", "schedule_tool")
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


def build_seller_graph_async(checkpointer=None):
    """
    Async-first seller graph 빌더. LangGraph `astream`/`astream_events` 시나리오에 최적화.
    """

    graph_builder = StateGraph(AgentState)
    tool_node = ToolNode([seller_retrieve_async, web_search_async])

    graph_builder.add_node("extract_query", extract_user_query)
    graph_builder.add_node("case_classification", case_classification_async)
    graph_builder.add_node("schedule_tool", schedule_seller_tool_async)
    graph_builder.add_node("consume_tool", consume_seller_tool_result)
    graph_builder.add_node("tool_executor", tool_node)
    graph_builder.add_node("generate", generate_async)
    graph_builder.add_node("check_hallucination", check_hallucination_async)
    graph_builder.add_node("rewrite", rewrite_async)
    graph_builder.add_node("basic_generate", basic_generate_async)
    graph_builder.add_node("format_answer", format_answer_message)
    graph_builder.add_node("summarize_messages", summarize_messages_async)
    graph_builder.add_node("truncate_messages", truncate_messages)

    graph_builder.add_edge(START, "extract_query")
    graph_builder.add_conditional_edges(
        "extract_query",
        router_async,
        {
            "rag_answer": "case_classification",
            "general_answer": "basic_generate",
        },
    )
    graph_builder.add_edge("case_classification", "schedule_tool")
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


__all__ = ["build_seller_graph", "build_seller_graph_async", "AgentState"]

