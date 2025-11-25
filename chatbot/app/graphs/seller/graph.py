from __future__ import annotations

from langgraph.graph import END, START, StateGraph

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
    retrieve,
    retrieve_async,
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

    graph_builder.add_node("extract_query", extract_user_query)
    graph_builder.add_node("case_classification", case_classification)
    graph_builder.add_node("retrieve", retrieve)
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
    graph_builder.add_edge("case_classification", "retrieve")
    graph_builder.add_edge("retrieve", "generate")
    graph_builder.add_edge("generate", "check_hallucination")
    graph_builder.add_conditional_edges(
        "check_hallucination",
        hallucination_router,
        {
            "not hallucinated": "format_answer",
            "hallucinated": "rewrite",
        },
    )
    graph_builder.add_edge("rewrite", "retrieve")
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

    graph_builder.add_node("extract_query", extract_user_query)
    graph_builder.add_node("case_classification", case_classification_async)
    graph_builder.add_node("retrieve", retrieve_async)
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
    graph_builder.add_edge("case_classification", "retrieve")
    graph_builder.add_edge("retrieve", "generate")
    graph_builder.add_edge("generate", "check_hallucination")
    graph_builder.add_conditional_edges(
        "check_hallucination",
        hallucination_router,
        {
            "not hallucinated": "format_answer",
            "hallucinated": "rewrite",
        },
    )
    graph_builder.add_edge("rewrite", "retrieve")
    graph_builder.add_edge("basic_generate", "format_answer")
    graph_builder.add_edge("format_answer", "summarize_messages")
    graph_builder.add_edge("summarize_messages", "truncate_messages")
    graph_builder.add_edge("truncate_messages", END)

    return graph_builder.compile(checkpointer=checkpointer)


__all__ = ["build_seller_graph", "build_seller_graph_async", "AgentState"]

