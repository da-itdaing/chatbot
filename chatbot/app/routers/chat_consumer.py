from __future__ import annotations

from typing import Any, AsyncGenerator, Dict, List, Optional
from uuid import uuid4

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from langchain_core.messages import AIMessage


router = APIRouter(prefix="/api/chat/consumer", tags=["consumer-chat"])


class ChatConsumerRequest(BaseModel):
    user_id: str = Field(..., description="고객 식별자 (Spring 세션/회원 ID 등)")
    session_id: Optional[str] = Field(
        default=None,
        description="대화 세션 ID (프론트 탭/대화 ID, 없으면 'default')",
    )
    message: str = Field(..., description="사용자 질문 텍스트")
    thread_id: Optional[str] = Field(
        default=None,
        description="명시적으로 사용할 LangGraph thread_id (옵션)",
    )
    restart_thread: bool = Field(
        default=False,
        description="이전 대화 상태가 깨졌을 때 새 thread_id로 재시작",
    )


class ChatResponse(BaseModel):
    answer: str
    thread_id: str


class ErrorResponse(BaseModel):
    error: str = Field(..., description="에러 유형 (예: BAD_REQUEST, RATE_LIMIT 등)")
    detail: str = Field(..., description="사람이 읽을 수 있는 에러 메시지")
    code: str = Field(..., description="클라이언트 로깅/분류용 내부 코드 (예: REQ_001)")


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


def _build_thread_id(prefix: str, payload: ChatConsumerRequest) -> str:
    """
    Thread ID 생성 규칙:
    - 기본: {prefix}:{user_id}:{session_id or default}
    - restart_thread=True 이면 UUID suffix를 붙여 완전히 새 thread를 시작
    - 사용자가 명시적으로 thread_id를 넘기면 그대로 사용 (단, restart_thread가 False일 때)
    """

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


@router.post(
    "",
    response_model=ChatResponse,
    summary="소비자 챗봇 동기 완료 응답",
    responses={
        400: {
            "model": ErrorResponse,
            "description": "필수 필드 누락 등 잘못된 요청",
        },
        429: {
            "model": ErrorResponse,
            "description": "OpenAI 또는 내부 큐의 Rate Limit 초과",
        },
        500: {
            "model": ErrorResponse,
            "description": "서버 내부 오류 (LangGraph/Tool 예외 등)",
        },
    },
)
async def chat_consumer(request: Request, payload: ChatConsumerRequest) -> ChatResponse:
    """
    Consumer-facing multi-turn chat endpoint.

    - thread_id 기본값: `consumer:{user_id}:{session_id or 'default'}`
    - LangGraph MessagesState 기반 멀티턴/멀티유저 지원
    """

    app = request.app
    graph = app.state.consumer_graph

    thread_id = _build_thread_id("consumer", payload)
    config: Dict[str, Any] = {"configurable": {"thread_id": thread_id}}
    state = _initial_state(payload.message)
    result = await graph.ainvoke(state, config=config)
    answer = _extract_answer(result.get("messages", []))

    return ChatResponse(answer=answer, thread_id=thread_id)


@router.post(
    "/stream",
    summary="소비자 챗봇 동기 스트림 (JSON 라인 1회 전송)",
    responses={
        200: {
            "description": 'Chunked JSON line, 예: {"delta":"...","thread_id":"consumer:..."}',
        },
        400: {
            "model": ErrorResponse,
            "description": "필수 필드 누락 등 잘못된 요청",
        },
        429: {
            "model": ErrorResponse,
            "description": "OpenAI 또는 내부 큐의 Rate Limit 초과",
        },
        500: {
            "model": ErrorResponse,
            "description": "서버 내부 오류 (LangGraph/Tool 예외 등)",
        },
    },
)
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

    thread_id = _build_thread_id("consumer", payload)
    config: Dict[str, Any] = {"configurable": {"thread_id": thread_id}}
    state = _initial_state(payload.message)

    async def event_stream() -> AsyncGenerator[bytes, None]:
        import json

        # 현재는 LangGraph 전체 실행이 끝난 후 최종 답변을 한 번에 스트림으로 흘려보냅니다.
        # 필요하면 여기에서 answer를 토큰 단위로 나누어 더 잘게 스트리밍할 수 있습니다.
        result = await graph.ainvoke(state, config=config)
        text = _extract_answer(result.get("messages", []))
        data = {"delta": text, "thread_id": thread_id}
        yield (json.dumps(data, ensure_ascii=False) + "\n").encode("utf-8")

    return StreamingResponse(event_stream(), media_type="application/json")


@router.post(
    "/async",
    response_model=ChatResponse,
    summary="소비자 챗봇 Async 완료 응답",
    responses={
        400: {
            "model": ErrorResponse,
            "description": "필수 필드 누락 등 잘못된 요청",
        },
        429: {
            "model": ErrorResponse,
            "description": "OpenAI 또는 내부 큐의 Rate Limit 초과",
        },
        500: {
            "model": ErrorResponse,
            "description": "서버 내부 오류 (LangGraph/Tool 예외 등)",
        },
    },
)
async def chat_consumer_async(
    request: Request,
    payload: ChatConsumerRequest,
) -> ChatResponse:
    """
    Async-first 버전의 소비자 챗봇 엔드포인트.

    - LangGraph async 그래프를 사용해 LLM/RAG 호출을 모두 await 처리
    - 향후 SSE/토큰 스트리밍과 궁합이 좋도록 분리
    """

    app = request.app
    graph = app.state.consumer_graph_async

    thread_id = _build_thread_id("consumer", payload)
    config: Dict[str, Any] = {"configurable": {"thread_id": thread_id}}
    state = _initial_state(payload.message)

    result = await graph.ainvoke(state, config=config)
    answer = _extract_answer(result.get("messages", []))
    return ChatResponse(answer=answer, thread_id=thread_id)


@router.post(
    "/async/stream",
    summary="소비자 챗봇 Async diff 스트림",
    responses={
        200: {
            "description": '여러 JSON 라인 스트림, 예: {"delta":"...","thread_id":"consumer:..."}',
        },
        400: {
            "model": ErrorResponse,
            "description": "필수 필드 누락 등 잘못된 요청",
        },
        429: {
            "model": ErrorResponse,
            "description": "OpenAI 또는 내부 큐의 Rate Limit 초과",
        },
        500: {
            "model": ErrorResponse,
            "description": "서버 내부 오류 (LangGraph/Tool 예외 등)",
        },
    },
)
async def chat_consumer_async_stream(
    request: Request,
    payload: ChatConsumerRequest,
) -> StreamingResponse:
    """
    Async 그래프 + LangGraph `astream`을 사용한 SSE 스타일 스트리밍.

    현재는 메시지 단위 diff를 흘려보내며, LangGraph `events` 기반 토큰 스트림으로
    확장할 여지를 남겨둔다.
    """

    import json

    app = request.app
    graph = app.state.consumer_graph_async

    thread_id = _build_thread_id("consumer", payload)
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


