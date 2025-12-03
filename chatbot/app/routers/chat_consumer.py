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
    summary="소비자 챗봇 실시간 토큰 스트리밍",
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
    astream_events를 사용한 실시간 LLM 토큰 스트리밍.

    - generate 노드의 LLM 토큰이 생성될 때마다 즉시 클라이언트로 전송
    - TTFT(Time To First Token)를 크게 개선하여 체감 속도 향상
    - 마지막에 recommendations 전송
    """

    import json
    import logging

    logger = logging.getLogger(__name__)

    app = request.app
    graph = app.state.consumer_graph_async

    thread_id = _build_thread_id("consumer", payload)
    config: Dict[str, Any] = {"configurable": {"thread_id": thread_id}}
    state = _initial_state(payload.message)

    async def event_stream() -> AsyncGenerator[bytes, None]:
        """
        astream_events v2를 사용한 실제 토큰 스트리밍.
        
        v14.3: 템플릿 응답(non-LLM)도 처리
        - LLM 토큰은 즉시 전송
        - basic_generate 템플릿 응답은 on_chain_end에서 추출
        """
        first_token_sent = False
        final_answer: Optional[str] = None
        final_recommendations: Optional[List[Dict[str, Any]]] = None

        # generate 노드에서만 스트리밍
        STREAMING_NODES = {"generate", "basic_generate"}
        
        try:
            async for event in graph.astream_events(state, config=config, version="v2"):
                event_type = event.get("event")
                event_name = event.get("name", "")
                
                # LLM 토큰 스트리밍
                if event_type == "on_chat_model_stream":
                    langgraph_node = event.get("metadata", {}).get("langgraph_node", "")
                    
                    if langgraph_node in STREAMING_NODES:
                        chunk = event.get("data", {}).get("chunk")
                        if chunk:
                            content = getattr(chunk, "content", None)
                            if content:
                                payload_dict: Dict[str, Any] = {
                                    "delta": content,
                                    "thread_id": thread_id,
                                }
                                if not first_token_sent:
                                    payload_dict["recommendations"] = []
                                    first_token_sent = True
                                
                                yield (json.dumps(payload_dict, ensure_ascii=False) + "\n").encode("utf-8")
                
                # 그래프 전체 종료 시 최종 state 추출
                elif event_type == "on_chain_end" and event_name == "LangGraph":
                    output = event.get("data", {}).get("output", {})
                    if isinstance(output, dict):
                        # recommendations
                        recs = output.get("recommendations")
                        if isinstance(recs, list) and len(recs) > 0:
                            final_recommendations = recs
                        
                        # 템플릿 응답 (스트리밍 없이 생성된 answer)
                        if not first_token_sent:
                            answer = output.get("answer")
                            if answer:
                                final_answer = answer
                            else:
                                # messages에서 추출
                                messages = output.get("messages", [])
                                final_answer = _extract_answer(messages)
            
            # 템플릿 응답 전송 (스트리밍이 없었던 경우)
            if not first_token_sent and final_answer:
                payload_dict = {
                    "delta": final_answer,
                    "thread_id": thread_id,
                    "recommendations": final_recommendations or [],
                }
                yield (json.dumps(payload_dict, ensure_ascii=False) + "\n").encode("utf-8")
            elif final_recommendations:
                # 스트리밍 후 recommendations만 전송
                payload_dict = {
                    "thread_id": thread_id,
                    "recommendations": final_recommendations,
                }
                yield (json.dumps(payload_dict, ensure_ascii=False) + "\n").encode("utf-8")

        except Exception as e:
            logger.error(f"[chat_consumer_async_stream] Error: {e}")
            error_payload = {
                "error": "STREAM_ERROR",
                "detail": str(e),
                "thread_id": thread_id,
            }
            yield (json.dumps(error_payload, ensure_ascii=False) + "\n").encode("utf-8")

    return StreamingResponse(
        event_stream(),
        media_type="application/x-ndjson",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # Nginx 버퍼링 비활성화
        },
    )


__all__ = ["router"]


