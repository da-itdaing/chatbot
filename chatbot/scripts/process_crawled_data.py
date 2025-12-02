#!/usr/bin/env python3
"""
크롤링 데이터 정제 및 DB 삽입 스크립트

1. 크롤링된 JSON 데이터 로드
2. 중복 제거 및 정제
3. 이미지 처리 (플레이스홀더 URL 할당)
4. popup 테이블에 INSERT
5. zone_cell, zone_area 연결
"""

import asyncio
import json
import os
import random
from datetime import datetime
from pathlib import Path

import asyncpg
from dotenv import load_dotenv

# 환경변수 로드
load_dotenv(Path(__file__).parent.parent / "chatbot.env")

# 플레이스홀더 이미지 목록 (실제 S3에 있는 이미지들)
PLACEHOLDER_IMAGES = [
    "https://daitdaing-static-files.s3.ap-northeast-2.amazonaws.com/uploads/popup/0/2025-12-02/138f1ede7e648f8d.jpg",
    "https://daitdaing-static-files.s3.ap-northeast-2.amazonaws.com/uploads/popup/0/2025-12-02/7a39375ab46cec66.jpg",
    "https://daitdaing-static-files.s3.ap-northeast-2.amazonaws.com/uploads/popup/0/2025-12-02/469f1c2fdc5d8263.jpg",
    "https://daitdaing-static-files.s3.ap-northeast-2.amazonaws.com/uploads/popup/0/2025-12-02/bb8ae720269e0187.jpg",
    "https://daitdaing-static-files.s3.ap-northeast-2.amazonaws.com/uploads/popup/0/2025-12-02/8ee7d3a3132363f7.jpg",
]

# 구별 zone_area 매핑 (DB에서 조회한 실제 ID 사용)
DISTRICT_ZONE_AREA_MAP = {
    "동구": [53, 54, 55, 77],  # 문화전당, 대인시장, 금남로, 동구 팝업존
    "서구": [56, 57, 58, 59, 60],  # 첨단, 상무, 풍암, 화정, 치평
    "남구": [76],  # 남구 팝업존
    "북구": [],  # DB에서 조회 필요
    "광산구": [],  # DB에서 조회 필요
}


async def get_zone_area_mapping(conn: asyncpg.Connection) -> dict[str, list[int]]:
    """DB에서 구별 zone_area ID 조회"""
    rows = await conn.fetch("SELECT id, name FROM zone_area")
    
    mapping = {
        "동구": [],
        "서구": [],
        "남구": [],
        "북구": [],
        "광산구": [],
    }
    
    for row in rows:
        name = row["name"]
        id_ = row["id"]
        
        for district in mapping.keys():
            if district in name:
                mapping[district].append(id_)
                break
    
    print("=== zone_area 매핑 ===")
    for district, ids in mapping.items():
        print(f"  {district}: {ids}")
    
    return mapping


async def get_existing_zone_cell(
    conn: asyncpg.Connection,
    zone_area_id: int,
) -> int | None:
    """기존 zone_cell 가져오기 (새로 생성하지 않음)"""
    row = await conn.fetchrow(
        """
        SELECT id FROM zone_cell 
        WHERE zone_area_id = $1 
        LIMIT 1
        """,
        zone_area_id,
    )
    
    return row["id"] if row else None


async def insert_popup(
    conn: asyncpg.Connection,
    event: dict,
    zone_cell_id: int,
    default_seller_id: int = 1,
) -> int | None:
    """popup 테이블에 INSERT"""
    try:
        # 중복 확인 (이름으로)
        existing = await conn.fetchval(
            "SELECT id FROM popup WHERE name = $1",
            event["name"],
        )
        if existing:
            print(f"  [SKIP] 이미 존재: {event['name']}")
            return None
        
        # 날짜 파싱
        start_date = datetime.strptime(event["start_date"], "%Y-%m-%d").date()
        end_date = datetime.strptime(event["end_date"], "%Y-%m-%d").date()
        
        # 운영시간 문자열 변환
        op_hours = event.get("operating_hours", {})
        operating_time = f"평일 {op_hours.get('weekday', '10:00-18:00')}, 주말 {op_hours.get('weekend', '10:00-20:00')}"
        
        # INSERT (seller_id 필수)
        new_id = await conn.fetchval(
            """
            INSERT INTO popup (
                seller_id, zone_cell_id, name, description, 
                start_date, end_date, operating_time,
                approval_status, view_count, favorite_count,
                created_at, updated_at
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, 'APPROVED', 0, 0, NOW(), NOW())
            RETURNING id
            """,
            default_seller_id,
            zone_cell_id,
            event["name"],
            event["description"],
            start_date,
            end_date,
            operating_time,
        )
        
        return new_id
        
    except Exception as e:
        print(f"  [ERROR] INSERT 실패: {event['name']} - {e}")
        return None


async def main():
    """메인 처리"""
    print("=" * 60)
    print("데이터 정제 및 DB 삽입 시작")
    print("=" * 60)
    
    # 크롤링 데이터 로드
    data_file = Path(__file__).parent.parent / "data" / "crawled" / "playgwangju_events.json"
    
    if not data_file.exists():
        print(f"[ERROR] 데이터 파일 없음: {data_file}")
        return
    
    with open(data_file, "r", encoding="utf-8") as f:
        events = json.load(f)
    
    print(f"총 {len(events)}개 이벤트 로드")
    
    # DB 연결
    dsn = os.getenv("PGVECTOR_CONNECTION", "").replace("postgresql+psycopg://", "postgresql://")
    conn = await asyncpg.connect(dsn)
    
    try:
        # zone_area 매핑 조회
        zone_area_map = await get_zone_area_mapping(conn)
        
        # 삽입 카운터
        inserted = 0
        skipped = 0
        
        for event in events:
            district = event["district"]
            zone_area_ids = zone_area_map.get(district, [])
            
            if not zone_area_ids:
                print(f"  [SKIP] zone_area 없음: {district}")
                skipped += 1
                continue
            
            # 랜덤하게 zone_area 선택
            zone_area_id = random.choice(zone_area_ids)
            
            # 기존 zone_cell 가져오기
            zone_cell_id = await get_existing_zone_cell(conn, zone_area_id)
            
            if not zone_cell_id:
                print(f"  [SKIP] zone_cell 없음: {zone_area_id}")
                skipped += 1
                continue
            
            # popup 삽입
            new_id = await insert_popup(conn, event, zone_cell_id)
            
            if new_id:
                inserted += 1
                print(f"  [OK] {event['name']} (ID: {new_id})")
            else:
                skipped += 1
        
        print("\n" + "=" * 60)
        print(f"완료! 삽입: {inserted}, 스킵: {skipped}")
        
        # 최종 구별 분포 확인
        print("\n=== 최종 popup 구별 분포 ===")
        rows = await conn.fetch(
            """
            SELECT za.name, COUNT(*) as cnt
            FROM popup p
            JOIN zone_cell zc ON p.zone_cell_id = zc.id
            JOIN zone_area za ON zc.zone_area_id = za.id
            GROUP BY za.name
            ORDER BY cnt DESC
            """
        )
        for row in rows:
            print(f"  {row['name']}: {row['cnt']}개")
        
        total = await conn.fetchval("SELECT COUNT(*) FROM popup")
        print(f"\n총 popup 수: {total}개")
        
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())

