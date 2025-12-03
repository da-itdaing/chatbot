"""
임베딩 큐 처리 워커

DB 트리거가 embedding_queue에 등록한 작업을 처리합니다.
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Dict, List, Optional

import asyncpg
from langchain_openai import OpenAIEmbeddings
from langchain_postgres import PGVector
from pydantic import BaseModel

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


class QueueStatus(BaseModel):
    """큐 상태"""
    pending: int = 0
    processing: int = 0
    completed: int = 0
    failed: int = 0
    by_entity_type: Dict[str, Dict[str, int]] = {}


class ProcessResult(BaseModel):
    """처리 결과"""
    processed: int = 0
    success: int = 0
    failed: int = 0
    errors: List[str] = []


class EmbeddingWorker:
    """임베딩 큐 처리 워커"""
    
    def __init__(self):
        self._embeddings: Optional[OpenAIEmbeddings] = None
        self._popup_vectorstore: Optional[PGVector] = None
        self._zone_vectorstore: Optional[PGVector] = None
    
    @property
    def embeddings(self) -> OpenAIEmbeddings:
        if self._embeddings is None:
            self._embeddings = OpenAIEmbeddings(
                model=settings.openai_embedding_model,
                api_key=settings.openai_api_key,
            )
        return self._embeddings
    
    @property
    def popup_vectorstore(self) -> PGVector:
        if self._popup_vectorstore is None:
            self._popup_vectorstore = PGVector(
                embeddings=self.embeddings,
                collection_name="itdaing_popups",
                connection=settings.pgvector_connection,
                use_jsonb=True,
            )
        return self._popup_vectorstore
    
    @property
    def zone_vectorstore(self) -> PGVector:
        if self._zone_vectorstore is None:
            self._zone_vectorstore = PGVector(
                embeddings=self.embeddings,
                collection_name="itdaing_zone",
                connection=settings.pgvector_connection,
                use_jsonb=True,
            )
        return self._zone_vectorstore
    
    async def _get_connection(self) -> asyncpg.Connection:
        """DB 연결 생성"""
        return await asyncpg.connect(
            host=settings.postgres_host,
            database=settings.postgres_db,
            user=settings.postgres_user,
            password=settings.postgres_password,
            port=settings.postgres_port,
        )
    
    async def get_queue_status(self) -> QueueStatus:
        """큐 상태 조회"""
        conn = await self._get_connection()
        try:
            rows = await conn.fetch("""
                SELECT entity_type, status, COUNT(*) as cnt
                FROM embedding_queue
                GROUP BY entity_type, status
            """)
            
            status = QueueStatus()
            by_entity: Dict[str, Dict[str, int]] = {}
            
            for row in rows:
                entity_type = row["entity_type"]
                s = row["status"]
                cnt = row["cnt"]
                
                if entity_type not in by_entity:
                    by_entity[entity_type] = {}
                by_entity[entity_type][s] = cnt
                
                if s == "PENDING":
                    status.pending += cnt
                elif s == "PROCESSING":
                    status.processing += cnt
                elif s == "COMPLETED":
                    status.completed += cnt
                elif s == "FAILED":
                    status.failed += cnt
            
            status.by_entity_type = by_entity
            return status
        finally:
            await conn.close()
    
    async def process_queue(self, limit: int = 10) -> ProcessResult:
        """PENDING 작업 처리"""
        conn = await self._get_connection()
        result = ProcessResult()
        
        try:
            # PENDING 작업 가져오기 (PROCESSING으로 변경)
            tasks = await conn.fetch("""
                UPDATE embedding_queue
                SET status = 'PROCESSING'
                WHERE id IN (
                    SELECT id FROM embedding_queue
                    WHERE status = 'PENDING'
                    ORDER BY created_at
                    LIMIT $1
                    FOR UPDATE SKIP LOCKED
                )
                RETURNING id, entity_type, entity_id, action
            """, limit)
            
            result.processed = len(tasks)
            
            for task in tasks:
                task_id = task["id"]
                entity_type = task["entity_type"]
                entity_id = task["entity_id"]
                action = task["action"]
                
                try:
                    if action == "DELETE":
                        if entity_type == "popup":
                            await self.delete_popup_embedding(entity_id)
                        else:
                            await self.delete_zone_embedding(entity_id)
                    else:  # INSERT or UPDATE
                        if entity_type == "popup":
                            await self.embed_popup(entity_id)
                        else:
                            await self.embed_zone(entity_id)
                    
                    # 성공
                    await conn.execute("""
                        UPDATE embedding_queue
                        SET status = 'COMPLETED', processed_at = NOW()
                        WHERE id = $1
                    """, task_id)
                    result.success += 1
                    
                except Exception as e:
                    # 실패
                    error_msg = str(e)[:500]
                    await conn.execute("""
                        UPDATE embedding_queue
                        SET status = 'FAILED', 
                            error_message = $2,
                            retry_count = retry_count + 1
                        WHERE id = $1
                    """, task_id, error_msg)
                    result.failed += 1
                    result.errors.append(f"{entity_type}:{entity_id} - {error_msg}")
                    logger.error(f"임베딩 실패 {entity_type}:{entity_id}: {e}")
            
            return result
        finally:
            await conn.close()
    
    async def embed_popup(self, popup_id: int) -> None:
        """popup 임베딩 생성/업데이트"""
        conn = await self._get_connection()
        try:
            # popup 조회
            popup = await conn.fetchrow("""
                SELECT 
                    p.id, p.name, p.description, p.start_date, p.end_date,
                    p.operating_time, p.view_count, p.favorite_count,
                    zc.lat, zc.lng, zc.detailed_address, zc.label as cell_label,
                    za.name as zone_name, za.id as zone_area_id
                FROM popup p
                LEFT JOIN zone_cell zc ON p.zone_cell_id = zc.id
                LEFT JOIN zone_area za ON zc.zone_area_id = za.id
                WHERE p.id = $1
            """, popup_id)
            
            if not popup:
                raise ValueError(f"Popup {popup_id} not found")
            
            # 기존 임베딩 삭제
            await self.delete_popup_embedding(popup_id)
            
            # 임베딩 텍스트
            text = f"""
{popup['name']}

{popup['description'] or ''}

위치: {popup['detailed_address'] or '광주광역시'}
존: {popup['zone_name'] or '미지정'}
기간: {popup['start_date']} ~ {popup['end_date']}
운영시간: {popup['operating_time'] or '미정'}
""".strip()
            
            # 메타데이터
            metadata = {
                "market_id": str(popup["id"]),
                "market_name": popup["name"],
                "address": popup["detailed_address"] or "광주광역시",
                "lat": float(popup["lat"]) if popup["lat"] else None,
                "lon": float(popup["lng"]) if popup["lng"] else None,
                "zone_id": str(popup["zone_area_id"]) if popup["zone_area_id"] else None,
                "zone_name": popup["zone_name"],
                "cell_label": popup["cell_label"],
                "start_date": str(popup["start_date"]) if popup["start_date"] else None,
                "end_date": str(popup["end_date"]) if popup["end_date"] else None,
                "operating_hours": popup["operating_time"],
                "event_type": "popup",
            }
            
            # 임베딩 추가
            self.popup_vectorstore.add_texts(texts=[text], metadatas=[metadata])
            logger.info(f"Popup {popup_id} 임베딩 완료")
            
        finally:
            await conn.close()
    
    async def embed_zone(self, zone_id: int) -> None:
        """zone_area 임베딩 생성/업데이트"""
        conn = await self._get_connection()
        try:
            # zone_area 조회
            zone = await conn.fetchrow("""
                SELECT id, name, status, max_capacity, notice
                FROM zone_area
                WHERE id = $1
            """, zone_id)
            
            if not zone:
                raise ValueError(f"Zone {zone_id} not found")
            
            # 기존 임베딩 삭제
            await self.delete_zone_embedding(zone_id)
            
            # 상권 정보 (별도 테이블이 없으므로 기본값 사용)
            # TODO: 상권 정보 테이블 연동 시 업데이트
            
            text = f"""
## {zone['name']}

### 기본 정보
- 상태: {zone['status']}
- 수용 인원: {zone['max_capacity']}명
- 안내: {zone['notice'] or '없음'}
""".strip()
            
            metadata = {
                "type": "zone_detail",
                "zone_id": str(zone["id"]),
                "zone_name": zone["name"],
            }
            
            # 임베딩 추가
            self.zone_vectorstore.add_texts(texts=[text], metadatas=[metadata])
            logger.info(f"Zone {zone_id} 임베딩 완료")
            
        finally:
            await conn.close()
    
    async def delete_popup_embedding(self, popup_id: int) -> None:
        """popup 임베딩 삭제"""
        conn = await self._get_connection()
        try:
            # market_id로 임베딩 삭제
            popup_uuid = await conn.fetchval(
                "SELECT uuid FROM langchain_pg_collection WHERE name = 'itdaing_popups'"
            )
            if popup_uuid:
                await conn.execute("""
                    DELETE FROM langchain_pg_embedding
                    WHERE collection_id = $1 
                      AND cmetadata->>'market_id' = $2
                """, popup_uuid, str(popup_id))
                logger.info(f"Popup {popup_id} 임베딩 삭제됨")
        finally:
            await conn.close()
    
    async def delete_zone_embedding(self, zone_id: int) -> None:
        """zone_area 임베딩 삭제"""
        conn = await self._get_connection()
        try:
            zone_uuid = await conn.fetchval(
                "SELECT uuid FROM langchain_pg_collection WHERE name = 'itdaing_zone'"
            )
            if zone_uuid:
                await conn.execute("""
                    DELETE FROM langchain_pg_embedding
                    WHERE collection_id = $1 
                      AND cmetadata->>'zone_id' = $2
                """, zone_uuid, str(zone_id))
                logger.info(f"Zone {zone_id} 임베딩 삭제됨")
        finally:
            await conn.close()
    
    async def cleanup_completed(self, days: int = 7) -> int:
        """오래된 완료 작업 정리"""
        conn = await self._get_connection()
        try:
            result = await conn.fetchval("""
                SELECT cleanup_embedding_queue($1)
            """, days)
            return result or 0
        finally:
            await conn.close()


__all__ = ["EmbeddingWorker"]


