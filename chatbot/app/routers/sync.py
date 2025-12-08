"""
Popup/Zone 동기화 API 라우터

Spring Boot에서 호출하여 PGVector를 동기화합니다.
- 단일 popup 동기화 (생성/수정)
- popup 삭제 시 벡터 제거
- 전체 popup 동기화 (초기화용)
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, BackgroundTasks
from pydantic import BaseModel, Field

from app.workers.embedding_worker import EmbeddingWorker

router = APIRouter(prefix="/api/sync", tags=["sync"])
logger = logging.getLogger(__name__)

# 싱글톤 워커 인스턴스
_worker: Optional[EmbeddingWorker] = None


def get_worker() -> EmbeddingWorker:
    """임베딩 워커 인스턴스 반환"""
    global _worker
    if _worker is None:
        _worker = EmbeddingWorker()
    return _worker


# ============= Request/Response Models =============

class PopupSyncRequest(BaseModel):
    """단일 popup 동기화 요청"""
    popup_id: int = Field(..., description="동기화할 popup ID")


class BulkSyncRequest(BaseModel):
    """전체 동기화 요청"""
    popup_ids: Optional[List[int]] = Field(
        None, 
        description="동기화할 popup ID 목록 (없으면 전체)"
    )
    clear_existing: bool = Field(
        True, 
        description="기존 임베딩 삭제 후 재생성 여부"
    )


class ZoneSyncRequest(BaseModel):
    """단일 zone 동기화 요청"""
    zone_id: int = Field(..., description="동기화할 zone_area ID")


class ZoneBulkSyncRequest(BaseModel):
    """전체 zone 동기화 요청"""
    zone_ids: Optional[List[int]] = Field(
        None,
        description="동기화할 zone_area ID 목록 (없으면 전체)"
    )
    clear_existing: bool = Field(
        True,
        description="기존 임베딩 삭제 후 재생성 여부"
    )


class SyncResponse(BaseModel):
    """동기화 응답"""
    status: str
    popup_id: Optional[int] = None
    message: Optional[str] = None


class BulkSyncResponse(BaseModel):
    """전체 동기화 응답"""
    status: str
    total: int = 0
    success: int = 0
    failed: int = 0
    errors: List[str] = Field(default_factory=list)


# ============= API Endpoints =============

@router.post("/popup", response_model=SyncResponse)
async def sync_popup(request: PopupSyncRequest) -> SyncResponse:
    """
    단일 popup 동기화 (생성/수정)
    
    Spring에서 popup 생성/수정 후 호출합니다.
    DB에서 popup 데이터를 조회하여 PGVector에 임베딩합니다.
    
    Args:
        request: popup_id 포함
    
    Returns:
        동기화 결과
    """
    worker = get_worker()
    try:
        await worker.embed_popup(request.popup_id)
        logger.info(f"[Sync] Popup {request.popup_id} 동기화 완료")
        return SyncResponse(
            status="success",
            popup_id=request.popup_id,
            message="임베딩 완료"
        )
    except ValueError as e:
        logger.warning(f"[Sync] Popup {request.popup_id} not found: {e}")
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"[Sync] Popup {request.popup_id} 동기화 실패: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/popup/{popup_id}", response_model=SyncResponse)
async def delete_popup_sync(popup_id: int) -> SyncResponse:
    """
    popup 삭제 시 임베딩 제거
    
    Spring에서 popup 삭제 후 호출합니다.
    
    Args:
        popup_id: 삭제할 popup ID
    
    Returns:
        삭제 결과
    """
    worker = get_worker()
    try:
        await worker.delete_popup_embedding(popup_id)
        logger.info(f"[Sync] Popup {popup_id} 임베딩 삭제 완료")
        return SyncResponse(
            status="deleted",
            popup_id=popup_id,
            message="임베딩 삭제됨"
        )
    except Exception as e:
        logger.error(f"[Sync] Popup {popup_id} 임베딩 삭제 실패: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/popups/bulk", response_model=BulkSyncResponse)
async def bulk_sync_popups(
    request: BulkSyncRequest = None,
    background_tasks: BackgroundTasks = None,
) -> BulkSyncResponse:
    """
    전체 popup 동기화 (초기화용)
    
    모든 승인된 popup을 PGVector에 임베딩합니다.
    초기 데이터 마이그레이션 또는 전체 재생성 시 사용합니다.
    
    Args:
        request: popup_ids (없으면 전체), clear_existing (기존 삭제 여부)
    
    Returns:
        동기화 결과
    """
    worker = get_worker()
    
    if request is None:
        request = BulkSyncRequest()
    
    result = BulkSyncResponse(status="processing")
    
    try:
        # DB에서 popup 목록 조회
        import asyncpg
        from app.config import get_settings
        
        settings = get_settings()
        conn = await asyncpg.connect(
            host=settings.postgres_host,
            database=settings.postgres_db,
            user=settings.postgres_user,
            password=settings.postgres_password,
            port=settings.postgres_port,
        )
        
        try:
            if request.popup_ids:
                # 특정 popup만 조회
                popup_ids = request.popup_ids
            else:
                # 모든 승인된 popup 조회
                rows = await conn.fetch("""
                    SELECT id FROM popup 
                    WHERE approval_status = 'APPROVED'
                    ORDER BY id
                """)
                popup_ids = [row["id"] for row in rows]
            
            result.total = len(popup_ids)
            logger.info(f"[Sync] 전체 동기화 시작: {result.total}개 popup")
            
            # 기존 임베딩 삭제 (선택적)
            if request.clear_existing and not request.popup_ids:
                logger.info("[Sync] 기존 itdaing_popups 임베딩 전체 삭제")
                popup_uuid = await conn.fetchval(
                    "SELECT uuid FROM langchain_pg_collection WHERE name = 'itdaing_popups'"
                )
                if popup_uuid:
                    await conn.execute(
                        "DELETE FROM langchain_pg_embedding WHERE collection_id = $1",
                        popup_uuid
                    )
            
            # 각 popup 임베딩
            for popup_id in popup_ids:
                try:
                    await worker.embed_popup(popup_id)
                    result.success += 1
                except Exception as e:
                    result.failed += 1
                    error_msg = f"popup:{popup_id} - {str(e)[:100]}"
                    result.errors.append(error_msg)
                    logger.warning(f"[Sync] {error_msg}")
            
            result.status = "completed"
            logger.info(
                f"[Sync] 전체 동기화 완료: "
                f"성공 {result.success}/{result.total}, 실패 {result.failed}"
            )
            
        finally:
            await conn.close()
        
        return result
        
    except Exception as e:
        logger.error(f"[Sync] 전체 동기화 실패: {e}")
        result.status = "failed"
        result.errors.append(str(e))
        raise HTTPException(status_code=500, detail=str(e))


# ============= Zone Sync Endpoints =============

@router.post("/zone", response_model=SyncResponse)
async def sync_zone(request: ZoneSyncRequest) -> SyncResponse:
    """
    단일 zone 동기화 (생성/수정)
    
    Spring에서 zone_area 생성/수정 후 호출합니다.
    DB에서 zone 데이터를 조회하여 PGVector(itdaing_zone)에 임베딩합니다.
    
    Args:
        request: zone_id 포함
    
    Returns:
        동기화 결과
    """
    worker = get_worker()
    try:
        await worker.embed_zone(request.zone_id)
        logger.info(f"[Sync] Zone {request.zone_id} 동기화 완료")
        return SyncResponse(
            status="success",
            popup_id=request.zone_id,  # reuse popup_id field for zone_id
            message="임베딩 완료"
        )
    except ValueError as e:
        logger.warning(f"[Sync] Zone {request.zone_id} not found: {e}")
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"[Sync] Zone {request.zone_id} 동기화 실패: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/zone/{zone_id}", response_model=SyncResponse)
async def delete_zone_sync(zone_id: int) -> SyncResponse:
    """
    zone 삭제 시 임베딩 제거
    
    Spring에서 zone_area 삭제 후 호출합니다.
    
    Args:
        zone_id: 삭제할 zone_area ID
    
    Returns:
        삭제 결과
    """
    worker = get_worker()
    try:
        await worker.delete_zone_embedding(zone_id)
        logger.info(f"[Sync] Zone {zone_id} 임베딩 삭제 완료")
        return SyncResponse(
            status="deleted",
            popup_id=zone_id,
            message="임베딩 삭제됨"
        )
    except Exception as e:
        logger.error(f"[Sync] Zone {zone_id} 임베딩 삭제 실패: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/zones/bulk", response_model=BulkSyncResponse)
async def bulk_sync_zones(
    request: ZoneBulkSyncRequest = None,
) -> BulkSyncResponse:
    """
    전체 zone 동기화 (초기화용)
    
    모든 zone_area를 PGVector(itdaing_zone)에 임베딩합니다.
    초기 데이터 마이그레이션 또는 전체 재생성 시 사용합니다.
    
    Args:
        request: zone_ids (없으면 전체), clear_existing (기존 삭제 여부)
    
    Returns:
        동기화 결과
    """
    worker = get_worker()
    
    if request is None:
        request = ZoneBulkSyncRequest()
    
    result = BulkSyncResponse(status="processing")
    
    try:
        import asyncpg
        from app.config import get_settings
        
        settings = get_settings()
        conn = await asyncpg.connect(
            host=settings.postgres_host,
            database=settings.postgres_db,
            user=settings.postgres_user,
            password=settings.postgres_password,
            port=settings.postgres_port,
        )
        
        try:
            if request.zone_ids:
                zone_ids = request.zone_ids
            else:
                # 모든 AVAILABLE zone 조회
                rows = await conn.fetch("""
                    SELECT id FROM zone_area 
                    WHERE status = 'AVAILABLE'
                    ORDER BY id
                """)
                zone_ids = [row["id"] for row in rows]
            
            result.total = len(zone_ids)
            logger.info(f"[Sync] 전체 zone 동기화 시작: {result.total}개 zone")
            
            # 기존 임베딩 삭제 (선택적)
            if request.clear_existing and not request.zone_ids:
                logger.info("[Sync] 기존 itdaing_zone 임베딩 전체 삭제")
                zone_uuid = await conn.fetchval(
                    "SELECT uuid FROM langchain_pg_collection WHERE name = 'itdaing_zone'"
                )
                if zone_uuid:
                    await conn.execute(
                        "DELETE FROM langchain_pg_embedding WHERE collection_id = $1",
                        zone_uuid
                    )
            
            # 각 zone 임베딩
            for zone_id in zone_ids:
                try:
                    await worker.embed_zone(zone_id)
                    result.success += 1
                except Exception as e:
                    result.failed += 1
                    error_msg = f"zone:{zone_id} - {str(e)[:100]}"
                    result.errors.append(error_msg)
                    logger.warning(f"[Sync] {error_msg}")
            
            result.status = "completed"
            logger.info(
                f"[Sync] 전체 zone 동기화 완료: "
                f"성공 {result.success}/{result.total}, 실패 {result.failed}"
            )
            
        finally:
            await conn.close()
        
        return result
        
    except Exception as e:
        logger.error(f"[Sync] 전체 zone 동기화 실패: {e}")
        result.status = "failed"
        result.errors.append(str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/status")
async def get_sync_status() -> Dict[str, Any]:
    """
    동기화 상태 조회
    
    현재 itdaing_popups 컬렉션의 임베딩 수를 반환합니다.
    """
    import asyncpg
    from app.config import get_settings
    
    settings = get_settings()
    conn = await asyncpg.connect(
        host=settings.postgres_host,
        database=settings.postgres_db,
        user=settings.postgres_user,
        password=settings.postgres_password,
        port=settings.postgres_port,
    )
    
    try:
        # 컬렉션별 임베딩 수
        popup_count = await conn.fetchval("""
            SELECT COUNT(*) FROM langchain_pg_embedding e
            JOIN langchain_pg_collection c ON e.collection_id = c.uuid
            WHERE c.name = 'itdaing_popups'
        """) or 0
        
        zone_count = await conn.fetchval("""
            SELECT COUNT(*) FROM langchain_pg_embedding e
            JOIN langchain_pg_collection c ON e.collection_id = c.uuid
            WHERE c.name = 'itdaing_zone'
        """) or 0
        
        # DB의 실제 popup/zone 수
        approved_popup_count = await conn.fetchval("""
            SELECT COUNT(*) FROM popup WHERE approval_status = 'APPROVED'
        """) or 0
        
        total_popup_count = await conn.fetchval(
            "SELECT COUNT(*) FROM popup"
        ) or 0
        
        zone_area_count = await conn.fetchval(
            "SELECT COUNT(*) FROM zone_area"
        ) or 0
        
        return {
            "embeddings": {
                "itdaing_popups": popup_count,
                "itdaing_zone": zone_count,
            },
            "database": {
                "popup_approved": approved_popup_count,
                "popup_total": total_popup_count,
                "zone_area": zone_area_count,
            },
            "sync_ratio": {
                "popup": f"{popup_count}/{approved_popup_count}" if approved_popup_count else "N/A",
                "zone": f"{zone_count}/{zone_area_count}" if zone_area_count else "N/A",
            }
        }
    finally:
        await conn.close()


__all__ = ["router"]

