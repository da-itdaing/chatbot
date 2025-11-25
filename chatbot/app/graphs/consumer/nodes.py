from __future__ import annotations

"""
Consumer-facing LangGraph nodes and helpers.

이 모듈은 기존 `consumer_graph.py`에 있던 상태/노드/프롬프트 정의를 보존한 채
구조만 모듈화한 버전이다.
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

from app.chains.consumer import build_consumer_rag_chain
from app.config import get_settings
from app.graphs.shared import format_messages, latest_user_message


settings = get_settings()

def _llm(temperature: float = 0.0) -> ChatOpenAI:
    # Pass api_key via callable so we don't depend on process env.
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
rag_chain = build_consumer_rag_chain(settings)


# ---------------------------------------------------------------------------
# State definition (ported from bot4c_v2_multiturn.AgentState)
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Helper utilities
# ---------------------------------------------------------------------------


def _format_context(docs: List[Document]) -> str:
    if not docs:
        return "(no documents)"
    parts = []
    for idx, doc in enumerate(docs, start=1):
        title = doc.metadata.get("market_name", f"Document {idx}")
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
        return answer
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
    ai_message = AIMessage(
        content=f"{tool_name} 호출 준비",
        tool_calls=[tool_call],
    )
    return call_id, ai_message


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


def schedule_consumer_tool(state: AgentState) -> AgentState:
    tool_name = "web_search" if state.get("needs_web_search") else "consumer_retrieve"
    query = _resolve_tool_query(state, web_search=tool_name.startswith("web_search"))
    return _schedule_tool(state, tool_name=tool_name, query=query)


def schedule_consumer_tool_async(state: AgentState) -> AgentState:
    tool_name = "web_search_async" if state.get("needs_web_search") else "consumer_retrieve_async"
    query = _resolve_tool_query(state, web_search=tool_name.startswith("web_search"))
    return _schedule_tool(state, tool_name=tool_name, query=query)


def consumer_tool_router(state: AgentState) -> Literal["tools", "resume"]:
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
    docs_data = payload.get("documents") or []
    documents: List[Document] = []
    for row in docs_data:
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


def consume_consumer_tool_result(state: AgentState) -> AgentState:
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
        payload.get("type") == "consumer_retrieve"
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


def consumer_tool_followup_router(state: AgentState) -> Literal["more_tools", "continue"]:
    return "more_tools" if state.get("needs_web_search") else "continue"


# ---------------------------------------------------------------------------
# Router & prompts (identical to bot4c_v2_multiturn)
# ---------------------------------------------------------------------------


class Route(BaseModel):
    target: Literal["rag_answer", "general_answer"] = Field(
        description="Routing target for the user's query"
    )


router_system_prompt = """
You are the routing assistant for '잇다잉(Itdaing)', 광주광역시 플리마켓/팝업 추천 서비스.
Decide whether the user's question should go through the rag_answer path (DB/tool 기반)
or the general_answer fallback.

The vector store contains detailed information about flea markets for recommendations,
including descriptions, locations, distances, categories, attributes, and metadata.
Users typically describe their situation or desired experience (예: 여자친구랑 가기 좋은 곳, 배고픈데 뭐 먹지?).

Choose ``rag_answer`` unless the question is completely unrelated to Itdaing's market
recommendations, requests markets outside Gwangju, or clearly attempts abusive load
(예: 대량 계산, 전혀 무관한 작업). Return only one of the two labels.
""".strip()

router_prompt = ChatPromptTemplate.from_messages(
    [
        ("system", router_system_prompt),
        (
            "user",
            "이전 대화 요약:\n{summary}\n\n사용자 질문:\n{query}",
        ),
    ]
)


structured_router_llm = router_llm.with_structured_output(Route)
router_chain = router_prompt | structured_router_llm


class CaseClassification(BaseModel):
    case: Literal["region_keyword", "date", "market_info", "amenity", "rating"]
    rewritten_query: str = Field(
        description="Rewritten Korean query optimized for retrieval"
    )


case_classification_system_prompt = """
당신은 광주광역시 플리마켓/팝업 추천 전문가 '잇다잉(Itdaing)'의 질문분류기이자 문장작성기입니다.

사용자 질문을 보고:
1) 아래 질문 분류 기준 중 하나를 선택해서 case로 출력하고,
2) 벡터스토어 검색/추천에 쓰기 좋은 형태로 문장을 다시 써서 rewritten_query에 넣어주세요.

질문 분류 기준:
- region_keyword: 지역 + 카테고리 등 키워드 기반 질문
- date: 날짜, 운영시간 관련 질문
- market_info: 마켓의 성격 표현, 묘사 설명 등이 동반된 질문
- amenity: 편의시설/반려동물/주차 등 질문
- rating: 리뷰/평점 기반 질문
""".strip()

case_classification_prompt = ChatPromptTemplate.from_messages(
    [
        ("system", case_classification_system_prompt),
        (
            "user",
            "이전 대화 요약:\n{summary}\n\n사용자 질문:\n{query}",
        ),
    ]
)


case_classification_structured = case_classification_llm.with_structured_output(
    CaseClassification
)
case_classification_chain = case_classification_prompt | case_classification_structured


hallucination_prompt = PromptTemplate.from_template(
    """
You are a teacher tasked with evaluating whether a student's answer is based on
documents or not.

Given documents (market information) and the student's answer, respond with a
label ("hallucinated" or "not hallucinated") plus a short reason.

Documents:
{documents}

Student answer:
{student_answer}
""".strip()
)


rewrite_dictionary = """
갈만한 데 → 갈만한 플리마켓이나 팝업 마켓
데이트 코스 → 연인과 함께 가기 좋은 플리마켓이나 야외 팝업 마켓
놀거리 → 볼거리와 체험이 있는 플리마켓이나 팝업 마켓
구경할 곳 → 구경하기 좋은 플리마켓이나 팝업 마켓
먹을 데 → 먹거리가 많은 플리마켓이나 야시장 형태의 마켓
""".strip()

rewrite_prompt = PromptTemplate.from_template(
    f"""
당신은 광주 플리마켓/팝업 마켓 추천 챗봇을 위한 쿼리 재작성 도우미입니다.
'사전'과 '질문'과 '할루시네이션 이유'와 '할루시네이션 라벨'을 참고하여 쿼리를 재작성 해주세요.

목표:
1) 사용자의 한국어 질문을 벡터 검색에 적합한 한 문장으로 재작성한다.
2) 반드시 '플리마켓', '마켓', '팝업' 등의 단어를 포함하여, 마켓 추천 요청임이 드러나게 한다.
3) 사용자가 언급한 지역, 날짜/요일, 같이 가는 사람, 분위기 키워드 등은 그대로 유지한다.
4) 모호한 표현이나 축약형은 아래 사전을 참고해 더 명확하게 풀어쓴다.

사전:
{rewrite_dictionary}

이전 대화 요약:
{{summary}}

질문:
{{query}}

할루시네이션 라벨: {{hallucination_label}}
할루시네이션 이유: {{hallucination_reason}}

출력 형식:
- 벡터 검색용으로 완성된 한 문장의 한국어 쿼리만 출력한다.
- 추가 설명, 해석, 말머리, 따옴표, 리스트, 번역은 절대 출력하지 않는다.
""".strip()
)


rewrite_chain = rewrite_prompt | rewrite_llm | StrOutputParser()


basic_system_prompt = """
당신은 광주광역시 플리마켓 및 팝업스토어 추천 전문가 '잇다잉(Itdaing)'의 간단 응답용 챗봇입니다.

질문이 다음과 같은 경우:
1) 광주광역시 외의 지역에 대한 마켓 추천 요청
2) 비현실적인 계산/대량 나열 등 서버 공격 의도가 있는 요청

"지원되지 않는 서비스입니다." 형태로 한 문장만 응답하세요.
그 외 인사/잡담 등 간단한 대화에는 친근하고 유머러스하게, 그러나 간결하게 응답하세요.
""".strip()

basic_prompt = ChatPromptTemplate.from_messages(
    [
        ("system", basic_system_prompt),
        (
            "user",
            "이전 대화 요약:\n{summary}\n\n사용자 질문:\n{query}",
        ),
    ]
)


basic_chain = basic_prompt | basic_llm | StrOutputParser()


# ---------------------------------------------------------------------------
# Graph nodes (ported 1:1)
# ---------------------------------------------------------------------------


def extract_user_query(state: AgentState) -> AgentState:
    messages = state.get("messages", [])
    latest = latest_user_message(messages)
    latest_text = latest.content if isinstance(latest.content, str) else str(latest.content)
    return {
        **state,
        "query": latest_text.strip(),
    }


def router(state: AgentState) -> Literal["rag_answer", "general_answer"]:
    summary = state.get("summary", "").strip() or "요약 없음"
    query = state.get("query")
    query_text = query if isinstance(query, str) else ""
    route = cast(
        Route,
        router_chain.invoke(
            {
                "query": query_text,
                "summary": summary,
            }
        ),
    )
    return route.target


def case_classification(state: AgentState) -> AgentState:
    summary = state.get("summary", "").strip() or "요약 없음"
    query = _get_query_for_reasoning(state)
    result = cast(
        CaseClassification,
        case_classification_chain.invoke(
            {
                "query": query,
                "summary": summary,
            }
        ),
    )
    return {
        **state,
        "case": result.case,
        "paraphrased_query": result.rewritten_query,
    }


def generate(state: AgentState) -> AgentState:
    context_docs = state.get("context", []) or []
    summary = state.get("summary", "").strip()
    query = _get_query_for_search(state)
    response = rag_chain.invoke(
        {
            "question": query,
            "context": _format_context(context_docs),
            "summary": summary or "요약 없음",
        }
    )
    answer_text = response.content if isinstance(response.content, str) else str(response.content)
    return {
        **state,
        "answer": answer_text,
    }


class Hallucination(BaseModel):
    label: Literal["hallucinated", "not hallucinated"]
    reason: str


structured_hallucination_llm = hallucination_llm.with_structured_output(Hallucination)
hallucination_chain = hallucination_prompt | structured_hallucination_llm


def check_hallucination(state: AgentState) -> AgentState:
    docs = state.get("context", []) or []
    documents = _format_context(docs)
    answer = _get_answer_text(state)
    result = cast(
        Hallucination,
        hallucination_chain.invoke(
            {
                "student_answer": answer,
                "documents": documents,
            }
        ),
    )
    return {
        **state,
        "hallucination_label": result.label,
        "hallucination_reason": result.reason,
    }


def hallucination_router(state: AgentState) -> Literal["hallucinated", "not hallucinated"]:
    return "hallucinated" if state.get("hallucination_label") == "hallucinated" else "not hallucinated"


def rewrite(state: AgentState) -> AgentState:
    summary = state.get("summary", "").strip() or "요약 없음"
    original_query = _get_query_for_reasoning(state)
    new_query = rewrite_chain.invoke(
        {
            "query": original_query,
            "summary": summary,
            "hallucination_label": state.get("hallucination_label", "not hallucinated"),
            "hallucination_reason": state.get("hallucination_reason", ""),
        }
    )
    return {
        **state,
        "query": new_query,
        "paraphrased_query": new_query,
    }


def basic_generate(state: AgentState) -> AgentState:
    summary = state.get("summary", "").strip() or "요약 없음"
    query = state.get("query", "")
    query_text = query if isinstance(query, str) else str(query)
    answer = basic_chain.invoke(
        {
            "query": query_text,
            "summary": summary,
        }
    )
    return {
        **state,
        "answer": answer,
    }


def format_answer_message(state: AgentState) -> AgentState:
    answer = state.get("answer", "")
    if not answer:
        return state
    answer_text = answer if isinstance(answer, str) else str(answer)
    return {
        **state,
        "messages": [AIMessage(content=answer_text)],
    }


def summarize_messages(state: AgentState) -> AgentState:
    messages = state.get("messages", [])
    summary = state.get("summary", "")
    if not messages:
        return state

    prompt = (
        "summarize this chat history below"
        if not summary
        else "summarize this chat history while incorporating the previous summary"
    )
    summary_text = summary_llm.invoke(
        f"{prompt}\n\nchat_history:\n{format_messages(messages)}\n\nsummary:{summary}"
    )
    new_summary = summary_text.content if isinstance(summary_text.content, str) else str(summary_text.content)
    return {
        **state,
        "summary": new_summary,
    }


def truncate_messages(state: AgentState) -> dict:
    messages = state.get("messages", [])
    if len(messages) <= settings.max_message_history:
        return {}
    delete_targets: List[RemoveMessage] = []
    for message in messages[:-settings.max_message_history]:
        message_id = getattr(message, "id", None)
        if message_id:
            delete_targets.append(RemoveMessage(id=message_id))
    if not delete_targets:
        return {}
    return {"messages": delete_targets}


# ---------------------------------------------------------------------------
# Async graph nodes (LLM/RAG 호출을 await로 처리)
# ---------------------------------------------------------------------------


async def router_async(state: AgentState) -> Literal["rag_answer", "general_answer"]:
    summary = state.get("summary", "").strip() or "요약 없음"
    query = state.get("query", "")
    query_text = query if isinstance(query, str) else str(query)
    route = cast(
        Route,
        await router_chain.ainvoke({"query": query_text, "summary": summary}),
    )
    return route.target


async def case_classification_async(state: AgentState) -> AgentState:
    summary = state.get("summary", "").strip() or "요약 없음"
    query = _get_query_for_reasoning(state)
    result = cast(
        CaseClassification,
        await case_classification_chain.ainvoke(
            {
                "query": query,
                "summary": summary,
            }
        ),
    )
    return {
        **state,
        "case": result.case,
        "paraphrased_query": result.rewritten_query,
    }


async def generate_async(state: AgentState) -> AgentState:
    context_docs = state.get("context", []) or []
    summary = state.get("summary", "").strip()
    query = _get_query_for_search(state)
    response = await rag_chain.ainvoke(
        {
            "question": query,
            "context": _format_context(context_docs),
            "summary": summary or "요약 없음",
        }
    )
    answer_text = response.content if isinstance(response.content, str) else str(response.content)
    return {
        **state,
        "answer": answer_text,
    }


async def check_hallucination_async(state: AgentState) -> AgentState:
    docs = state.get("context", []) or []
    documents = _format_context(docs)
    answer = _get_answer_text(state)
    result = cast(
        Hallucination,
        await hallucination_chain.ainvoke(
            {
                "student_answer": answer,
                "documents": documents,
            }
        ),
    )
    return {
        **state,
        "hallucination_label": result.label,
        "hallucination_reason": result.reason,
    }


async def rewrite_async(state: AgentState) -> AgentState:
    summary = state.get("summary", "").strip() or "요약 없음"
    original_query = _get_query_for_reasoning(state)
    new_query = await rewrite_chain.ainvoke(
        {
            "query": original_query,
            "summary": summary,
            "hallucination_label": state.get("hallucination_label", "not hallucinated"),
            "hallucination_reason": state.get("hallucination_reason", ""),
        }
    )
    return {
        **state,
        "query": new_query,
        "paraphrased_query": new_query,
    }


async def basic_generate_async(state: AgentState) -> AgentState:
    summary = state.get("summary", "").strip() or "요약 없음"
    query = state.get("query", "")
    query_text = query if isinstance(query, str) else str(query)
    answer = await basic_chain.ainvoke(
        {
            "query": query_text,
            "summary": summary,
        }
    )
    return {
        **state,
        "answer": answer,
    }


async def summarize_messages_async(state: AgentState) -> AgentState:
    messages = state.get("messages", [])
    summary = state.get("summary", "")
    if not messages:
        return state

    prompt = (
        "summarize this chat history below"
        if not summary
        else "summarize this chat history while incorporating the previous summary"
    )
    summary_text = await summary_llm.ainvoke(
        f"{prompt}\n\nchat_history:\n{format_messages(messages)}\n\nsummary:{summary}"
    )
    new_summary = summary_text.content if isinstance(summary_text.content, str) else str(summary_text.content)
    return {
        **state,
        "summary": new_summary,
    }


__all__ = [
    "AgentState",
    "extract_user_query",
    "router",
    "router_async",
    "case_classification",
    "case_classification_async",
    "schedule_consumer_tool",
    "schedule_consumer_tool_async",
    "consumer_tool_router",
    "consumer_tool_followup_router",
    "consume_consumer_tool_result",
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

