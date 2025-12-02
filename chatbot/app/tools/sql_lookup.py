"""
정형 DB 직접 조회 도구 (비동기).

RAG와 병행하여 정확한 정보(날짜, 시간, 주소 등)가 필요할 때 사용.
v11: asyncpg 기반 비동기 처리로 여러 유저 동시 접속 지원.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

import asyncpg
from langchain_core.tools import tool

from app.config import get_settings


# 모듈 레벨 커넥션 풀 (FastAPI lifespan에서 초기화)
_pool: Optional[asyncpg.Pool] = None


async def get_pool() -> asyncpg.Pool:
    """비동기 커넥션 풀 획득 (lazy initialization)."""
    global _pool
    if _pool is None:
        settings = get_settings()
        _pool = await asyncpg.create_pool(
            host=settings.postgres_host,
            port=settings.postgres_port,
            database=settings.postgres_db,
            user=settings.postgres_user,
            password=settings.postgres_password,
            min_size=2,
            max_size=10,
        )
    return _pool


async def close_pool() -> None:
    """커넥션 풀 종료 (앱 종료 시 호출)."""
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


@tool("popup_sql_lookup_async")
async def popup_sql_lookup_async(
    name: Optional[str] = None,
    category: Optional[str] = None,
    limit: int = 5,
) -> str:
    """
    정형 DB에서 팝업/이벤트 정보를 비동기로 조회한다.
    정확한 날짜, 시간, 상태 정보가 필요할 때 사용.
    
    Args:
        name: 검색할 팝업 이름 (부분 일치)
        category: 카테고리 필터
        limit: 최대 결과 수
    
    Returns:
        JSON 형식의 팝업 정보 목록
    """
    try:
        pool = await get_pool()
        
        # 기본 쿼리
        query = """
            SELECT 
                p.id,
                p.name,
                p.description,
                p.start_date,
                p.end_date,
                p.operating_time,
                p.approval_status,
                za.name as zone_area_name
            FROM popup p
            LEFT JOIN zone_cell zc ON p.zone_cell_id = zc.id
            LEFT JOIN zone_area za ON zc.zone_area_id = za.id
            WHERE p.approval_status = 'APPROVED'
        """
        params: List[Any] = []
        param_idx = 1
        
        if name:
            query += f" AND p.name ILIKE ${param_idx}"
            params.append(f"%{name}%")
            param_idx += 1
        
        query += f" ORDER BY p.start_date DESC LIMIT ${param_idx}"
        params.append(limit)
        
        async with pool.acquire() as conn:
            rows = await conn.fetch(query, *params)
        
        results = []
        for row in rows:
            item = dict(row)
            # 날짜 직렬화
            if item.get("start_date"):
                item["start_date"] = str(item["start_date"])
            if item.get("end_date"):
                item["end_date"] = str(item["end_date"])
            results.append(item)
        
        return json.dumps({
            "type": "popup_sql_lookup",
            "query": {"name": name, "category": category},
            "count": len(results),
            "results": results,
        }, ensure_ascii=False)
        
    except Exception as e:
        return json.dumps({
            "type": "popup_sql_lookup",
            "error": str(e),
            "count": 0,
            "results": [],
        }, ensure_ascii=False)


@tool("zone_sql_lookup_async")
async def zone_sql_lookup_async(
    name: Optional[str] = None,
    region: Optional[str] = None,
    limit: int = 5,
) -> str:
    """
    정형 DB에서 존/상권 정보를 비동기로 조회한다.
    존 위치, 대여 가능 여부, 셀 정보가 필요할 때 사용.
    
    Args:
        name: 검색할 존 이름 (부분 일치)
        region: 지역 필터 (동구, 서구, 남구, 북구, 광산구)
        limit: 최대 결과 수
    
    Returns:
        JSON 형식의 존 정보 목록
    """
    try:
        pool = await get_pool()
        
        # 기본 쿼리
        query = """
            SELECT 
                za.id,
                za.name,
                za.status,
                za.max_capacity,
                za.notice,
                r.name as region_name,
                COUNT(zc.id) as cell_count
            FROM zone_area za
            LEFT JOIN region r ON za.region_id = r.id
            LEFT JOIN zone_cell zc ON za.id = zc.zone_area_id
            WHERE za.status = 'AVAILABLE'
        """
        params: List[Any] = []
        param_idx = 1
        
        if name:
            query += f" AND za.name ILIKE ${param_idx}"
            params.append(f"%{name}%")
            param_idx += 1
        
        if region:
            query += f" AND r.name ILIKE ${param_idx}"
            params.append(f"%{region}%")
            param_idx += 1
        
        query += " GROUP BY za.id, za.name, za.status, za.max_capacity, za.notice, r.name"
        query += f" ORDER BY za.name LIMIT ${param_idx}"
        params.append(limit)
        
        async with pool.acquire() as conn:
            rows = await conn.fetch(query, *params)
        
        results = []
        for row in rows:
            item = dict(row)
            results.append(item)
        
        return json.dumps({
            "type": "zone_sql_lookup",
            "query": {"name": name, "region": region},
            "count": len(results),
            "results": results,
        }, ensure_ascii=False)
        
    except Exception as e:
        return json.dumps({
            "type": "zone_sql_lookup",
            "error": str(e),
            "count": 0,
            "results": [],
        }, ensure_ascii=False)


__all__ = [
    "get_pool",
    "close_pool",
    "popup_sql_lookup_async",
    "zone_sql_lookup_async",
]
