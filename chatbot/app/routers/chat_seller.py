from __future__ import annotations

from typing import Any, AsyncGenerator, Dict, List, Optional

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


@router.post("", response_model=ChatResponse)
async def chat_seller(request: Request, payload: ChatSellerRequest) -> ChatResponse:
    """
    Seller-facing multi-turn chat endpoint (zone RAG).

    - thread_id 기본값: `seller:{user_id}:{session_id or 'default'}`
    """

    app = request.app
    graph = app.state.seller_graph

    thread_id = payload.thread_id or f"seller:{payload.user_id}:{payload.session_id or 'default'}"
    config: Dict[str, Any] = {"configurable": {"thread_id": thread_id}}

    state = {
        "messages": [
            {
                "role": "user",
                "content": payload.message,
            }
        ]
    }

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

    thread_id = payload.thread_id or f"seller:{payload.user_id}:{payload.session_id or 'default'}"
    config: Dict[str, Any] = {"configurable": {"thread_id": thread_id}}

    state = {
        "messages": [
            {
                "role": "user",
                "content": payload.message,
            }
        ]
    }

    async def event_stream() -> AsyncGenerator[bytes, None]:
        import json

        result = await graph.ainvoke(state, config=config)
        text = _extract_answer(result.get("messages", []))
        data = {"delta": text, "thread_id": thread_id}
        yield (json.dumps(data, ensure_ascii=False) + "\n").encode("utf-8")

    return StreamingResponse(event_stream(), media_type="application/json")


__all__ = ["router"]


