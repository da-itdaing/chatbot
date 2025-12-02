from __future__ import annotations

import asyncio
from typing import Any, AsyncGenerator, Dict, List, Optional
from uuid import uuid4

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from langchain_core.messages import AIMessage, ToolMessage


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
    recommendations: Optional[List[Dict[str, Any]]] = None


class ErrorResponse(BaseModel):
    error: str = Field(..., description="에러 유형 (예: BAD_REQUEST, RATE_LIMIT 등)")
    detail: str = Field(..., description="사람이 읽을 수 있는 에러 메시지")
    code: str = Field(..., description="클라이언트 로깅/분류용 내부 코드 (예: REQ_001)")


def _coerce_content(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts: List[str] = []
        for item in value:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str):
                    parts.append(text)
                else:
                    parts.append(str(item))
            else:
                parts.append(str(item))
        return "\n".join(parts)
    return str(value)


def _extract_answer(messages: List[Any]) -> str:
    for message in reversed(messages or []):
        if isinstance(message, ToolMessage):
            continue
        if isinstance(message, AIMessage):
            if message.tool_calls:
                continue
            content = message.content
        elif isinstance(message, dict):
            role = message.get("role")
            if role == "tool":
                continue
            if message.get("tool_calls"):
                continue
            content = message.get("content", "")
        else:
            content = getattr(message, "content", "")

        text = _coerce_content(content).strip()
        if text:
            return text
    return ""


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
    recommendations = result.get("recommendations")

    return ChatResponse(
        answer=answer,
        thread_id=thread_id,
        recommendations=recommendations,
    )


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
    recommendations = result.get("recommendations")
    return ChatResponse(answer=answer, thread_id=thread_id, recommendations=recommendations)


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
        """
        LangGraph async 그래프에서 answer가 갱신될 때마다 diff를 받아,
        작은 조각으로 잘라 여러 번 내려보냄으로써 토큰 스트리밍에 가까운 UX를 제공한다.
        
        멀티턴 대화에서 이전 턴의 answer가 다시 스트리밍되지 않도록,
        마지막으로 생성된 answer만 스트리밍한다.
        """

        CHUNK_SIZE = 20
        recommendations_sent = False
        final_answer = ""
        final_recommendations = None

        # 먼저 전체 스트림을 소비하여 최종 answer를 얻음
        async for chunk in graph.astream(state, config=config, stream_mode="values"):
            if not isinstance(chunk, dict):
                continue

            chunk_recommendations = chunk.get("recommendations")
            if isinstance(chunk_recommendations, list) and len(chunk_recommendations) > 0:
                final_recommendations = chunk_recommendations

            chunk_answer = chunk.get("answer")
            if isinstance(chunk_answer, str) and chunk_answer.strip():
                final_answer = chunk_answer.strip()

        # 최종 answer를 청크 단위로 스트리밍
        if final_answer:
            text_chunks = [
                final_answer[i : i + CHUNK_SIZE]
                for i in range(0, len(final_answer), CHUNK_SIZE)
            ]

            for idx, piece in enumerate(text_chunks):
                payload_dict: Dict[str, Any] = {"thread_id": thread_id}
                if piece.strip():
                    payload_dict["delta"] = piece
                # 첫 청크에 추천 결과 포함 (없으면 빈 배열로 이전 추천 초기화)
                if idx == 0 and not recommendations_sent:
                    payload_dict["recommendations"] = final_recommendations if final_recommendations else []
                    recommendations_sent = True

                yield (json.dumps(payload_dict, ensure_ascii=False) + "\n").encode(
                    "utf-8"
                )
                await asyncio.sleep(0)
        elif final_recommendations:
            # answer 없이 recommendations만 있는 경우
            payload_dict: Dict[str, Any] = {
                "thread_id": thread_id,
                "recommendations": final_recommendations,
            }
            yield (json.dumps(payload_dict, ensure_ascii=False) + "\n").encode("utf-8")

    return StreamingResponse(event_stream(), media_type="application/json")


__all__ = ["router"]


