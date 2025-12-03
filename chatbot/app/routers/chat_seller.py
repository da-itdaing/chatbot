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

from app.db.postgres import get_zone_cell_stats, get_zone_geometry, get_zone_geometry_with_center

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


async def _enrich_zone_recommendations_async(recommendations: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    존 추천 결과에서 프론트엔드에 필요한 필드를 추출/변환합니다.
    
    메타데이터에서 lat/lng, 상권 정보 등을 추출하고,
    DB에서 셀 가용성 정보와 폴리곤 데이터를 조회합니다.
    """
    enriched = []
    for rec in recommendations:
        metadata = rec.get("metadata", {}) or {}
        
        # zone_id 추출 (다양한 소스에서)
        zone_id = (
            rec.get("zone_id") or 
            metadata.get("zone_id") or 
            metadata.get("id") or
            rec.get("id")
        )
        
        logger.debug(f"[enrich] rec keys: {list(rec.keys())}, zone_id: {zone_id}, metadata keys: {list(metadata.keys())}")
        
        # 위도/경도: metadata에서 추출 또는 기존 값 유지
        lat = rec.get("lat") or metadata.get("lat")
        lng = rec.get("lon") or rec.get("lng") or metadata.get("lng") or metadata.get("lon")
        
        enriched_rec = {
            "type": "zone",
            "zone_id": zone_id,
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
        
        # DB에서 셀 가용성 정보 및 폴리곤/좌표 조회
        if zone_id:
            try:
                zone_id_int = int(zone_id)
                cell_stats = await get_zone_cell_stats(zone_id_int)
                enriched_rec["total_cells"] = cell_stats.get("total_cells", 0)
                enriched_rec["available_cells"] = cell_stats.get("available_cells", 0)
                
                # 폴리곤 + 중심점 좌표 조회
                geo_data = await get_zone_geometry_with_center(zone_id_int)
                if geo_data:
                    polygon_str = geo_data.get("polygon")
                    if polygon_str:
                        try:
                            enriched_rec["polygon"] = json.loads(polygon_str)
                        except (json.JSONDecodeError, TypeError):
                            enriched_rec["polygon"] = polygon_str
                    
                    # 중심점 좌표 (DB에서 계산된 값 우선)
                    if geo_data.get("lat") and geo_data.get("lng"):
                        enriched_rec["lat"] = geo_data["lat"]
                        enriched_rec["lng"] = geo_data["lng"]
                
                # 팝업 등록 페이지 URL
                enriched_rec["popup_register_url"] = f"/seller/popups/create?zoneId={zone_id_int}"
                
            except (ValueError, TypeError) as e:
                logger.warning(f"Failed to get cell stats for zone_id={zone_id}: {e}")
        
        # None 값 제거
        enriched_rec = {k: v for k, v in enriched_rec.items() if v is not None}
        enriched.append(enriched_rec)
    
    return enriched


def _enrich_zone_recommendations(recommendations: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    동기 버전: 존 추천 결과에서 기본 필드만 추출합니다.
    셀 가용성 정보는 포함되지 않습니다.
    """
    enriched = []
    for rec in recommendations:
        metadata = rec.get("metadata", {}) or {}
        zone_id = rec.get("zone_id") or metadata.get("zone_id")
        
        # 위도/경도: metadata에서 추출 또는 기존 값 유지
        lat = rec.get("lat") or metadata.get("lat")
        lng = rec.get("lon") or rec.get("lng") or metadata.get("lng") or metadata.get("lon")
        
        enriched_rec = {
            "type": "zone",
            "zone_id": zone_id,
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
        
        # 팝업 등록 페이지 URL (zone_id가 있을 경우)
        if zone_id:
            enriched_rec["popup_register_url"] = f"/seller/popups/create?zoneId={zone_id}"
        
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
    
    # 존 추천 정보 보강 (비동기 버전으로 DB 조회 포함)
    if recommendations:
        recommendations = await _enrich_zone_recommendations_async(recommendations)

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
    
    셀 가용성 정보와 폴리곤 데이터를 포함합니다.
    """

    app = request.app
    graph = app.state.seller_graph_async

    thread_id = _build_thread_id("seller", payload)
    config: Dict[str, Any] = {
        "configurable": {"thread_id": thread_id},
        "recursion_limit": 50,  # 무한 루프 방지
    }
    state = _initial_state(payload.message)

    result = await graph.ainvoke(state, config=config)
    answer = _extract_answer(result.get("messages", []))
    recommendations = result.get("recommendations")
    
    # 셀 가용성 정보 포함 (async 버전)
    if recommendations:
        recommendations = await _enrich_zone_recommendations_async(recommendations)
    
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
    astream_events를 사용한 실시간 LLM 토큰 스트리밍.
    
    - generate/basic_generate 노드의 LLM 토큰이 생성될 때마다 즉시 전송
    - 소비자 챗봇과 동일한 스트리밍 패턴 적용
    - 마지막에 recommendations 전송 (셀 가용성, 폴리곤 포함)
    """

    app = request.app
    graph = app.state.seller_graph_async

    thread_id = _build_thread_id("seller", payload)
    config: Dict[str, Any] = {
        "configurable": {"thread_id": thread_id},
        "recursion_limit": 50,
    }
    initial = _initial_state(payload.message)

    async def event_stream() -> AsyncGenerator[bytes, None]:
        """
        astream_events v2를 사용한 실제 토큰 스트리밍.
        소비자 챗봇과 동일한 패턴 적용.
        """
        first_token_sent = False
        final_answer: Optional[str] = None
        final_recommendations: Optional[List[Dict[str, Any]]] = None

        # generate/basic_generate 노드에서만 스트리밍
        STREAMING_NODES = {"generate", "basic_generate"}
        
        try:
            async for event in graph.astream_events(initial, config=config, version="v2"):
                event_type = event.get("event")
                event_name = event.get("name", "")
                
                # LLM 토큰 스트리밍 (on_chat_model_stream)
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
                                await asyncio.sleep(0)  # 즉시 전송
                
                # 그래프 전체 종료 시 최종 state 추출
                elif event_type == "on_chain_end" and event_name == "LangGraph":
                    output = event.get("data", {}).get("output", {})
                    if isinstance(output, dict):
                        # recommendations 추출
                        recs = output.get("recommendations")
                        if isinstance(recs, list) and len(recs) > 0:
                            final_recommendations = await _enrich_zone_recommendations_async(recs)
                        
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
            logger.exception(f"Seller stream error: {e}")
            error_payload = {
                "error": "STREAM_ERROR",
                "detail": f"스트리밍 중 오류가 발생했습니다: {str(e)}",
                "thread_id": thread_id
            }
            yield (json.dumps(error_payload, ensure_ascii=False) + "\n").encode("utf-8")

    return StreamingResponse(
        event_stream(),
        media_type="application/x-ndjson",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # Nginx 버퍼링 비활성화
        }
    )


__all__ = ["router"]
