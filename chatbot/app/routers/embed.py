"""
임베딩 관리 API 라우터

- 큐 상태 조회
- 수동 임베딩 트리거
- 큐 처리
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel, Field

from app.workers.embedding_worker import EmbeddingWorker

router = APIRouter(prefix="/api/embed", tags=["embedding"])
logger = logging.getLogger(__name__)

# 싱글톤 워커 인스턴스
_worker: Optional[EmbeddingWorker] = None


def get_worker() -> EmbeddingWorker:
    """임베딩 워커 인스턴스 반환"""
    global _worker
    if _worker is None:
        _worker = EmbeddingWorker()
    return _worker


class QueueStatus(BaseModel):
    """큐 상태 응답"""
    pending: int = 0
    processing: int = 0
    completed: int = 0
    failed: int = 0
    by_entity_type: Dict[str, Dict[str, int]] = Field(default_factory=dict)


class ProcessResult(BaseModel):
    """처리 결과 응답"""
    processed: int = 0
    success: int = 0
    failed: int = 0
    errors: List[str] = Field(default_factory=list)


class EmbedRequest(BaseModel):
    """수동 임베딩 요청"""
    entity_type: str = Field(..., description="'popup' 또는 'zone_area'")
    entity_id: int = Field(..., description="엔티티 ID")


@router.get("/queue/status", response_model=QueueStatus)
async def get_queue_status() -> QueueStatus:
    """
    임베딩 큐 상태 조회
    
    Returns:
        큐 상태 (PENDING, PROCESSING, COMPLETED, FAILED 수)
    """
    worker = get_worker()
    return await worker.get_queue_status()


@router.post("/queue/process", response_model=ProcessResult)
async def process_queue(
    limit: int = 10,
    background_tasks: BackgroundTasks = None,
) -> ProcessResult:
    """
    큐의 PENDING 작업 처리
    
    Args:
        limit: 처리할 최대 작업 수 (기본 10)
    
    Returns:
        처리 결과
    """
    worker = get_worker()
    return await worker.process_queue(limit=limit)


@router.post("/popup/{popup_id}")
async def embed_popup(popup_id: int) -> Dict[str, Any]:
    """
    특정 popup 임베딩 (수동)
    
    Args:
        popup_id: 팝업 ID
    
    Returns:
        결과 메시지
    """
    worker = get_worker()
    try:
        await worker.embed_popup(popup_id)
        return {"status": "success", "popup_id": popup_id}
    except Exception as e:
        logger.error(f"Popup {popup_id} 임베딩 실패: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/zone/{zone_id}")
async def embed_zone(zone_id: int) -> Dict[str, Any]:
    """
    특정 zone_area 임베딩 (수동)
    
    Args:
        zone_id: zone_area ID
    
    Returns:
        결과 메시지
    """
    worker = get_worker()
    try:
        await worker.embed_zone(zone_id)
        return {"status": "success", "zone_id": zone_id}
    except Exception as e:
        logger.error(f"Zone {zone_id} 임베딩 실패: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/popup/{popup_id}")
async def delete_popup_embedding(popup_id: int) -> Dict[str, Any]:
    """
    특정 popup 임베딩 삭제
    
    Args:
        popup_id: 팝업 ID
    
    Returns:
        결과 메시지
    """
    worker = get_worker()
    try:
        await worker.delete_popup_embedding(popup_id)
        return {"status": "deleted", "popup_id": popup_id}
    except Exception as e:
        logger.error(f"Popup {popup_id} 임베딩 삭제 실패: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/zone/{zone_id}")
async def delete_zone_embedding(zone_id: int) -> Dict[str, Any]:
    """
    특정 zone_area 임베딩 삭제
    
    Args:
        zone_id: zone_area ID
    
    Returns:
        결과 메시지
    """
    worker = get_worker()
    try:
        await worker.delete_zone_embedding(zone_id)
        return {"status": "deleted", "zone_id": zone_id}
    except Exception as e:
        logger.error(f"Zone {zone_id} 임베딩 삭제 실패: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/queue/cleanup")
async def cleanup_queue(days: int = 7) -> Dict[str, Any]:
    """
    오래된 완료 작업 정리
    
    Args:
        days: 보관 기간 (기본 7일)
    
    Returns:
        삭제된 작업 수
    """
    worker = get_worker()
    deleted = await worker.cleanup_completed(days)
    return {"deleted": deleted, "days": days}


__all__ = ["router"]


