from __future__ import annotations

"""
판매자용 챗봇 API 라우터.

소비자용 챗봇과 동일한 패턴으로 구현되어 있으며,
데이터 소스만 itdaing_zone 컬렉션을 참조합니다.
"""

import asyncio
import json
import logging
from typing import Any, AsyncGenerator, Dict, List, Optional
from uuid import uuid4

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from langchain_core.messages import AIMessage, ToolMessage

router = APIRouter(prefix="/api/chat/seller", tags=["seller-chat"])
logger = logging.getLogger(__name__)


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


class ZoneRecommendation(BaseModel):
    """존 추천 응답 스키마"""
    type: str = "zone"
    zone_id: Optional[str] = None
    name: Optional[str] = None
    address: Optional[str] = None
    lat: Optional[float] = None
    lng: Optional[float] = None
    district: Optional[str] = None
    neighborhood: Optional[str] = None
    commercial_grade: Optional[str] = None
    traffic_score: Optional[int] = None
    competition_score: Optional[int] = None
    potential_score: Optional[int] = None
    best_products: Optional[List[str]] = None
    rent_per_day: Optional[int] = None
    metadata: Optional[Dict[str, Any]] = None


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


def _enrich_zone_recommendations(recommendations: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    존 추천 결과에서 프론트엔드에 필요한 필드를 추출/변환합니다.
    
    메타데이터에서 lat/lng, 상권 정보 등을 추출합니다.
    """
    enriched = []
    for rec in recommendations:
        metadata = rec.get("metadata", {}) or {}
        
        # 위도/경도: metadata에서 추출 또는 기존 값 유지
        lat = rec.get("lat") or metadata.get("lat")
        lng = rec.get("lon") or rec.get("lng") or metadata.get("lng") or metadata.get("lon")
        
        enriched_rec = {
            "type": "zone",
            "zone_id": rec.get("zone_id") or metadata.get("zone_id"),
            "name": rec.get("name") or metadata.get("zone_name") or metadata.get("name"),
            "address": rec.get("address") or metadata.get("address") or metadata.get("detailed_address"),
            "lat": lat,
            "lng": lng,
            "district": rec.get("district") or metadata.get("district"),
            "neighborhood": rec.get("neighborhood") or metadata.get("neighborhood"),
            "commercial_grade": metadata.get("commercial_grade"),
            "traffic_score": metadata.get("traffic_score"),
            "competition_score": metadata.get("competition_score"),
            "potential_score": metadata.get("potential_score"),
            "weekday_traffic": metadata.get("weekday_traffic"),
            "weekend_traffic": metadata.get("weekend_traffic"),
            "best_products": metadata.get("best_products"),
            "rent_per_day": metadata.get("rent_per_day"),
            "avg_sales": metadata.get("avg_sales"),
        }
        
        # None 값 제거
        enriched_rec = {k: v for k, v in enriched_rec.items() if v is not None}
        enriched.append(enriched_rec)
    
    return enriched


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
    - 데이터 소스: itdaing_zone 컬렉션 (소비자용 itdaing_popups와 분리)
    """

    app = request.app
    graph = app.state.seller_graph

    thread_id = _build_thread_id("seller", payload)
    config: Dict[str, Any] = {"configurable": {"thread_id": thread_id}}
    state = _initial_state(payload.message)

    result = await graph.ainvoke(state, config=config)
    answer = _extract_answer(result.get("messages", []))
    recommendations = result.get("recommendations")
    
    # 존 추천 정보 보강
    if recommendations:
        recommendations = _enrich_zone_recommendations(recommendations)

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
        result = await graph.ainvoke(state, config=config)
        text = _extract_answer(result.get("messages", []))
        recommendations = result.get("recommendations")
        
        if recommendations:
            recommendations = _enrich_zone_recommendations(recommendations)
        
        data = {"delta": text, "thread_id": thread_id}
        if recommendations:
            data["recommendations"] = recommendations
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
    
    if recommendations:
        recommendations = _enrich_zone_recommendations(recommendations)
    
    return ChatResponse(answer=answer, thread_id=thread_id, recommendations=recommendations)


@router.post(
    "/async/stream",
    summary="판매자 챗봇 Async 토큰 스트림 (SSE)",
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
    LangGraph `astream_events` 기반 실시간 토큰 스트리밍.
    
    소비자용 챗봇과 동일한 스트리밍 방식을 사용합니다.
    """

    app = request.app
    graph = app.state.seller_graph_async

    thread_id = _build_thread_id("seller", payload)
    config: Dict[str, Any] = {"configurable": {"thread_id": thread_id}}
    state = _initial_state(payload.message)

    async def event_stream() -> AsyncGenerator[bytes, None]:
        """
        astream_events를 사용한 실시간 토큰 스트리밍.
        """
        recommendations_sent = False
        final_answer = ""
        final_recommendations = None

        try:
            async for event in graph.astream_events(state, config=config, version="v2"):
                event_type = event.get("event")
                
                # 토큰 스트리밍
                if event_type == "on_llm_new_token":
                    token = event.get("data", {}).get("token", "")
                    if token:
                        payload_dict = {"delta": token, "thread_id": thread_id}
                        yield (json.dumps(payload_dict, ensure_ascii=False) + "\n").encode("utf-8")
                        await asyncio.sleep(0)
                
                # 노드 완료 시 recommendations 추출
                elif event_type == "on_chain_end":
                    output = event.get("data", {}).get("output", {})
                    if isinstance(output, dict):
                        # 최종 답변 추출
                        answer = output.get("answer")
                        if isinstance(answer, str) and answer.strip():
                            final_answer = answer
                        
                        # recommendations 추출
                        recs = output.get("recommendations")
                        if isinstance(recs, list) and len(recs) > 0 and not recommendations_sent:
                            final_recommendations = _enrich_zone_recommendations(recs)
                            recommendations_sent = True
                            
                            # recommendations만 먼저 전송
                            payload_dict = {
                                "thread_id": thread_id,
                                "recommendations": final_recommendations
                            }
                            yield (json.dumps(payload_dict, ensure_ascii=False) + "\n").encode("utf-8")
            
            # 스트리밍이 끝나면 최종 응답 전송 (토큰 스트리밍이 없었을 경우)
            if final_answer and not recommendations_sent:
                payload_dict = {"delta": final_answer, "thread_id": thread_id}
                if final_recommendations:
                    payload_dict["recommendations"] = final_recommendations
                yield (json.dumps(payload_dict, ensure_ascii=False) + "\n").encode("utf-8")
                
        except Exception as e:
            logger.exception(f"Seller stream error: {e}")
            error_payload = {
                "error": "STREAM_ERROR",
                "detail": "스트리밍 중 오류가 발생했습니다.",
                "thread_id": thread_id
            }
            yield (json.dumps(error_payload, ensure_ascii=False) + "\n").encode("utf-8")

    return StreamingResponse(
        event_stream(),
        media_type="application/json",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # Nginx 버퍼링 비활성화
        }
    )


__all__ = ["router"]
