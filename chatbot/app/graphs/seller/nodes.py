from __future__ import annotations

"""
Seller-facing LangGraph nodes and helpers.

`seller_graph.py`에서 정의하던 상태/노드/프롬프트를 모듈화했고,
실제 로직은 기존 설계와 동일하게 유지된다.
"""

import json
import uuid
from typing import Any, Dict, List, Literal, Sequence, cast

from langchain_core.documents import Document
from langchain_core.messages import AIMessage, BaseMessage, RemoveMessage, ToolMessage
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate, PromptTemplate
from langgraph.graph import MessagesState
from pydantic import BaseModel, Field
from typing_extensions import NotRequired

from langchain_openai import ChatOpenAI

from app.chains.seller import build_seller_rag_chain
from app.config import get_settings
from app.graphs.shared import format_messages, latest_user_message


settings = get_settings()

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
hallucination_llm = _llm(temperature=0)
rewrite_llm = _llm(temperature=0)
basic_llm = _llm(temperature=0.3)
summary_llm = _llm(temperature=0)
rag_chain = build_seller_rag_chain(settings)


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
    pending_tool_call_id: NotRequired[str]
    pending_tool_name: NotRequired[str]
    pending_tool_query: NotRequired[str]
    needs_web_search: NotRequired[bool]
    web_search_attempted: NotRequired[bool]
    last_tool_payload: NotRequired[Dict[str, Any]]


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


def _build_tool_call_message(
    tool_name: str,
    query: str,
) -> tuple[str, AIMessage]:
    call_id = f"{tool_name}-{uuid.uuid4().hex}"
    # LangChain v1 ToolCall 스키마(name/args)를 따른다.
    # 참고: https://docs.langchain.com/oss/python/langchain/overview
    tool_call = {
        "id": call_id,
        "type": "tool_call",
        "name": tool_name,
        "args": {"query": query},
    }
    message = AIMessage(
        content=f"{tool_name} 호출 준비",
        tool_calls=[tool_call],
    )
    return call_id, message


def _schedule_tool(
    state: AgentState,
    *,
    tool_name: str,
    query: str,
) -> AgentState:
    call_id, ai_message = _build_tool_call_message(tool_name, query)
    next_state: AgentState = {
        **state,
        "messages": [ai_message],
        "pending_tool_call_id": call_id,
        "pending_tool_name": tool_name,
        "pending_tool_query": query,
    }
    if tool_name.startswith("web_search"):
        next_state["web_search_attempted"] = True
        next_state.pop("needs_web_search", None)
    else:
        next_state.pop("web_search_attempted", None)
    return next_state


def _resolve_tool_query(state: AgentState, *, web_search: bool) -> str:
    if web_search:
        return _get_query_for_reasoning(state)
    return _get_query_for_search(state)


def schedule_seller_tool(state: AgentState) -> AgentState:
    tool_name = "web_search" if state.get("needs_web_search") else "seller_retrieve"
    query = _resolve_tool_query(state, web_search=tool_name.startswith("web_search"))
    return _schedule_tool(state, tool_name=tool_name, query=query)


def schedule_seller_tool_async(state: AgentState) -> AgentState:
    tool_name = "web_search_async" if state.get("needs_web_search") else "seller_retrieve_async"
    query = _resolve_tool_query(state, web_search=tool_name.startswith("web_search"))
    return _schedule_tool(state, tool_name=tool_name, query=query)


def seller_tool_router(state: AgentState) -> Literal["tools", "resume"]:
    return "tools" if state.get("pending_tool_name") else "resume"


def _find_tool_message(
    messages: Sequence[BaseMessage],
    call_id: str | None,
) -> ToolMessage | None:
    for message in reversed(messages):
        if isinstance(message, ToolMessage):
            if call_id is None or message.tool_call_id == call_id:
                return message
    return None


def _parse_tool_payload(content: Any) -> Dict[str, Any]:
    if isinstance(content, dict):
        return content
    if isinstance(content, str):
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            return {"type": "unknown", "raw": content}
    return {"type": "unknown", "raw": content}


def _documents_from_payload(payload: Dict[str, Any]) -> List[Document]:
    documents: List[Document] = []
    for row in payload.get("documents") or []:
        if not isinstance(row, dict):
            continue
        page_content = row.get("page_content") or ""
        metadata = row.get("metadata") or {}
        documents.append(
            Document(
                page_content=str(page_content),
                metadata=dict(metadata),
            )
        )
    return documents


def consume_seller_tool_result(state: AgentState) -> AgentState:
    call_id = state.get("pending_tool_call_id")
    messages = state.get("messages", [])
    tool_message = _find_tool_message(messages, call_id)
    if tool_message is None:
        return state

    payload = _parse_tool_payload(tool_message.content)
    documents = _documents_from_payload(payload)
    next_state: AgentState = {
        **state,
        "context": documents,
        "last_tool_payload": payload,
    }

    next_state.pop("pending_tool_call_id", None)
    next_state.pop("pending_tool_name", None)
    next_state.pop("pending_tool_query", None)

    should_retry_with_web = (
        payload.get("type") == "seller_retrieve"
        and not documents
        and bool(settings.websearch_enabled)
        and not state.get("web_search_attempted")
    )
    if should_retry_with_web:
        next_state["needs_web_search"] = True
    else:
        next_state.pop("needs_web_search", None)

    if payload.get("type") == "web_search":
        next_state["web_search_attempted"] = True

    return next_state


def seller_tool_followup_router(state: AgentState) -> Literal["more_tools", "continue"]:
    return "more_tools" if state.get("needs_web_search") else "continue"


# ---------------------------------------------------------------------------
# Router & prompts (seller tone preserved from bot4s)
# ---------------------------------------------------------------------------


class Route(BaseModel):  # type: ignore[misc]
    target: Literal["rag_answer", "general_answer"]


router_system_prompt = """
You are the routing assistant for '잇다잉(Itdaing)', 광주광역시 플리마켓/팝업 셀러 전용 존 추천 서비스.
Decide whether the user's question should be answered via rag_answer (tool/DB 기반) or general_answer.

The vector store contains detailed information about zone recommendations for sellers,
including descriptions, locations, categories, visitor patterns, and atmosphere tags.

Choose ``rag_answer`` unless the question is unrelated to picking a zone in Gwangju,
requests other cities, or clearly aims to overload the system.
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
당신은 광주광역시 존 추천 전문가 '잇다잉(Itdaing)'의 질문분류기이자 문장 작성기입니다.

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
당신은 광주광역시 플리마켓 및 팝업스토어 셀러를 돕는 '잇다잉(Itdaing)'의 간단 응답용 챗봇입니다.

질문이 광주 외 지역이거나, 과도한 요청이면 "지원되지 않는 서비스입니다." 한 문장만 답하세요.
간단한 인사나 소개는 친근하고 유머러스하게, 그러나 짧게 답하세요.
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


# ---------------------------------------------------------------------------
# Graph nodes
# ---------------------------------------------------------------------------


def extract_user_query(state: AgentState) -> AgentState:
    latest = latest_user_message(state.get("messages", []))
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
        f"{prompt}\n\nchat_history:\n{format_messages(messages)}\n\nsummary:{summary}"
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
# Async graph nodes (seller flow)
# ---------------------------------------------------------------------------


async def router_async(state: AgentState) -> Literal["rag_answer", "general_answer"]:
    summary = state.get("summary", "").strip() or "요약 없음"
    query = state.get("query", "")
    query_text = query if isinstance(query, str) else str(query)
    result = cast(
        Route,
        await router_chain.ainvoke(
            {
                "summary": summary,
                "query": query_text,
            }
        ),
    )
    return result.target


async def case_classification_async(state: AgentState) -> AgentState:
    summary = state.get("summary", "").strip() or "요약 없음"
    query = _get_query_for_reasoning(state)
    result = cast(
        CaseClassification,
        await case_classification_chain.ainvoke({"summary": summary, "query": query}),
    )
    return {**state, "case": result.case, "paraphrased_query": result.rewritten_query}


async def generate_async(state: AgentState) -> AgentState:
    context_docs = state.get("context", []) or []
    summary = state.get("summary", "").strip() or "요약 없음"
    question = _get_query_for_search(state)
    response = await rag_chain.ainvoke(
        {
            "summary": summary,
            "question": question,
            "context": _format_context(context_docs),
        }
    )
    answer_text = response.content if isinstance(response.content, str) else str(response.content)
    return {**state, "answer": answer_text}


async def check_hallucination_async(state: AgentState) -> AgentState:
    docs = state.get("context", []) or []
    formatted_docs = _format_context(docs)
    answer_text = _get_answer_text(state)
    result = cast(
        Hallucination,
        await hallucination_chain.ainvoke(
            {"student_answer": answer_text, "documents": formatted_docs}
        ),
    )
    return {**state, "hallucination_label": result.label, "hallucination_reason": result.reason}


async def rewrite_async(state: AgentState) -> AgentState:
    summary = state.get("summary", "").strip() or "요약 없음"
    query = _get_query_for_reasoning(state)
    new_query = await rewrite_chain.ainvoke(
        {
            "summary": summary,
            "query": query,
            "hallucination_label": state.get("hallucination_label", "not hallucinated"),
            "hallucination_reason": state.get("hallucination_reason", ""),
        }
    )
    return {**state, "query": new_query, "paraphrased_query": new_query}


async def basic_generate_async(state: AgentState) -> AgentState:
    summary = state.get("summary", "").strip() or "요약 없음"
    query = state.get("query", "")
    answer = await basic_chain.ainvoke({"summary": summary, "query": query})
    return {**state, "answer": answer}


async def summarize_messages_async(state: AgentState) -> AgentState:
    messages = state.get("messages", [])
    if not messages:
        return state
    summary = state.get("summary", "")
    prompt = (
        "summarize this chat history below"
        if not summary
        else "summarize this chat history while incorporating the previous summary"
    )
    summary_text = await summary_llm.ainvoke(
        f"{prompt}\n\nchat_history:\n{format_messages(messages)}\n\nsummary:{summary}"
    )
    new_summary = summary_text.content if isinstance(summary_text.content, str) else str(summary_text.content)
    return {**state, "summary": new_summary}


__all__ = [
    "AgentState",
    "extract_user_query",
    "router",
    "router_async",
    "case_classification",
    "case_classification_async",
    "schedule_seller_tool",
    "schedule_seller_tool_async",
    "seller_tool_router",
    "seller_tool_followup_router",
    "consume_seller_tool_result",
    "generate",
    "generate_async",
        "check_hallucination",
    "check_hallucination_async",
    "rewrite",
    "rewrite_async",
    "basic_generate",
    "basic_generate_async",
    "format_answer_message",
    "summarize_messages",
    "summarize_messages_async",
    "truncate_messages",
]


