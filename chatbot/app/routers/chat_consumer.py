from __future__ import annotations

from typing import Any, AsyncGenerator, Dict, List, Optional

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from langchain_core.messages import AIMessage


router = APIRouter(prefix="/api/chat/consumer", tags=["consumer-chat"])


class ChatConsumerRequest(BaseModel):
    user_id: str = Field(..., description="고객 식별자 (Spring 세션/회원 ID 등)")
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
async def chat_consumer(request: Request, payload: ChatConsumerRequest) -> ChatResponse:
    """
    Consumer-facing multi-turn chat endpoint.

    - thread_id 기본값: `consumer:{user_id}:{session_id or 'default'}`
    - LangGraph MessagesState 기반 멀티턴/멀티유저 지원
    """

    app = request.app
    graph = app.state.consumer_graph

    thread_id = payload.thread_id or f"consumer:{payload.user_id}:{payload.session_id or 'default'}"
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
async def chat_consumer_stream(
    request: Request,
    payload: ChatConsumerRequest,
) -> StreamingResponse:
    """
    Streaming version of the consumer chat endpoint.

    - Chunked JSON lines: {"delta": "...", "thread_id": "..."}
    - Spring에서는 line-by-line으로 읽으면서 스트리밍 처리 가능
    """

    app = request.app
    graph = app.state.consumer_graph

    thread_id = payload.thread_id or f"consumer:{payload.user_id}:{payload.session_id or 'default'}"
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

        # 현재는 LangGraph 전체 실행이 끝난 후 최종 답변을 한 번에 스트림으로 흘려보냅니다.
        # 필요하면 여기에서 answer를 토큰 단위로 나누어 더 잘게 스트리밍할 수 있습니다.
        result = await graph.ainvoke(state, config=config)
        text = _extract_answer(result.get("messages", []))
        data = {"delta": text, "thread_id": thread_id}
        yield (json.dumps(data, ensure_ascii=False) + "\n").encode("utf-8")

    return StreamingResponse(event_stream(), media_type="application/json")


__all__ = ["router"]


