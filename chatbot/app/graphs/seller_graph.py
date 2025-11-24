from __future__ import annotations

"""
Seller-facing multi-turn LangGraph graph.

This is a refactoring of `bot4s.py` into the new package layout.
Zone RAG, routing, hallucination checking, rewrite, summarization, and
message truncation logic are preserved, while vector store/settings are
provided by `app.db.postgres` / `app.config`.
"""

from pathlib import Path
from typing import Any, Dict, List, Literal, Sequence, cast

from langchain_core.documents import Document
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, RemoveMessage
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate, PromptTemplate
from langgraph.graph import END, START, MessagesState, StateGraph
from pydantic import BaseModel, Field
from typing_extensions import NotRequired

from langchain_openai import ChatOpenAI

from app.config import get_settings
from app.db.postgres import get_zones_vectorstore


settings = get_settings()

# ---------------------------------------------------------------------------
# Shared resources (zone vector store + LLMs)
# ---------------------------------------------------------------------------

_zone_vectorstore = get_zones_vectorstore(settings)
_zone_retriever = _zone_vectorstore.as_retriever(
    search_type="similarity",
    search_kwargs={"k": settings.zone_rag_top_k},
)


def _llm(temperature: float = 0.0) -> ChatOpenAI:
    def _api_key_provider() -> str:
        return settings.openai_api_key

    return ChatOpenAI(
        model=settings.openai_model,
        temperature=temperature,
        api_key=_api_key_provider,
    )


router_llm = _llm(temperature=0)
case_classification_llm = _llm(temperature=0)
generate_llm = _llm(temperature=0)
hallucination_llm = _llm(temperature=0)
rewrite_llm = _llm(temperature=0)
basic_llm = _llm(temperature=0.3)
summary_llm = _llm(temperature=0)


# ---------------------------------------------------------------------------
# Conversation-aware state and helpers (ported from bot4s.AgentState)
# ---------------------------------------------------------------------------


class AgentState(MessagesState):
    """Seller assistant state augmented with retrieval metadata."""

    summary: NotRequired[str]
    query: NotRequired[str]
    case: NotRequired[str]
    paraphrased_query: NotRequired[str]
    context: NotRequired[List[Document]]
    answer: NotRequired[str]
    hallucination_label: NotRequired[str]
    hallucination_reason: NotRequired[str]


def _latest_user_message(messages: Sequence[BaseMessage]) -> HumanMessage:
    for message in reversed(messages):
        if isinstance(message, HumanMessage):
            return message
    raise ValueError("대화 내에 사용자 메시지가 존재하지 않습니다.")


def _format_messages(messages: Sequence[BaseMessage]) -> str:
    lines: List[str] = []
    for msg in messages:
        role = msg.type.upper()
        content = msg.content if isinstance(msg.content, str) else str(msg.content)
        lines.append(f"[{role}] {content}")
    return "\n".join(lines)


def _format_context(docs: Sequence[Document]) -> str:
    if not docs:
        return "(no documents)"
    parts: List[str] = []
    for idx, doc in enumerate(docs, start=1):
        title = doc.metadata.get("zone_name", f"Zone {idx}")
        parts.append(f"## {title}\n{doc.page_content}")
    return "\n\n".join(parts)


def _get_query_for_reasoning(state: AgentState) -> str:
    query = state.get("query")
    if isinstance(query, str) and query.strip():
        return query.strip()
    raise ValueError("질문을 찾을 수 없습니다.")


def _get_query_for_search(state: AgentState) -> str:
    candidate = state.get("paraphrased_query")
    if isinstance(candidate, str) and candidate.strip():
        return candidate.strip()
    return _get_query_for_reasoning(state)


def _get_answer_text(state: AgentState) -> str:
    answer = state.get("answer")
    if isinstance(answer, str) and answer.strip():
        return answer.strip()
    raise ValueError("응답이 비어있습니다.")


# ---------------------------------------------------------------------------
# Router & prompts (seller tone preserved from bot4s)
# ---------------------------------------------------------------------------


class Route(BaseModel):  # type: ignore[misc]
    target: Literal["rag_answer", "general_answer"]


router_system_prompt = """
You are an expert router that decides whether a user's question should be
answered using the rag_answer or the general_answer path.

The vector store contains detailed information about zone recommendations for
sellers, including descriptions, locations, categories, visitor patterns, and
atmosphere tags.

Choose ``rag_answer`` unless the question is unrelated to picking a zone in
Gwangju, requests other cities, or clearly aims to overload the system.
""".strip()

router_prompt = ChatPromptTemplate.from_messages(
    [
        ("system", router_system_prompt),
        ("user", "이전 대화 요약:\n{summary}\n\n사용자 질문:\n{query}"),
    ]
)

structured_router_llm = router_llm.with_structured_output(Route)
router_chain = router_prompt | structured_router_llm


class CaseClassification(BaseModel):  # type: ignore[misc]
    case: Literal[
        "zone_info",
        "category_fit",
        "seller_recommend",
        "traffic_peak",
        "demographics",
    ]
    rewritten_query: str = Field(description="벡터 검색에 적합한 한국어 문장")


case_classification_system_prompt = """
당신은 사용자의 질문을 분석해 존 추천에 필요한 정보를 뽑아주는 질문분류기이자
문장 작성기입니다.

질문을 보고:
1) 아래 라벨 중 하나를 case로 출력하고
2) 벡터 검색에 적합하도록 한국어로 명확히 다시 써서 rewritten_query로 반환하세요.
""".strip()

case_classification_prompt = ChatPromptTemplate.from_messages(
    [
        ("system", case_classification_system_prompt),
        ("user", "이전 대화 요약:\n{summary}\n\n사용자 질문:\n{query}"),
    ]
)

case_classification_chain = case_classification_prompt | case_classification_llm.with_structured_output(
    CaseClassification
)


generate_prompt = PromptTemplate.from_template(
    """
당신은 광주 플리마켓 존 추천 도우미입니다.
검색된 문서를 바탕으로 사용자의 질문에 친근하고 위트있게 최대 3문장으로 답하세요.
답을 모를 때는 솔직히 모른다고 말하세요.

이전 대화 요약:
{summary}

질문:
{question}

문서:
{context}
""".strip()
)


hallucination_prompt = PromptTemplate.from_template(
    """
You are a teacher checking if the assistant's answer is grounded in the zone documents.
Respond with a label ("hallucinated" or "not hallucinated") and a short reason.

Documents:
{documents}

Student answer:
{student_answer}
""".strip()
)


rewrite_dictionary = """
열만한 데, 차릴만한 곳 -> 플리마켓을 열만한 존
곳, 존, 장소, 지역 -> 플리마켓을 열만한 존
""".strip()

rewrite_prompt = PromptTemplate.from_template(
    f"""
당신은 광주광역시 플리마켓 존 추천 챗봇의 쿼리 재작성 도우미입니다.
사전과 질문과 할루시네이션 정보를 참고해 검색용 한국어 문장을 한 줄로 출력하세요.

사전:
{rewrite_dictionary}

이전 대화 요약:
{{summary}}

질문:
{{query}}

할루시네이션 라벨: {{hallucination_label}}
할루시네이션 이유: {{hallucination_reason}}

출력 형식:
- 마켓존 추천을 명확히 드러내는 한 문장만 출력합니다.
- 말머리, 따옴표, 리스트, 번역은 금지입니다.
""".strip()
)


basic_system_prompt = """
당신은 판매자에게 플리마켓 존을 안내해주는 간단한 챗봇입니다.

질문이 광주 외 지역이거나, 과도한 요청이면 "지원되지 않는 서비스입니다." 한 문장만 답하세요.
간단한 인사나 소개는 친근하지만 짧게 답하세요.
""".strip()

basic_prompt = ChatPromptTemplate.from_messages(
    [
        ("system", basic_system_prompt),
        ("user", "이전 대화 요약:\n{summary}\n\n사용자 질문:\n{query}"),
    ]
)


class Hallucination(BaseModel):  # type: ignore[misc]
    label: Literal["hallucinated", "not hallucinated"]
    reason: str


structured_hallucination_llm = hallucination_llm.with_structured_output(Hallucination)
hallucination_chain = hallucination_prompt | structured_hallucination_llm
rewrite_chain = rewrite_prompt | rewrite_llm | StrOutputParser()
basic_chain = basic_prompt | basic_llm | StrOutputParser()
rag_chain = generate_prompt | generate_llm


# ---------------------------------------------------------------------------
# Graph nodes
# ---------------------------------------------------------------------------


def extract_user_query(state: AgentState) -> AgentState:
    latest = _latest_user_message(state.get("messages", []))
    latest_text = latest.content if isinstance(latest.content, str) else str(latest.content)
    return {**state, "query": latest_text.strip()}


def router(state: AgentState) -> Literal["rag_answer", "general_answer"]:
    summary = state.get("summary", "").strip() or "요약 없음"
    query = state.get("query", "")
    result = cast(
        Route,
        router_chain.invoke(
            {
                "summary": summary,
                "query": query if isinstance(query, str) else str(query),
            }
        ),
    )
    return result.target


def case_classification(state: AgentState) -> AgentState:
    summary = state.get("summary", "").strip() or "요약 없음"
    query = _get_query_for_reasoning(state)
    result = cast(
        CaseClassification,
        case_classification_chain.invoke({"summary": summary, "query": query}),
    )
    return {**state, "case": result.case, "paraphrased_query": result.rewritten_query}


def retrieve(state: AgentState) -> AgentState:
    query_for_search = _get_query_for_search(state)
    docs = _zone_retriever.invoke(query_for_search)
    return {**state, "context": docs}


def generate(state: AgentState) -> AgentState:
    context_docs = state.get("context", []) or []
    summary = state.get("summary", "").strip() or "요약 없음"
    question = _get_query_for_search(state)
    response = rag_chain.invoke(
        {
            "summary": summary,
            "question": question,
            "context": _format_context(context_docs),
        }
    )
    answer_text = response.content if isinstance(response.content, str) else str(response.content)
    return {**state, "answer": answer_text}


def check_hallucination(state: AgentState) -> AgentState:
    docs = state.get("context", []) or []
    formatted_docs = _format_context(docs)
    answer_text = _get_answer_text(state)
    result = cast(
        Hallucination,
        hallucination_chain.invoke({"student_answer": answer_text, "documents": formatted_docs}),
    )
    return {**state, "hallucination_label": result.label, "hallucination_reason": result.reason}


def hallucination_router(state: AgentState) -> Literal["hallucinated", "not hallucinated"]:
    return state.get("hallucination_label", "not hallucinated")  # type: ignore[return-value]


def rewrite(state: AgentState) -> AgentState:
    summary = state.get("summary", "").strip() or "요약 없음"
    query = _get_query_for_reasoning(state)
    new_query = rewrite_chain.invoke(
        {
            "summary": summary,
            "query": query,
            "hallucination_label": state.get("hallucination_label", "not hallucinated"),
            "hallucination_reason": state.get("hallucination_reason", ""),
        }
    )
    return {**state, "query": new_query, "paraphrased_query": new_query}


def basic_generate(state: AgentState) -> AgentState:
    summary = state.get("summary", "").strip() or "요약 없음"
    query = state.get("query", "")
    answer = basic_chain.invoke({"summary": summary, "query": query})
    return {**state, "answer": answer}


def format_answer_message(state: AgentState) -> AgentState:
    answer = state.get("answer")
    if not answer:
        return state
    answer_text = answer if isinstance(answer, str) else str(answer)
    return {**state, "messages": [AIMessage(content=answer_text)]}


def summarize_messages(state: AgentState) -> AgentState:
    messages = state.get("messages", [])
    if not messages:
        return state
    summary = state.get("summary", "")
    prompt = (
        "summarize this chat history below"
        if not summary
        else "summarize this chat history while incorporating the previous summary"
    )
    summary_text = summary_llm.invoke(
        f"{prompt}\n\nchat_history:\n{_format_messages(messages)}\n\nsummary:{summary}"
    )
    new_summary = summary_text.content if isinstance(summary_text.content, str) else str(summary_text.content)
    return {**state, "summary": new_summary}


def truncate_messages(state: AgentState) -> dict:
    messages = state.get("messages", [])
    if len(messages) <= settings.zone_max_message_history:
        return {}
    delete_targets: List[RemoveMessage] = []
    for message in messages[:-settings.zone_max_message_history]:
        message_id = getattr(message, "id", None)
        if message_id:
            delete_targets.append(RemoveMessage(id=message_id))
    if not delete_targets:
        return {}
    return {"messages": delete_targets}


# ---------------------------------------------------------------------------
# Graph assembly
# ---------------------------------------------------------------------------


def build_seller_graph(checkpointer=None):
    """
    Build a LangGraph StateGraph for the seller chatbot, preserving the
    original bot4s flow while allowing an external checkpointer
    (e.g. AsyncPostgresSaver) to be injected.
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


__all__ = ["build_seller_graph", "AgentState"]


