from __future__ import annotations

"""
Consumer-facing multi-turn LangGraph graph.

This is a refactoring of `bot4c_v2_multiturn.py` into the new package layout.
The routing, classification, hallucination, rewrite, summarization, and
message-truncation logic are preserved as-is, but the vector store and
settings now come from `app.db.postgres` / `app.config`.
"""

import asyncio
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
from app.db.postgres import get_markets_vectorstore
from app.utils.search import WebSearchClient


settings = get_settings()

# ---------------------------------------------------------------------------
# Shared resources (vector store + LLMs)
# ---------------------------------------------------------------------------

_vectorstore = get_markets_vectorstore(settings)
_retriever = _vectorstore.as_retriever(
    search_type="similarity",
    search_kwargs={"k": settings.rag_top_k},
)
_web_search_client = WebSearchClient(settings)


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
generate_llm = _llm(temperature=0)
hallucination_llm = _llm(temperature=0)
rewrite_llm = _llm(temperature=0)
basic_llm = _llm(temperature=0.3)
summary_llm = _llm(temperature=0)


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


# ---------------------------------------------------------------------------
# Helper utilities
# ---------------------------------------------------------------------------


def _latest_user_message(messages: Sequence[BaseMessage]) -> HumanMessage:
    for message in reversed(messages):
        if isinstance(message, HumanMessage):
            return message
    raise ValueError("대화 내에 사용자 메시지가 존재하지 않습니다.")


def _format_messages(messages: Sequence[BaseMessage]) -> str:
    lines: List[str] = []
    for msg in messages:
        if not isinstance(msg, BaseMessage):
            continue
        role = msg.type.upper()
        content = msg.content if isinstance(msg.content, str) else str(msg.content)
        lines.append(f"[{role}] {content}")
    return "\n".join(lines)


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


def _maybe_extend_with_web_results(
    docs: Sequence[Document],
    query: str,
) -> List[Document]:
    """
    벡터 검색 결과가 부족할 때 DuckDuckGo 검색 결과를 fallback 컨텍스트로 추가한다.
    """

    materialized = list(docs)
    if materialized:
        return materialized
    if not _web_search_client.enabled:
        return materialized
    extra = _web_search_client.search_sync(query)
    if extra:
        materialized.extend(extra)
    return materialized


async def _maybe_extend_with_web_results_async(
    docs: Sequence[Document],
    query: str,
) -> List[Document]:
    """
    async 그래프 전용 fallback: 벡터 스토어 결과가 없으면 DuckDuckGo 검색을 수행.
    """

    materialized = list(docs)
    if materialized or not _web_search_client.enabled:
        return materialized
    extra = await _web_search_client.search_async(query)
    if extra:
        materialized.extend(extra)
    return materialized


async def _aretrieve_documents(query: str) -> List[Document]:
    """
    Retriever가 비동기 인터페이스를 지원하면 그대로 사용하고,
    그렇지 않으면 thread executor로 감싸 async 컨텍스트에서도 재사용한다.
    """

    if hasattr(_retriever, "ainvoke"):
        return await _retriever.ainvoke(query)  # type: ignore[attr-defined]
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _retriever.invoke, query)


# ---------------------------------------------------------------------------
# Router & prompts (identical to bot4c_v2_multiturn)
# ---------------------------------------------------------------------------


class Route(BaseModel):
    target: Literal["rag_answer", "general_answer"] = Field(
        description="Routing target for the user's query"
    )


router_system_prompt = """
You are an expert router that decides whether a user's question should be
answered using the rag_answer or the general_answer path.

The vector store contains detailed information about flea markets for
recommendations, including descriptions, locations, distances, categories,
attributes, and metadata. Users typically describe their situation or desired
experience (예: 여자친구랑 가기 좋은 곳, 배고픈데 뭐 먹지?).

Choose ``rag_answer`` unless the question is completely unrelated to market
recommendations, requests markets outside Gwangju, or clearly attempts abusive
load (예: 대량 계산, 전혀 무관한 작업). Return only one of the two labels.
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
당신은 사용자에게 마켓을 추천해주려는 최종목적을 가진 질문분류기이자 문장작성기입니다.

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


generate_prompt = PromptTemplate.from_template(
    """
당신은 광주 플리마켓 추천 서비스의 도우미입니다.
검색된 문서를 활용해 사용자의 질문을 최대 3문장으로 정중하고 친근하게 답변하세요.
답을 모를 때는 솔직히 모른다고 말하세요.

이전 대화 요약:
{summary}

질문:
{question}

문서:
{context}
""".strip()
)


rag_chain = generate_prompt | generate_llm


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
당신은 소비자에게 플리마켓 안내 및 추천을 해주는 서비스의 간단한 응답용 챗봇입니다.

질문이 다음과 같은 경우:
1) 광주광역시 외의 지역에 대한 마켓 추천 요청
2) 비현실적인 계산/대량 나열 등 서버 공격 의도가 있는 요청

"지원되지 않는 서비스입니다." 형태로 한 문장만 응답하세요.
그 외 인사/잡담 등 간단한 대화에는 친근하고 간결하게 응답하세요.
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
    latest = _latest_user_message(messages)
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


def retrieve(state: AgentState) -> AgentState:
    query_for_search = _get_query_for_search(state)
    docs = _retriever.invoke(query_for_search)
    docs = _maybe_extend_with_web_results(docs, query_for_search)
    return {
        **state,
        "context": docs,
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
        f"{prompt}\n\nchat_history:\n{_format_messages(messages)}\n\nsummary:{summary}"
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


async def retrieve_async(state: AgentState) -> AgentState:
    query_for_search = _get_query_for_search(state)
    docs = await _aretrieve_documents(query_for_search)
    docs = await _maybe_extend_with_web_results_async(docs, query_for_search)
    return {
        **state,
        "context": docs,
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
        f"{prompt}\n\nchat_history:\n{_format_messages(messages)}\n\nsummary:{summary}"
    )
    new_summary = summary_text.content if isinstance(summary_text.content, str) else str(summary_text.content)
    return {
        **state,
        "summary": new_summary,
    }


# ---------------------------------------------------------------------------
# Graph assembly
# ---------------------------------------------------------------------------


def build_consumer_graph(checkpointer=None):
    """
    Build a LangGraph StateGraph for the consumer chatbot, preserving the
    original bot4c_v2_multiturn flow while allowing an external checkpointer
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


def build_consumer_graph_async(checkpointer=None):
    """
    Async-first LangGraph 빌더.

    - LLM/벡터 스토어/웹검색 호출을 모두 await 기반으로 처리
    - LangGraph `astream`/`astream_events` 시나리오에 적합
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


__all__ = ["build_consumer_graph", "build_consumer_graph_async", "AgentState"]

