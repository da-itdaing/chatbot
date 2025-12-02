from __future__ import annotations

import asyncio
from typing import Any, AsyncGenerator, Dict, List, Optional
from uuid import uuid4

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from langchain_core.messages import AIMessage, ToolMessage


router = APIRouter(prefix="/api/chat/seller", tags=["seller-chat"])


class ChatSellerRequest(BaseModel):
    user_id: str = Field(..., description="판매자 식별자 (Spring 세션/회원 ID 등)")
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
        description="이전 대화 상태가 손상된 경우 새 thread_id로 재시작",
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
                parts.append(text if isinstance(text, str) else str(item))
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


@router.post(
    "",
    response_model=ChatResponse,
    summary="판매자 챗봇 동기 완료 응답 (존 추천)",
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
    recommendations = result.get("recommendations")

    return ChatResponse(answer=answer, thread_id=thread_id, recommendations=recommendations)


@router.post(
    "/stream",
    summary="판매자 챗봇 동기 스트림 (JSON 라인 1회 전송)",
    responses={
        200: {
            "description": 'Chunked JSON line, 예: {"delta":"...","thread_id":"seller:..."}',
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


@router.post(
    "/async",
    response_model=ChatResponse,
    summary="판매자 챗봇 Async 완료 응답",
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
    recommendations = result.get("recommendations")
    return ChatResponse(answer=answer, thread_id=thread_id, recommendations=recommendations)


@router.post(
    "/async/stream",
    summary="판매자 챗봇 Async diff 스트림",
    responses={
        200: {
            "description": '여러 JSON 라인 스트림, 예: {"delta":"...","thread_id":"seller:..."}',
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
        """
        Seller 그래프의 answer를 여러 조각으로 나눠 스트리밍해
        토큰 스트리밍에 가까운 UX를 제공한다.
        """

        CHUNK_SIZE = 20
        previous = ""
        recommendations_sent = False

        async for chunk in graph.astream(state, config=config, stream_mode="values"):
            if not isinstance(chunk, dict):
                continue

            chunk_recommendations = chunk.get("recommendations")
            has_recommendations = (
                not recommendations_sent
                and isinstance(chunk_recommendations, list)
                and len(chunk_recommendations) > 0
            )

            chunk_answer = chunk.get("answer")
            if isinstance(chunk_answer, str) and chunk_answer.strip():
                full_text = chunk_answer
            else:
                # answer가 아직 준비되지 않은 단계에서는 delta를 전송하지 않는다.
                if not has_recommendations:
                    continue
                full_text = ""

            if not full_text and not has_recommendations:
                continue

            if full_text:
                if previous and full_text.startswith(previous):
                    new_text = full_text[len(previous) :]
                else:
                    new_text = full_text
                previous = full_text
            else:
                new_text = ""

            if not new_text.strip() and not has_recommendations:
                continue

            if new_text:
                text_chunks = [
                    new_text[i : i + CHUNK_SIZE]
                    for i in range(0, len(new_text), CHUNK_SIZE)
                ]
            else:
                text_chunks = [""]

            for idx, piece in enumerate(text_chunks):
                if not piece.strip() and not has_recommendations:
                    continue

                payload_dict: Dict[str, Any] = {"thread_id": thread_id}
                if piece.strip():
                    payload_dict["delta"] = piece
                if has_recommendations and idx == 0:
                    payload_dict["recommendations"] = chunk_recommendations
                    recommendations_sent = True

                yield (json.dumps(payload_dict, ensure_ascii=False) + "\n").encode(
                    "utf-8"
                )
                await asyncio.sleep(0)

    return StreamingResponse(event_stream(), media_type="application/json")


__all__ = ["router"]


