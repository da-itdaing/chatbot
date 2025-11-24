from __future__ import annotations

from typing import Any, AsyncGenerator, Dict, List, Optional
from uuid import uuid4

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from langchain_core.messages import AIMessage


router = APIRouter(prefix="/api/chat/seller", tags=["seller-chat"])


class ChatSellerRequest(BaseModel):
    user_id: str = Field(..., description="판매자 식별자 (Spring 세션/회원 ID 등)")
    session_id: Optional[str] = Field(
        default=None,
        description="대화 세션 ID (없으면 user_id 기준으로 단일 세션)",
    )
    message: str = Field(..., description="사용자 질문 텍스트")
    thread_id: Optional[str] = Field(
        default=None,
        description="명시적으로 사용할 LangGraph thread_id (옵션)",
    )
    restart_thread: bool = Field(
        default=False,
        description="이전 대화 상태가 손상된 경우 새 thread_id로 재시작",
    )


class ChatResponse(BaseModel):
    answer: str
    thread_id: str


def _extract_answer(messages: List[Any]) -> str:
    if not messages:
        return ""
    last = messages[-1]
    if isinstance(last, AIMessage):
        content = last.content
    elif isinstance(last, dict) and last.get("role") == "assistant":
        content = last.get("content", "")
    else:
        content = getattr(last, "content", "")
    return content if isinstance(content, str) else str(content)


def _build_thread_id(prefix: str, payload: ChatSellerRequest) -> str:
    session_part = payload.session_id or "default"
    core = f"{payload.user_id}:{session_part}"
    if payload.restart_thread:
        return f"{prefix}:{core}:{uuid4().hex}"
    if payload.thread_id:
        return payload.thread_id
    return f"{prefix}:{core}"


def _initial_state(message: str) -> Dict[str, Any]:
    return {
        "messages": [
            {
                "role": "user",
                "content": message,
            }
        ]
    }


@router.post("", response_model=ChatResponse)
async def chat_seller(request: Request, payload: ChatSellerRequest) -> ChatResponse:
    """
    Seller-facing multi-turn chat endpoint (zone RAG).

    - thread_id 기본값: `seller:{user_id}:{session_id or 'default'}`
    """

    app = request.app
    graph = app.state.seller_graph

    thread_id = _build_thread_id("seller", payload)
    config: Dict[str, Any] = {"configurable": {"thread_id": thread_id}}
    state = _initial_state(payload.message)

    result = await graph.ainvoke(state, config=config)
    answer = _extract_answer(result.get("messages", []))

    return ChatResponse(answer=answer, thread_id=thread_id)


@router.post("/stream")
async def chat_seller_stream(
    request: Request,
    payload: ChatSellerRequest,
) -> StreamingResponse:
    """
    Streaming version of the seller chat endpoint.
    """

    app = request.app
    graph = app.state.seller_graph

    thread_id = _build_thread_id("seller", payload)
    config: Dict[str, Any] = {"configurable": {"thread_id": thread_id}}
    state = _initial_state(payload.message)

    async def event_stream() -> AsyncGenerator[bytes, None]:
        import json

        result = await graph.ainvoke(state, config=config)
        text = _extract_answer(result.get("messages", []))
        data = {"delta": text, "thread_id": thread_id}
        yield (json.dumps(data, ensure_ascii=False) + "\n").encode("utf-8")

    return StreamingResponse(event_stream(), media_type="application/json")


@router.post("/async", response_model=ChatResponse)
async def chat_seller_async(
    request: Request,
    payload: ChatSellerRequest,
) -> ChatResponse:
    """
    존 추천 챗봇의 async 버전 (LangGraph async 그래프 사용).
    """

    app = request.app
    graph = app.state.seller_graph_async

    thread_id = _build_thread_id("seller", payload)
    config: Dict[str, Any] = {"configurable": {"thread_id": thread_id}}
    state = _initial_state(payload.message)

    result = await graph.ainvoke(state, config=config)
    answer = _extract_answer(result.get("messages", []))
    return ChatResponse(answer=answer, thread_id=thread_id)


@router.post("/async/stream")
async def chat_seller_async_stream(
    request: Request,
    payload: ChatSellerRequest,
) -> StreamingResponse:
    """
    LangGraph `astream` 기반 SSE 응답.
    """

    import json

    app = request.app
    graph = app.state.seller_graph_async

    thread_id = _build_thread_id("seller", payload)
    config: Dict[str, Any] = {"configurable": {"thread_id": thread_id}}
    state = _initial_state(payload.message)

    async def event_stream() -> AsyncGenerator[bytes, None]:
        previous = ""
        async for chunk in graph.astream(state, config=config, stream_mode="values"):
            if not isinstance(chunk, dict):
                continue
            messages = chunk.get("messages", [])
            delta_text = _extract_answer(messages)
            if not delta_text:
                continue
            if previous and delta_text.startswith(previous):
                new_part = delta_text[len(previous) :]
            else:
                new_part = delta_text
            previous = delta_text
            if not new_part.strip():
                continue
            payload_dict = {"delta": new_part, "thread_id": thread_id}
            yield (json.dumps(payload_dict, ensure_ascii=False) + "\n").encode("utf-8")

    return StreamingResponse(event_stream(), media_type="application/json")


__all__ = ["router"]


