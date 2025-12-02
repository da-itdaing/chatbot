#!/usr/bin/env python3
"""
PGVector 임베딩 재생성 스크립트

1. 신규 popup 데이터 조회
2. OpenAI 임베딩 생성
3. langchain_pg_embedding에 추가
"""

import asyncio
import json
import os
import random
import uuid
from pathlib import Path

import asyncpg
from dotenv import load_dotenv
from langchain_openai import OpenAIEmbeddings

# 환경변수 로드
load_dotenv(Path(__file__).parent.parent / "chatbot.env")

# 플레이스홀더 이미지 목록
PLACEHOLDER_IMAGES = [
    "https://daitdaing-static-files.s3.ap-northeast-2.amazonaws.com/uploads/popup/0/2025-12-02/138f1ede7e648f8d.jpg",
    "https://daitdaing-static-files.s3.ap-northeast-2.amazonaws.com/uploads/popup/0/2025-12-02/7a39375ab46cec66.jpg",
    "https://daitdaing-static-files.s3.ap-northeast-2.amazonaws.com/uploads/popup/0/2025-12-02/469f1c2fdc5d8263.jpg",
    "https://daitdaing-static-files.s3.ap-northeast-2.amazonaws.com/uploads/popup/0/2025-12-02/bb8ae720269e0187.jpg",
    "https://daitdaing-static-files.s3.ap-northeast-2.amazonaws.com/uploads/popup/0/2025-12-02/8ee7d3a3132363f7.jpg",
]


async def get_new_popups(conn, existing_ids):
    """기존 임베딩에 없는 신규 popup 조회"""
    rows = await conn.fetch(
        """
        SELECT 
            p.id, p.name, p.description, p.start_date, p.end_date, p.operating_time,
            za.name as zone_name,
            zc.lat, zc.lng as lon, zc.detailed_address
        FROM popup p
        JOIN zone_cell zc ON p.zone_cell_id = zc.id
        JOIN zone_area za ON zc.zone_area_id = za.id
        WHERE p.approval_status = 'APPROVED'
        ORDER BY p.id
        """
    )
    
    new_popups = []
    for row in rows:
        if row["id"] not in existing_ids:
            zone_name = row["zone_name"]
            district = "동구"
            for d in ["동구", "서구", "남구", "북구", "광산구"]:
                if d in zone_name:
                    district = d
                    break
            
            new_popups.append({
                "id": row["id"],
                "name": row["name"],
                "description": row["description"] or "",
                "start_date": str(row["start_date"]),
                "end_date": str(row["end_date"]),
                "operating_time": row["operating_time"] or "",
                "zone_name": zone_name,
                "district": district,
                "lat": float(row["lat"]) if row["lat"] else 35.15,
                "lon": float(row["lon"]) if row["lon"] else 126.91,
                "address": row["detailed_address"] or f"광주광역시 {district}",
            })
    
    return new_popups


async def get_existing_market_ids(conn):
    """기존 임베딩의 market_id 목록 조회"""
    rows = await conn.fetch(
        """
        SELECT e.cmetadata->>'market_id' as market_id
        FROM langchain_pg_embedding e
        JOIN langchain_pg_collection c ON e.collection_id = c.uuid
        WHERE c.name = 'itdaing_popups'
        """
    )
    
    ids = set()
    for row in rows:
        mid = row["market_id"]
        if mid:
            try:
                ids.add(int(mid))
            except ValueError:
                pass
    
    return ids


def build_document_text(popup):
    """임베딩용 텍스트 생성"""
    parts = [
        f"마켓명: {popup['name']}",
        f"설명: {popup['description']}",
        f"위치: {popup['district']} {popup['zone_name']}",
        f"주소: {popup['address']}",
        f"기간: {popup['start_date']} ~ {popup['end_date']}",
        f"운영시간: {popup['operating_time']}",
    ]
    return "\n".join(parts)


def build_metadata(popup):
    """메타데이터 생성"""
    name = popup["name"].lower()
    if "플리마켓" in name or "마켓" in name:
        category = "플리마켓"
    elif "야시장" in name:
        category = "야시장"
    elif "축제" in name or "페스타" in name:
        category = "축제"
    elif "전시" in name:
        category = "전시"
    else:
        category = "행사"
    
    if "무료" in name or "무료" in popup["description"]:
        price_range = "무료"
    else:
        price_range = random.choice(["무료", "₩"])
    
    return {
        "market_id": popup["id"],
        "market_name": popup["name"],
        "address": popup["address"],
        "lat": popup["lat"],
        "lon": popup["lon"],
        "zone_id": popup["zone_name"],
        "market_category": category,
        "price_range": price_range,
        "operating_hours": {"weekday": "10:00-18:00", "weekend": "10:00-20:00"},
        "operating_days": ["금", "토", "일"],
        "market_rating": round(random.uniform(4.0, 4.8), 1),
        "market_ameni": [],
        "market_attribute": ["무료입장"],
        "search_tags": [popup["district"], category, popup["name"][:10]],
        "image_url": random.choice(PLACEHOLDER_IMAGES),
        "event_type": "regular",
        "distance_km": round(random.uniform(0.5, 3.0), 1),
    }


async def main():
    """메인 처리"""
    print("=" * 60)
    print("PGVector 임베딩 재생성 시작")
    print("=" * 60)
    
    dsn = os.getenv("PGVECTOR_CONNECTION", "").replace("postgresql+psycopg://", "postgresql://")
    conn = await asyncpg.connect(dsn)
    
    try:
        existing_ids = await get_existing_market_ids(conn)
        print(f"기존 임베딩 수: {len(existing_ids)}개")
        
        new_popups = await get_new_popups(conn, existing_ids)
        print(f"신규 popup 수: {len(new_popups)}개")
        
        if not new_popups:
            print("추가할 신규 popup 없음")
            return
        
        collection_uuid = await conn.fetchval(
            "SELECT uuid FROM langchain_pg_collection WHERE name = 'itdaing_popups'"
        )
        
        if not collection_uuid:
            print("[ERROR] 'itdaing_popups' 컬렉션 없음")
            return
        
        print(f"컬렉션 UUID: {collection_uuid}")
        
        embeddings = OpenAIEmbeddings(
            model=os.getenv("OPENAI_EMBEDDING_MODEL", "text-embedding-3-small"),
        )
        
        batch_size = 20
        inserted = 0
        
        for i in range(0, len(new_popups), batch_size):
            batch = new_popups[i:i + batch_size]
            texts = [build_document_text(p) for p in batch]
            
            print(f"  임베딩 생성 중... ({i+1}-{min(i+batch_size, len(new_popups))})")
            vectors = await embeddings.aembed_documents(texts)
            
            for j, (popup, vector) in enumerate(zip(batch, vectors)):
                doc_uuid = str(uuid.uuid4())
                metadata = build_metadata(popup)
                
                # 벡터를 PostgreSQL vector 형식으로 변환
                vector_str = "[" + ",".join(str(v) for v in vector) + "]"
                
                await conn.execute(
                    """
                    INSERT INTO langchain_pg_embedding (id, collection_id, embedding, document, cmetadata)
                    VALUES ($1, $2, $3::vector, $4, $5)
                    """,
                    doc_uuid,
                    collection_uuid,
                    vector_str,
                    texts[j],
                    json.dumps(metadata),
                )
                inserted += 1
        
        print(f"\n완료! {inserted}개 임베딩 추가")
        
        total = await conn.fetchval(
            """
            SELECT COUNT(*) FROM langchain_pg_embedding e
            JOIN langchain_pg_collection c ON e.collection_id = c.uuid
            WHERE c.name = 'itdaing_popups'
            """
        )
        print(f"총 itdaing_popups 임베딩 수: {total}개")
        
        print("\n=== 구별 임베딩 분포 ===")
        rows = await conn.fetch(
            """
            SELECT 
                CASE 
                    WHEN e.cmetadata->>'address' LIKE '%%동구%%' THEN '동구'
                    WHEN e.cmetadata->>'address' LIKE '%%서구%%' THEN '서구'
                    WHEN e.cmetadata->>'address' LIKE '%%남구%%' THEN '남구'
                    WHEN e.cmetadata->>'address' LIKE '%%북구%%' THEN '북구'
                    WHEN e.cmetadata->>'address' LIKE '%%광산구%%' THEN '광산구'
                    ELSE '기타'
                END as district,
                COUNT(*) as cnt
            FROM langchain_pg_embedding e
            JOIN langchain_pg_collection c ON e.collection_id = c.uuid
            WHERE c.name = 'itdaing_popups'
            GROUP BY district
            ORDER BY cnt DESC
            """
        )
        for row in rows:
            print(f"  {row['district']}: {row['cnt']}개")
        
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())

