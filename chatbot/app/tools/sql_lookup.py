"""
정형 DB 직접 조회 도구.

RAG와 병행하여 정확한 정보(날짜, 시간, 주소 등)가 필요할 때 사용.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

import psycopg
from langchain_core.tools import tool

from app.config import get_settings


def _get_db_connection() -> psycopg.Connection:
    """정형 DB 연결 획득."""
    settings = get_settings()
    return psycopg.connect(
        host=settings.postgres_host,
        port=settings.postgres_port,
        dbname=settings.postgres_db,
        user=settings.postgres_user,
        password=settings.postgres_password,
    )


@tool("popup_sql_lookup")
def popup_sql_lookup(
    name: Optional[str] = None,
    category: Optional[str] = None,
    limit: int = 5,
) -> str:
    """
    정형 DB에서 팝업/이벤트 정보를 조회한다.
    정확한 날짜, 시간, 상태 정보가 필요할 때 사용.
    
    Args:
        name: 검색할 팝업 이름 (부분 일치)
        category: 카테고리 필터
        limit: 최대 결과 수
    
    Returns:
        JSON 형식의 팝업 정보 목록
    """
    try:
        conn = _get_db_connection()
        cur = conn.cursor()
        
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
        
        if name:
            query += " AND p.name ILIKE %s"
            params.append(f"%{name}%")
        
        query += " ORDER BY p.start_date DESC LIMIT %s"
        params.append(limit)
        
        cur.execute(query, params)
        columns = [desc[0] for desc in cur.description]
        results = []
        
        for row in cur.fetchall():
            item = dict(zip(columns, row))
            # 날짜 직렬화
            if item.get("start_date"):
                item["start_date"] = str(item["start_date"])
            if item.get("end_date"):
                item["end_date"] = str(item["end_date"])
            results.append(item)
        
        cur.close()
        conn.close()
        
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


@tool("popup_sql_lookup_async")
async def popup_sql_lookup_async(
    name: Optional[str] = None,
    category: Optional[str] = None,
    limit: int = 5,
) -> str:
    """
    (Async) 정형 DB에서 팝업/이벤트 정보를 조회한다.
    정확한 날짜, 시간, 상태 정보가 필요할 때 사용.
    """
    import asyncio
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        None,
        lambda: popup_sql_lookup.invoke({"name": name, "category": category, "limit": limit})
    )


@tool("zone_sql_lookup")
def zone_sql_lookup(
    name: Optional[str] = None,
    region: Optional[str] = None,
    limit: int = 5,
) -> str:
    """
    정형 DB에서 존/상권 정보를 조회한다.
    존 위치, 대여 가능 여부, 셀 정보가 필요할 때 사용.
    
    Args:
        name: 검색할 존 이름 (부분 일치)
        region: 지역 필터 (동구, 서구, 남구, 북구, 광산구)
        limit: 최대 결과 수
    
    Returns:
        JSON 형식의 존 정보 목록
    """
    try:
        conn = _get_db_connection()
        cur = conn.cursor()
        
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
        
        if name:
            query += " AND za.name ILIKE %s"
            params.append(f"%{name}%")
        
        if region:
            query += " AND r.name ILIKE %s"
            params.append(f"%{region}%")
        
        query += " GROUP BY za.id, za.name, za.status, za.max_capacity, za.notice, r.name"
        query += " ORDER BY za.name LIMIT %s"
        params.append(limit)
        
        cur.execute(query, params)
        columns = [desc[0] for desc in cur.description]
        results = []
        
        for row in cur.fetchall():
            item = dict(zip(columns, row))
            results.append(item)
        
        cur.close()
        conn.close()
        
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


@tool("zone_sql_lookup_async")
async def zone_sql_lookup_async(
    name: Optional[str] = None,
    region: Optional[str] = None,
    limit: int = 5,
) -> str:
    """
    (Async) 정형 DB에서 존/상권 정보를 조회한다.
    존 위치, 대여 가능 여부, 셀 정보가 필요할 때 사용.
    """
    import asyncio
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        None,
        lambda: zone_sql_lookup.invoke({"name": name, "region": region, "limit": limit})
    )


__all__ = [
    "popup_sql_lookup",
    "popup_sql_lookup_async",
    "zone_sql_lookup",
    "zone_sql_lookup_async",
]

