#!/usr/bin/env python3
"""
판매자용 챗봇 데이터 임베딩 스크립트
- 광주 상권 정보를 PGVector에 임베딩
- 컬렉션: itdaing_zone (소비자용 itdaing_popups와 분리)
"""
import asyncio
import asyncpg
import os
import json
import uuid
from datetime import datetime
from dotenv import load_dotenv
from langchain_openai import OpenAIEmbeddings
from langchain_core.documents import Document

load_dotenv('chatbot.env')

# 판매자용 컬렉션 (소비자용과 분리)
SELLER_COLLECTION = os.getenv('PGVECTOR_ZONE_COLLECTION', 'itdaing_zone')

async def create_collection_if_not_exists(conn, collection_name: str) -> str:
    """컬렉션이 없으면 생성하고 UUID 반환"""
    existing = await conn.fetchval('''
        SELECT uuid FROM langchain_pg_collection WHERE name = $1
    ''', collection_name)
    
    if existing:
        print(f"✅ 컬렉션 '{collection_name}' 존재: {existing}")
        return str(existing)
    
    collection_uuid = str(uuid.uuid4())
    await conn.execute('''
        INSERT INTO langchain_pg_collection (uuid, name, cmetadata)
        VALUES ($1, $2, $3)
    ''', collection_uuid, collection_name, '{}')
    
    print(f"✅ 컬렉션 '{collection_name}' 생성: {collection_uuid}")
    return collection_uuid


async def clear_collection(conn, collection_uuid: str):
    """기존 임베딩 삭제"""
    deleted = await conn.execute('''
        DELETE FROM langchain_pg_embedding WHERE collection_id = $1
    ''', collection_uuid)
    print(f"🗑️ 기존 임베딩 삭제: {deleted}")


def create_zone_documents(zone_commercial_data: dict, gwangju_data: dict) -> list[Document]:
    """존 상권 정보를 Document로 변환"""
    documents = []
    
    # 1. 존별 상세 정보
    for zone_id, zone_info in zone_commercial_data.get('zones', {}).items():
        # 풍부한 텍스트 생성
        content = f"""
## {zone_info['name']}

### 기본 정보
- 위치: 광주광역시 {zone_info['district']} {zone_info['neighborhood']}
- 상권 등급: {zone_info['commercial_grade']}
- 유동인구 점수: {zone_info['traffic_score']}/100
- 경쟁도: {zone_info['competition_score']}/100
- 성장 잠재력: {zone_info['potential_score']}/100

### 유동인구
- 평일 평균: {zone_info['weekday_traffic']:,}명
- 주말 평균: {zone_info['weekend_traffic']:,}명
- 피크 시간대: {', '.join(zone_info['peak_hours'])}

### 연령대 분포
{', '.join([f'{k}: {v}%' for k, v in zone_info['age_distribution'].items()])}

### 추천 상품
{', '.join(zone_info['best_products'])}

### 예상 수익
- 일 평균 매출: {zone_info['avg_sales_per_day']:,}원
- 일 임대료: {zone_info['rent_per_day']:,}원

### 편의시설
- 주차: {zone_info['parking']}
- 화장실: {zone_info['restroom']}
- 전기: {zone_info['electricity']}

### 셀러 팁
{chr(10).join(['- ' + tip for tip in zone_info['tips']])}

### 셀러 리뷰
- 평점: {zone_info['reviews']['avg_rating']}/5.0 ({zone_info['reviews']['count']}개)
- 주요 평가: {', '.join(zone_info['reviews']['highlights'])}
""".strip()
        
        metadata = {
            "zone_id": zone_id,
            "zone_name": zone_info['name'],
            "district": zone_info['district'],
            "neighborhood": zone_info['neighborhood'],
            "commercial_grade": zone_info['commercial_grade'],
            "traffic_score": zone_info['traffic_score'],
            "competition_score": zone_info['competition_score'],
            "potential_score": zone_info['potential_score'],
            "weekday_traffic": zone_info['weekday_traffic'],
            "weekend_traffic": zone_info['weekend_traffic'],
            "avg_sales": zone_info['avg_sales_per_day'],
            "rent_per_day": zone_info['rent_per_day'],
            "best_products": zone_info['best_products'],
            "type": "zone_detail"
        }
        
        documents.append(Document(page_content=content, metadata=metadata))
    
    # 2. 동별 상권 정보
    for district_name, district_info in gwangju_data.get('districts', {}).items():
        for neighborhood_name, neighborhood_info in district_info.get('neighborhoods', {}).items():
            commercial = neighborhood_info.get('commercial_info', {})
            floating = neighborhood_info.get('floating_population', {})
            age = neighborhood_info.get('age_distribution', {})
            
            content = f"""
## 광주 {district_name} {neighborhood_name} 상권 정보

### 지역 특성
- 인구: {neighborhood_info.get('population', 0):,}명
- 특징: {', '.join(neighborhood_info.get('characteristics', []))}

### 유동인구
- 평일 평균: {floating.get('weekday_avg', 0):,}명
- 주말 평균: {floating.get('weekend_avg', 0):,}명
- 피크 시간대: {', '.join(floating.get('peak_hours', []))}

### 연령대 분포
{', '.join([f'{k}: {v}%' for k, v in age.items()])}

### 상권 정보
- 주요 업종: {', '.join(commercial.get('main_industries', []))}
- 평당 임대료: {commercial.get('avg_rent_per_pyeong', 0):,}원
- 공실률: {commercial.get('vacancy_rate', 0)}%
- 경쟁 수준: {commercial.get('competition_level', '정보없음')}
- 추천 업종: {', '.join(commercial.get('recommended_business', []))}

### 주변 시설
{', '.join(neighborhood_info.get('nearby_facilities', ['정보 없음']))}

### 주요 행사
{', '.join(neighborhood_info.get('events', ['정기 행사 없음']))}
""".strip()
            
            metadata = {
                "district": district_name,
                "neighborhood": neighborhood_name,
                "region_id": district_info.get('region_id'),
                "population": neighborhood_info.get('population', 0),
                "weekday_traffic": floating.get('weekday_avg', 0),
                "weekend_traffic": floating.get('weekend_avg', 0),
                "avg_rent": commercial.get('avg_rent_per_pyeong', 0),
                "vacancy_rate": commercial.get('vacancy_rate', 0),
                "competition_level": commercial.get('competition_level', ''),
                "main_industries": commercial.get('main_industries', []),
                "type": "neighborhood_commercial"
            }
            
            documents.append(Document(page_content=content, metadata=metadata))
    
    # 3. 계절별 트렌드
    for season, season_info in gwangju_data.get('seasonal_trends', {}).items():
        content = f"""
## {season} 플리마켓 운영 가이드

### 주요 행사
{', '.join(season_info.get('peak_events', []))}

### 추천 상품
{', '.join(season_info.get('recommended_products', []))}

### 고객 특성
{season_info.get('customer_behavior', '')}
""".strip()
        
        metadata = {
            "season": season,
            "type": "seasonal_guide"
        }
        
        documents.append(Document(page_content=content, metadata=metadata))
    
    # 4. 타깃 고객 가이드
    for target, target_info in gwangju_data.get('target_customer_guide', {}).items():
        content = f"""
## {target} 타깃 판매 전략

### 고객 특성
{', '.join(target_info.get('characteristics', []))}

### 선호 지역
{', '.join(target_info.get('preferred_locations', []))}

### 소비 패턴
{target_info.get('spending_pattern', '')}

### 피크 시간대
{', '.join(target_info.get('peak_times', []))}
""".strip()
        
        metadata = {
            "target_age": target,
            "type": "target_guide"
        }
        
        documents.append(Document(page_content=content, metadata=metadata))
    
    # 5. 플리마켓 추천 장소
    for place in gwangju_data.get('flea_market_guide', {}).get('recommended_locations', []):
        content = f"""
## {place['name']} 플리마켓

### 기본 정보
- 위치: 광주 {place['district']}
- 유형: {place['type']}
- 타깃 고객: {place['target']}

### 비용
- 예상 임대료: {place['rent_estimate']}
- 인기 요일: {', '.join(place['peak_days'])}

### 운영 팁
{place['tips']}
""".strip()
        
        metadata = {
            "place_name": place['name'],
            "district": place['district'],
            "market_type": place['type'],
            "type": "flea_market_location"
        }
        
        documents.append(Document(page_content=content, metadata=metadata))
    
    return documents


async def embed_documents(conn, documents: list[Document], collection_uuid: str, embeddings: OpenAIEmbeddings):
    """문서들을 임베딩하여 DB에 저장"""
    print(f"\n📊 총 {len(documents)}개 문서 임베딩 시작...")
    
    batch_size = 20
    total_embedded = 0
    
    for i in range(0, len(documents), batch_size):
        batch = documents[i:i+batch_size]
        texts = [doc.page_content for doc in batch]
        
        # 임베딩 생성
        vectors = await embeddings.aembed_documents(texts)
        
        # DB에 저장
        for j, (doc, vector) in enumerate(zip(batch, vectors)):
            doc_id = str(uuid.uuid4())
            
            # metadata를 JSON 문자열로 변환
            metadata_json = json.dumps(doc.metadata, ensure_ascii=False)
            
            # vector를 문자열로 변환 (asyncpg용)
            vector_str = str(vector)
            
            await conn.execute('''
                INSERT INTO langchain_pg_embedding (id, collection_id, embedding, document, cmetadata)
                VALUES ($1, $2, $3, $4, $5)
            ''', doc_id, collection_uuid, vector_str, doc.page_content, metadata_json)
            
            total_embedded += 1
        
        print(f"  ... {total_embedded}/{len(documents)} 완료")
    
    print(f"✅ 임베딩 완료: {total_embedded}개")
    return total_embedded


async def main():
    print("=" * 60)
    print("🏪 판매자용 상권 데이터 임베딩 시작")
    print("=" * 60)
    
    # 데이터 파일 로드
    with open('/home/ubuntu/chatbot/data/zone_commercial_info.json', 'r', encoding='utf-8') as f:
        zone_data = json.load(f)
    
    with open('/home/ubuntu/chatbot/data/gwangju_commercial_data.json', 'r', encoding='utf-8') as f:
        gwangju_data = json.load(f)
    
    print(f"📂 존 상권 데이터: {len(zone_data.get('zones', {}))}개 존")
    print(f"📂 광주 상권 데이터: {len(gwangju_data.get('districts', {}))}개 구")
    
    # Document 생성
    documents = create_zone_documents(zone_data, gwangju_data)
    print(f"📄 생성된 Document: {len(documents)}개")
    
    # DB 연결
    dsn = os.getenv('PGVECTOR_CONNECTION').replace('postgresql+psycopg://', 'postgresql://')
    conn = await asyncpg.connect(dsn)
    
    # 컬렉션 생성/확인
    collection_uuid = await create_collection_if_not_exists(conn, SELLER_COLLECTION)
    
    # 기존 데이터 삭제
    await clear_collection(conn, collection_uuid)
    
    # 임베딩 생성
    embeddings = OpenAIEmbeddings(
        model=os.getenv('OPENAI_EMBEDDING_MODEL', 'text-embedding-3-small')
    )
    
    # 임베딩 및 저장
    total = await embed_documents(conn, documents, collection_uuid, embeddings)
    
    await conn.close()
    
    print("\n" + "=" * 60)
    print(f"🎉 판매자용 데이터 임베딩 완료!")
    print(f"   컬렉션: {SELLER_COLLECTION}")
    print(f"   총 문서: {total}개")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())

