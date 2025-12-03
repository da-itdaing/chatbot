#!/usr/bin/env python3
"""
PGVector 전체 임베딩 재생성 스크립트

- itdaing_popups: 197개 popup 전체 임베딩
- itdaing_zone: 30개 zone_area + 상권정보 전체 임베딩
"""

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List

sys.path.insert(0, str(Path(__file__).parent.parent))

import asyncpg
from dotenv import load_dotenv
from langchain_openai import OpenAIEmbeddings
from langchain_postgres import PGVector

load_dotenv(Path(__file__).parent.parent / "chatbot.env")


# 상권 데이터 (zone_area에 추가할 정보)
COMMERCIAL_DATA = {
    # 동구
    "51": {"district": "동구", "neighborhood": "용연동", "commercial_grade": "B", "traffic_score": 65, "weekday_traffic": 3500, "weekend_traffic": 8000, "rent_per_day": 30000, "avg_sales": 350000, "best_products": ["등산용품", "간식", "음료"], "competition_score": 30, "potential_score": 75},
    "52": {"district": "동구", "neighborhood": "충장동", "commercial_grade": "S", "traffic_score": 95, "weekday_traffic": 45000, "weekend_traffic": 65000, "rent_per_day": 80000, "avg_sales": 850000, "best_products": ["패션의류", "액세서리", "스트리트푸드"], "competition_score": 85, "potential_score": 90},
    "53": {"district": "동구", "neighborhood": "금남로", "commercial_grade": "A", "traffic_score": 80, "weekday_traffic": 25000, "weekend_traffic": 40000, "rent_per_day": 50000, "avg_sales": 550000, "best_products": ["아트/공예품", "책/문구", "디자인소품"], "competition_score": 50, "potential_score": 85},
    "54": {"district": "동구", "neighborhood": "대인동", "commercial_grade": "B+", "traffic_score": 70, "weekday_traffic": 8000, "weekend_traffic": 15000, "rent_per_day": 35000, "avg_sales": 400000, "best_products": ["빈티지", "수공예품", "로컬푸드"], "competition_score": 40, "potential_score": 80},
    "55": {"district": "동구", "neighborhood": "금남로", "commercial_grade": "A", "traffic_score": 85, "weekday_traffic": 30000, "weekend_traffic": 45000, "rent_per_day": 60000, "avg_sales": 600000, "best_products": ["패션", "액세서리", "화장품"], "competition_score": 60, "potential_score": 85},
    # 서구
    "56": {"district": "서구", "neighborhood": "첨단동", "commercial_grade": "A", "traffic_score": 75, "weekday_traffic": 20000, "weekend_traffic": 35000, "rent_per_day": 45000, "avg_sales": 500000, "best_products": ["IT기기", "스타트업굿즈", "카페음료"], "competition_score": 45, "potential_score": 80},
    "57": {"district": "서구", "neighborhood": "치평동", "commercial_grade": "A+", "traffic_score": 88, "weekday_traffic": 35000, "weekend_traffic": 55000, "rent_per_day": 70000, "avg_sales": 700000, "best_products": ["브런치", "디저트", "라이프스타일"], "competition_score": 70, "potential_score": 85},
    "58": {"district": "서구", "neighborhood": "풍암동", "commercial_grade": "B+", "traffic_score": 68, "weekday_traffic": 12000, "weekend_traffic": 25000, "rent_per_day": 40000, "avg_sales": 450000, "best_products": ["키즈용품", "가족체험", "수공예"], "competition_score": 35, "potential_score": 75},
    "59": {"district": "서구", "neighborhood": "화정동", "commercial_grade": "B", "traffic_score": 60, "weekday_traffic": 10000, "weekend_traffic": 20000, "rent_per_day": 35000, "avg_sales": 380000, "best_products": ["반려동물용품", "원예", "핸드메이드"], "competition_score": 30, "potential_score": 70},
    "60": {"district": "서구", "neighborhood": "치평동", "commercial_grade": "B+", "traffic_score": 65, "weekday_traffic": 15000, "weekend_traffic": 28000, "rent_per_day": 38000, "avg_sales": 420000, "best_products": ["생활용품", "인테리어소품", "플랜트"], "competition_score": 40, "potential_score": 72},
    # 남구
    "61": {"district": "남구", "neighborhood": "양림동", "commercial_grade": "A", "traffic_score": 78, "weekday_traffic": 18000, "weekend_traffic": 38000, "rent_per_day": 55000, "avg_sales": 580000, "best_products": ["아트/공예", "빈티지", "카페음료"], "competition_score": 55, "potential_score": 88},
    "62": {"district": "남구", "neighborhood": "봉선동", "commercial_grade": "B+", "traffic_score": 70, "weekday_traffic": 15000, "weekend_traffic": 30000, "rent_per_day": 42000, "avg_sales": 480000, "best_products": ["패션", "액세서리", "화장품"], "competition_score": 45, "potential_score": 75},
    "63": {"district": "남구", "neighborhood": "주월동", "commercial_grade": "B", "traffic_score": 62, "weekday_traffic": 10000, "weekend_traffic": 22000, "rent_per_day": 32000, "avg_sales": 350000, "best_products": ["키즈용품", "가정용품", "의류"], "competition_score": 35, "potential_score": 68},
    "64": {"district": "남구", "neighborhood": "진월동", "commercial_grade": "B", "traffic_score": 58, "weekday_traffic": 8000, "weekend_traffic": 18000, "rent_per_day": 28000, "avg_sales": 300000, "best_products": ["반려동물", "원예", "수공예"], "competition_score": 25, "potential_score": 65},
    "65": {"district": "남구", "neighborhood": "송하동", "commercial_grade": "B-", "traffic_score": 55, "weekday_traffic": 6000, "weekend_traffic": 15000, "rent_per_day": 25000, "avg_sales": 280000, "best_products": ["농산물", "수제먹거리", "생활용품"], "competition_score": 20, "potential_score": 62},
    # 북구
    "66": {"district": "북구", "neighborhood": "용봉동", "commercial_grade": "A", "traffic_score": 82, "weekday_traffic": 28000, "weekend_traffic": 45000, "rent_per_day": 58000, "avg_sales": 620000, "best_products": ["대학생패션", "푸드트럭", "빈티지"], "competition_score": 60, "potential_score": 85},
    "67": {"district": "북구", "neighborhood": "문흥동", "commercial_grade": "B+", "traffic_score": 68, "weekday_traffic": 12000, "weekend_traffic": 25000, "rent_per_day": 38000, "avg_sales": 420000, "best_products": ["가족체험", "키즈", "수공예"], "competition_score": 35, "potential_score": 72},
    "68": {"district": "북구", "neighborhood": "일곡동", "commercial_grade": "B", "traffic_score": 60, "weekday_traffic": 10000, "weekend_traffic": 20000, "rent_per_day": 32000, "avg_sales": 360000, "best_products": ["생활용품", "인테리어", "반려동물"], "competition_score": 30, "potential_score": 68},
    "69": {"district": "북구", "neighborhood": "오룡동", "commercial_grade": "B", "traffic_score": 58, "weekday_traffic": 8000, "weekend_traffic": 18000, "rent_per_day": 28000, "avg_sales": 320000, "best_products": ["스포츠용품", "건강식품", "캠핑"], "competition_score": 25, "potential_score": 65},
    "70": {"district": "북구", "neighborhood": "신안동", "commercial_grade": "B+", "traffic_score": 65, "weekday_traffic": 12000, "weekend_traffic": 24000, "rent_per_day": 36000, "avg_sales": 400000, "best_products": ["패션", "액세서리", "수공예"], "competition_score": 38, "potential_score": 70},
    # 광산구
    "71": {"district": "광산구", "neighborhood": "수완동", "commercial_grade": "A", "traffic_score": 85, "weekday_traffic": 32000, "weekend_traffic": 55000, "rent_per_day": 65000, "avg_sales": 680000, "best_products": ["패션", "라이프스타일", "브런치"], "competition_score": 65, "potential_score": 88},
    "72": {"district": "광산구", "neighborhood": "하남동", "commercial_grade": "A-", "traffic_score": 78, "weekday_traffic": 25000, "weekend_traffic": 45000, "rent_per_day": 55000, "avg_sales": 580000, "best_products": ["키즈용품", "가족체험", "푸드"], "competition_score": 55, "potential_score": 82},
    "73": {"district": "광산구", "neighborhood": "신가동", "commercial_grade": "B+", "traffic_score": 68, "weekday_traffic": 15000, "weekend_traffic": 30000, "rent_per_day": 40000, "avg_sales": 450000, "best_products": ["생활용품", "인테리어", "플랜트"], "competition_score": 40, "potential_score": 75},
    "74": {"district": "광산구", "neighborhood": "운남동", "commercial_grade": "B", "traffic_score": 62, "weekday_traffic": 10000, "weekend_traffic": 22000, "rent_per_day": 35000, "avg_sales": 380000, "best_products": ["반려동물", "원예", "핸드메이드"], "competition_score": 32, "potential_score": 70},
    "75": {"district": "광산구", "neighborhood": "월곡동", "commercial_grade": "B+", "traffic_score": 70, "weekday_traffic": 18000, "weekend_traffic": 35000, "rent_per_day": 45000, "avg_sales": 500000, "best_products": ["패션", "뷰티", "수공예"], "competition_score": 45, "potential_score": 78},
    # 팝업존
    "76": {"district": "남구", "neighborhood": "양림동", "commercial_grade": "A", "traffic_score": 75, "weekday_traffic": 15000, "weekend_traffic": 30000, "rent_per_day": 50000, "avg_sales": 500000, "best_products": ["팝업스토어", "전시", "체험"], "competition_score": 50, "potential_score": 80},
    "77": {"district": "동구", "neighborhood": "충장동", "commercial_grade": "A+", "traffic_score": 90, "weekday_traffic": 40000, "weekend_traffic": 60000, "rent_per_day": 75000, "avg_sales": 750000, "best_products": ["팝업스토어", "브랜드체험", "이벤트"], "competition_score": 80, "potential_score": 90},
    "78": {"district": "서구", "neighborhood": "상무지구", "commercial_grade": "A", "traffic_score": 82, "weekday_traffic": 30000, "weekend_traffic": 50000, "rent_per_day": 65000, "avg_sales": 650000, "best_products": ["팝업스토어", "라이프스타일", "전시"], "competition_score": 60, "potential_score": 85},
    "79": {"district": "북구", "neighborhood": "용봉동", "commercial_grade": "A-", "traffic_score": 78, "weekday_traffic": 25000, "weekend_traffic": 42000, "rent_per_day": 55000, "avg_sales": 580000, "best_products": ["팝업스토어", "대학생타겟", "체험"], "competition_score": 55, "potential_score": 82},
    "80": {"district": "광산구", "neighborhood": "수완동", "commercial_grade": "A", "traffic_score": 80, "weekday_traffic": 28000, "weekend_traffic": 48000, "rent_per_day": 60000, "avg_sales": 620000, "best_products": ["팝업스토어", "가족체험", "브랜드"], "competition_score": 58, "potential_score": 85},
}


async def regenerate_popup_embeddings(conn: asyncpg.Connection, embeddings: OpenAIEmbeddings):
    """popup 임베딩 재생성"""
    print("\n" + "=" * 60)
    print("🏪 itdaing_popups 임베딩 재생성")
    print("=" * 60)
    
    # 기존 임베딩 삭제
    popup_uuid = await conn.fetchval(
        "SELECT uuid FROM langchain_pg_collection WHERE name = 'itdaing_popups'"
    )
    if popup_uuid:
        deleted = await conn.fetchval(
            "SELECT COUNT(*) FROM langchain_pg_embedding WHERE collection_id = $1",
            popup_uuid
        )
        await conn.execute(
            "DELETE FROM langchain_pg_embedding WHERE collection_id = $1",
            popup_uuid
        )
        print(f"기존 임베딩 {deleted or 0}개 삭제")
    
    # 모든 popup 조회
    popups = await conn.fetch("""
        SELECT 
            p.id, p.name, p.description, p.start_date, p.end_date, 
            p.operating_time, p.approval_status, p.view_count, p.favorite_count,
            zc.lat, zc.lng, zc.detailed_address, zc.label as cell_label,
            za.name as zone_name, za.id as zone_area_id
        FROM popup p
        LEFT JOIN zone_cell zc ON p.zone_cell_id = zc.id
        LEFT JOIN zone_area za ON zc.zone_area_id = za.id
        WHERE p.approval_status = 'APPROVED'
        ORDER BY p.id
    """)
    
    print(f"총 {len(popups)}개 popup 임베딩 시작...")
    
    # PGVector 연결
    connection_string = os.environ["PGVECTOR_CONNECTION"]
    vectorstore = PGVector(
        embeddings=embeddings,
        collection_name="itdaing_popups",
        connection=connection_string,
        use_jsonb=True,
    )
    
    # 배치 처리
    batch_size = 20
    total_embedded = 0
    
    for i in range(0, len(popups), batch_size):
        batch = popups[i:i + batch_size]
        texts = []
        metadatas = []
        
        for p in batch:
            # 임베딩용 텍스트
            text = f"""
{p['name']}

{p['description'] or ''}

위치: {p['detailed_address'] or '광주광역시'}
존: {p['zone_name'] or '미지정'}
기간: {p['start_date']} ~ {p['end_date']}
운영시간: {p['operating_time'] or '미정'}
""".strip()
            
            texts.append(text)
            
            # 메타데이터
            metadatas.append({
                "market_id": str(p["id"]),
                "market_name": p["name"],
                "address": p["detailed_address"] or "광주광역시",
                "lat": float(p["lat"]) if p["lat"] else None,
                "lon": float(p["lng"]) if p["lng"] else None,
                "zone_id": str(p["zone_area_id"]) if p["zone_area_id"] else None,
                "zone_name": p["zone_name"],
                "cell_label": p["cell_label"],
                "start_date": str(p["start_date"]) if p["start_date"] else None,
                "end_date": str(p["end_date"]) if p["end_date"] else None,
                "operating_hours": p["operating_time"],
                "view_count": p["view_count"],
                "favorite_count": p["favorite_count"],
                "event_type": "popup",
            })
        
        # 임베딩 추가
        vectorstore.add_texts(texts=texts, metadatas=metadatas)
        total_embedded += len(batch)
        print(f"  진행: {total_embedded}/{len(popups)}")
    
    print(f"✅ itdaing_popups 임베딩 완료: {total_embedded}개")
    return total_embedded


async def regenerate_zone_embeddings(conn: asyncpg.Connection, embeddings: OpenAIEmbeddings):
    """zone_area 임베딩 재생성"""
    print("\n" + "=" * 60)
    print("📍 itdaing_zone 임베딩 재생성")
    print("=" * 60)
    
    # 기존 임베딩 삭제
    zone_uuid = await conn.fetchval(
        "SELECT uuid FROM langchain_pg_collection WHERE name = 'itdaing_zone'"
    )
    if zone_uuid:
        deleted = await conn.fetchval(
            "SELECT COUNT(*) FROM langchain_pg_embedding WHERE collection_id = $1",
            zone_uuid
        )
        await conn.execute(
            "DELETE FROM langchain_pg_embedding WHERE collection_id = $1",
            zone_uuid
        )
        print(f"기존 임베딩 {deleted or 0}개 삭제")
    
    # 모든 zone_area 조회
    zones = await conn.fetch("""
        SELECT id, name, status, max_capacity, notice
        FROM zone_area
        ORDER BY id
    """)
    
    print(f"총 {len(zones)}개 zone_area 임베딩 시작...")
    
    # PGVector 연결
    connection_string = os.environ["PGVECTOR_CONNECTION"]
    vectorstore = PGVector(
        embeddings=embeddings,
        collection_name="itdaing_zone",
        connection=connection_string,
        use_jsonb=True,
    )
    
    texts = []
    metadatas = []
    
    for z in zones:
        zone_id = str(z["id"])
        commercial = COMMERCIAL_DATA.get(zone_id, {})
        
        # 상권 정보 텍스트
        text = f"""
## {z['name']}

### 기본 정보
- 상태: {z['status']}
- 수용 인원: {z['max_capacity']}명
- 안내: {z['notice'] or '없음'}

### 상권 정보
- 상권 등급: {commercial.get('commercial_grade', 'N/A')}
- 유동인구 점수: {commercial.get('traffic_score', 'N/A')}/100
- 경쟁도: {commercial.get('competition_score', 'N/A')}/100
- 성장 잠재력: {commercial.get('potential_score', 'N/A')}/100

### 유동인구
- 평일 평균: {commercial.get('weekday_traffic', 'N/A'):,}명
- 주말 평균: {commercial.get('weekend_traffic', 'N/A'):,}명

### 매출/비용
- 일 평균 매출: {commercial.get('avg_sales', 'N/A'):,}원
- 일 대여료: {commercial.get('rent_per_day', 'N/A'):,}원

### 추천 상품
{', '.join(commercial.get('best_products', ['정보 없음']))}
""".strip()
        
        texts.append(text)
        
        # 메타데이터
        metadatas.append({
            "type": "zone_detail",
            "zone_id": zone_id,
            "zone_name": z["name"],
            "district": commercial.get("district"),
            "neighborhood": commercial.get("neighborhood"),
            "commercial_grade": commercial.get("commercial_grade"),
            "traffic_score": commercial.get("traffic_score"),
            "weekday_traffic": commercial.get("weekday_traffic"),
            "weekend_traffic": commercial.get("weekend_traffic"),
            "rent_per_day": commercial.get("rent_per_day"),
            "avg_sales": commercial.get("avg_sales"),
            "best_products": commercial.get("best_products"),
            "competition_score": commercial.get("competition_score"),
            "potential_score": commercial.get("potential_score"),
        })
    
    # 임베딩 추가
    vectorstore.add_texts(texts=texts, metadatas=metadatas)
    
    print(f"✅ itdaing_zone 임베딩 완료: {len(zones)}개")
    return len(zones)


async def main():
    print("=" * 60)
    print("🔄 PGVector 전체 임베딩 재생성")
    print("=" * 60)
    
    conn = await asyncpg.connect(
        host=os.environ["POSTGRES_HOST"],
        database=os.environ["POSTGRES_DB"],
        user=os.environ["POSTGRES_USER"],
        password=os.environ["POSTGRES_PASSWORD"],
        port=int(os.environ.get("POSTGRES_PORT", 5432)),
    )
    
    embeddings = OpenAIEmbeddings(
        model=os.environ.get("OPENAI_EMBEDDING_MODEL", "text-embedding-3-small"),
        api_key=os.environ["OPENAI_API_KEY"],
    )
    
    try:
        popup_count = await regenerate_popup_embeddings(conn, embeddings)
        zone_count = await regenerate_zone_embeddings(conn, embeddings)
        
        print("\n" + "=" * 60)
        print("✅ 전체 임베딩 재생성 완료!")
        print(f"   - itdaing_popups: {popup_count}개")
        print(f"   - itdaing_zone: {zone_count}개")
        print("=" * 60)
        
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())

