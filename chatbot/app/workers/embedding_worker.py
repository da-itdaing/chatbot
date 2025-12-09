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
    
    async def _table_exists(self, conn: asyncpg.Connection, table_name: str) -> bool:
        """테이블 존재 여부 확인"""
        result = await conn.fetchval("""
            SELECT EXISTS (
                SELECT FROM information_schema.tables 
                WHERE table_schema = 'public' 
                AND table_name = $1
            )
        """, table_name)
        return result or False
    
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
        """zone_area 임베딩 생성/업데이트 (셀 정보 + 상권 정보 포함)"""
        conn = await self._get_connection()
        try:
            # zone_area 조회 (region 정보 포함)
            zone = await conn.fetchrow("""
                SELECT za.id, za.name, za.status, za.max_capacity, za.notice,
                       r.name as region_name
                FROM zone_area za
                LEFT JOIN region r ON za.region_id = r.id
                WHERE za.id = $1
            """, zone_id)
            
            # 상권 정보 조회 (zone_commercial_info 테이블이 있으면)
            commercial_info = await conn.fetchrow("""
                SELECT commercial_grade, traffic_score, competition_score, potential_score,
                       weekday_traffic, weekend_traffic, best_products, rent_per_day, avg_sales,
                       detailed_address, neighborhood
                FROM zone_commercial_info
                WHERE zone_id = $1
            """, zone_id) if await self._table_exists(conn, 'zone_commercial_info') else None
            
            if not zone:
                raise ValueError(f"Zone {zone_id} not found")
            
            # 해당 존의 셀 목록 조회
            cells = await conn.fetch("""
                SELECT zc.id, zc.label, zc.detailed_address, zc.status, 
                       zc.max_capacity, zc.notice,
                       u.login_id as owner_login_id
                FROM zone_cell zc
                LEFT JOIN users u ON zc.owner_id = u.id
                WHERE zc.zone_area_id = $1
                ORDER BY zc.label
            """, zone_id)
            
            # 기존 임베딩 삭제
            await self.delete_zone_embedding(zone_id)
            
            # 셀 정보 텍스트 생성
            cell_text = ""
            available_cells = []
            total_capacity = 0
            
            for cell in cells:
                cell_status = cell["status"] or "PENDING"
                if cell_status == "APPROVED":
                    available_cells.append(cell["label"] or "미지정")
                    total_capacity += cell["max_capacity"] or 1
                cell_text += f"- {cell['label'] or '미지정'}: {cell['detailed_address'] or '주소 미정'} ({cell_status})\n"
            
            # 지역명 추출 (예: "광주 동구" -> "동구")
            region_name = zone['region_name'] or '광주광역시'
            district = region_name.replace('광주 ', '').replace('광주', '') if region_name else ''
            
            # 존 이름에서 핵심 키워드 추출 (검색 유사도 향상)
            zone_name = zone['name']
            keywords = []
            # 대학교/학교 관련 키워드
            if '대학교' in zone_name:
                base = zone_name.split('대학교')[0]
                keywords.extend([f"{base}대학교", f"{base}대", base])
            elif '대' in zone_name and '플리마켓' not in zone_name.split('대')[0]:
                base = zone_name.split('대')[0]
                if len(base) >= 2:
                    keywords.extend([f"{base}대", f"{base}대학교"])
            # 지역 관련 키워드
            if district:
                keywords.append(district)
            keywords_text = ', '.join(set(keywords)) if keywords else ''
            
            # 임베딩 텍스트 (핵심 키워드를 앞에 배치하여 유사도 향상)
            # 존 이름과 키워드를 반복하여 검색 유사도 강화
            text = f"""
## {zone_name}
{zone_name} {zone_name}
{f'관련 키워드: {keywords_text}' if keywords_text else ''}
{f'{keywords_text}' if keywords_text else ''}

### 위치
- 구역: {region_name}
- 주소: 광주광역시 {district} 일대

### 존 정보
- 상태: {zone['status']}
- 최대 수용 인원: {zone['max_capacity'] or '미정'}명
- 안내사항: {zone['notice'] or '없음'}

### 셀(부스) 현황
- 전체 셀 수: {len(cells)}개
- 승인된 셀: {len(available_cells)}개 ({', '.join(available_cells) if available_cells else '없음'})
- 총 수용 가능 인원: {total_capacity}명

### 셀 목록
{cell_text if cell_text else '등록된 셀이 없습니다.'}

이 존은 {region_name}에 위치한 플리마켓/팝업 운영 구역입니다.
{f'{zone_name}은(는) {keywords_text} 근처에 있습니다.' if keywords_text else ''}
판매자가 셀을 신청하여 팝업/플리마켓을 운영할 수 있습니다.
""".strip()
            
            metadata = {
                "type": "zone_detail",
                "zone_id": str(zone["id"]),
                "zone_name": zone["name"],
                "region": region_name,
                "status": zone["status"],
                "total_cells": len(cells),
                "available_cells": len(available_cells),
                "max_capacity": zone["max_capacity"],
            }
            
            # 상권 정보가 있으면 메타데이터에 추가
            if commercial_info:
                metadata.update({
                    "commercial_grade": commercial_info.get("commercial_grade"),
                    "traffic_score": commercial_info.get("traffic_score"),
                    "competition_score": commercial_info.get("competition_score"),
                    "potential_score": commercial_info.get("potential_score"),
                    "weekday_traffic": commercial_info.get("weekday_traffic"),
                    "weekend_traffic": commercial_info.get("weekend_traffic"),
                    "best_products": commercial_info.get("best_products"),
                    "rent_per_day": commercial_info.get("rent_per_day"),
                    "avg_sales": commercial_info.get("avg_sales"),
                    "detailed_address": commercial_info.get("detailed_address"),
                    "neighborhood": commercial_info.get("neighborhood"),
                })
            
            # 임베딩 추가
            self.zone_vectorstore.add_texts(texts=[text], metadatas=[metadata])
            logger.info(f"Zone {zone_id} 임베딩 완료 (셀 {len(cells)}개 포함)")
            
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


