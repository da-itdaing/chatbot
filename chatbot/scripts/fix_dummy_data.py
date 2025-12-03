#!/usr/bin/env python3
"""
더미 데이터 보강 스크립트
1. PGVector market_id를 실제 popup ID로 수정
2. 조회수/좋아요 더미값 추가
3. 리뷰 없는 팝업에 더미 리뷰 추가
"""
import asyncio
import asyncpg
import os
import random
import json
from datetime import datetime, timedelta
from dotenv import load_dotenv

load_dotenv('chatbot.env')

# 리뷰 더미 내용
REVIEW_CONTENTS = [
    "정말 좋은 마켓이었어요! 다양한 물건들이 있어서 구경하는 재미가 있었습니다. 다음에도 꼭 방문하고 싶어요.",
    "분위기가 너무 좋았어요. 가족과 함께 방문했는데 아이들도 즐거워했습니다. 음식도 맛있었어요!",
    "핸드메이드 제품들이 많아서 좋았습니다. 퀄리티도 훌륭하고 가격도 합리적이에요.",
    "주차가 좀 불편했지만 마켓 자체는 만족스러웠어요. 다양한 셀러분들이 계셔서 좋았습니다.",
    "위치가 좋아서 대중교통으로 가기 편했어요. 사진찍기 좋은 포토존도 많았습니다!",
    "음식이 정말 맛있었어요. 특히 떡볶이랑 타코야키가 최고였습니다. 또 오고 싶어요.",
    "규모가 생각보다 커서 놀랐어요. 다 둘러보는 데 2시간 정도 걸렸습니다. 볼거리 풍성!",
    "셀러분들이 친절하셔서 좋았어요. 제품 설명도 자세히 해주시고 서비스도 좋았습니다.",
    "야외라서 날씨 영향을 많이 받을 것 같지만, 방문한 날은 날씨가 좋아서 최고였어요!",
    "가격대가 다양해서 부담없이 구경할 수 있었어요. 예쁜 소품들을 많이 건졌습니다.",
    "분위기가 아기자기해서 데이트 코스로 딱이에요. 사진도 많이 찍고 좋은 추억 만들었어요.",
    "생각보다 사람이 많아서 붐볐지만, 그만큼 인기 있는 곳이란 증거겠죠. 재방문 의사 있어요!",
    "맛집이 많아서 먹느라 시간 가는 줄 몰랐어요. 배부르게 먹고 왔습니다 ㅎㅎ",
    "예술작품들이 많아서 눈이 즐거웠어요. 작가님들 작품도 구매했는데 만족합니다!",
    "아이와 함께 갔는데 체험 프로그램이 있어서 좋았어요. 아이가 너무 좋아했습니다.",
    "주말에 갔는데 사람이 너무 많아서 조금 힘들었어요. 평일에 가면 더 좋을 것 같아요.",
    "빈티지 제품들이 많아서 좋았습니다. 레트로 감성 좋아하시는 분들께 추천해요!",
    "음료도 팔고 있어서 좋았어요. 아이스커피 마시면서 구경하니까 여유로웠습니다.",
    "정기적으로 열리는 곳이라 또 방문할 예정이에요. 시즌마다 다른 분위기일 것 같아요.",
    "처음 방문했는데 너무 좋았어요! 친구들한테도 추천해줬습니다. 다들 만족했대요.",
    "광주에 이런 좋은 마켓이 있는 줄 몰랐어요. 앞으로 자주 올 것 같아요!",
    "현금만 받는 곳이 좀 있어서 아쉬웠지만, 대부분 카드결제가 가능해서 편했어요.",
    "화장실이 깨끗하게 관리되고 있어서 좋았습니다. 가족 나들이로 적극 추천해요.",
    "지역 특산품도 많이 팔아서 선물용으로 좋았어요. 부모님께 드릴 선물도 샀습니다.",
]

# 짧은 리뷰 (별점 3~4점용)
SHORT_REVIEWS = [
    "괜찮았어요",
    "그럭저럭이요",
    "나쁘지 않았습니다",
    "보통이었어요",
    "기대만큼은 아니었어요",
    "시간이 있으면 가볼 만해요",
    "특별하진 않았어요",
    "그냥 그랬어요",
]


async def fix_pgvector_market_ids():
    """PGVector의 market_id를 실제 popup ID로 수정"""
    dsn = os.getenv('PGVECTOR_CONNECTION').replace('postgresql+psycopg://', 'postgresql://')
    conn = await asyncpg.connect(dsn)
    
    print("=" * 60)
    print("🔧 PGVector market_id 수정")
    print("=" * 60)
    
    # 1. 현재 PGVector 임베딩 조회
    embeddings = await conn.fetch('''
        SELECT id, cmetadata
        FROM langchain_pg_embedding
        WHERE collection_id = (SELECT uuid FROM langchain_pg_collection WHERE name = 'itdaing_popups')
    ''')
    
    print(f"\n📊 총 임베딩 수: {len(embeddings)}개")
    
    # 2. popup 테이블에서 이름으로 ID 매핑
    popups = await conn.fetch('''
        SELECT id, name FROM popup
    ''')
    popup_name_to_id = {p['name'].strip(): p['id'] for p in popups}
    
    updated = 0
    not_found = []
    
    for emb in embeddings:
        raw_metadata = emb['cmetadata']
        if not raw_metadata:
            continue
        
        # metadata가 문자열이면 JSON 파싱
        if isinstance(raw_metadata, str):
            try:
                metadata = json.loads(raw_metadata)
            except json.JSONDecodeError:
                continue
        else:
            metadata = raw_metadata
            
        market_id = metadata.get('market_id', '')
        market_name = metadata.get('market_name', '')
        
        # REAL-XXX 형식이면 실제 ID로 변환 시도
        if market_id and (str(market_id).startswith('REAL-') or str(market_id).startswith('M0')):
            # 이름으로 실제 ID 찾기
            actual_id = popup_name_to_id.get(market_name.strip())
            
            if actual_id:
                # metadata 업데이트
                metadata['market_id'] = str(actual_id)
                
                await conn.execute('''
                    UPDATE langchain_pg_embedding
                    SET cmetadata = $1
                    WHERE id = $2
                ''', json.dumps(metadata, ensure_ascii=False), emb['id'])
                
                updated += 1
            else:
                not_found.append((market_id, market_name[:30]))
    
    print(f"✅ 업데이트된 임베딩: {updated}개")
    if not_found[:10]:
        print(f"⚠️ 매핑 실패 (상위 10개): {not_found[:10]}")
    
    await conn.close()
    return updated


async def add_view_counts():
    """조회수/좋아요 더미값 추가"""
    dsn = os.getenv('PGVECTOR_CONNECTION').replace('postgresql+psycopg://', 'postgresql://')
    conn = await asyncpg.connect(dsn)
    
    print("\n" + "=" * 60)
    print("👁️ 조회수/좋아요 더미값 추가")
    print("=" * 60)
    
    # 조회수/좋아요가 0인 팝업
    zero_stats = await conn.fetch('''
        SELECT id, name
        FROM popup
        WHERE (view_count IS NULL OR view_count = 0)
           OR (favorite_count IS NULL OR favorite_count = 0)
    ''')
    
    print(f"\n📊 조회수/좋아요 0인 팝업: {len(zero_stats)}개")
    
    updated = 0
    for popup in zero_stats:
        # 랜덤 조회수 (50~2000)
        view_count = random.randint(50, 2000)
        # 좋아요는 조회수의 5~20%
        favorite_count = random.randint(int(view_count * 0.05), int(view_count * 0.2))
        
        await conn.execute('''
            UPDATE popup
            SET view_count = $1, favorite_count = $2, updated_at = $3
            WHERE id = $4
        ''', view_count, favorite_count, datetime.now(), popup['id'])
        
        updated += 1
        if updated % 20 == 0:
            print(f"  ... {updated}개 업데이트 완료")
    
    print(f"✅ 조회수/좋아요 추가 완료: {updated}개")
    
    await conn.close()
    return updated


async def add_dummy_reviews():
    """리뷰 없는 팝업에 더미 리뷰 추가"""
    dsn = os.getenv('PGVECTOR_CONNECTION').replace('postgresql+psycopg://', 'postgresql://')
    conn = await asyncpg.connect(dsn)
    
    print("\n" + "=" * 60)
    print("💬 더미 리뷰 추가")
    print("=" * 60)
    
    # 리뷰 없는 팝업
    no_review_popups = await conn.fetch('''
        SELECT id, name
        FROM popup
        WHERE NOT EXISTS (SELECT 1 FROM review r WHERE r.popup_id = popup.id)
    ''')
    
    print(f"\n📊 리뷰 없는 팝업: {len(no_review_popups)}개")
    
    # consumer 계정 조회 (리뷰 작성자용)
    consumers = await conn.fetch('''
        SELECT id FROM users WHERE role = 'CONSUMER' LIMIT 50
    ''')
    
    if not consumers:
        print("⚠️ CONSUMER 계정이 없습니다. 더미 consumer를 생성해야 합니다.")
        await conn.close()
        return 0
    
    consumer_ids = [c['id'] for c in consumers]
    print(f"  사용 가능한 Consumer: {len(consumer_ids)}명")
    
    total_reviews = 0
    for idx, popup in enumerate(no_review_popups):
        # 각 팝업당 3~8개 리뷰 (consumer 수를 초과하지 않게)
        num_reviews = min(random.randint(3, 8), len(consumer_ids))
        
        # 중복 방지: 랜덤하게 consumer 선택 (중복 없이)
        selected_consumers = random.sample(consumer_ids, num_reviews)
        
        for consumer_id in selected_consumers:
            # 이미 존재하는 리뷰인지 확인
            existing = await conn.fetchval('''
                SELECT 1 FROM review WHERE consumer_id = $1 AND popup_id = $2
            ''', consumer_id, popup['id'])
            
            if existing:
                continue  # 이미 리뷰 있으면 스킵
            
            # 랜덤 별점 (3~5점 위주, 가끔 1~2점)
            rating = random.choices(
                [1, 2, 3, 4, 5],
                weights=[2, 5, 15, 40, 38]  # 4~5점이 많게
            )[0]
            
            # 별점에 따른 리뷰 내용
            if rating >= 4:
                content = random.choice(REVIEW_CONTENTS)
            else:
                content = random.choice(SHORT_REVIEWS)
            
            # 생성일은 팝업 시작일 이후 랜덤
            days_ago = random.randint(1, 30)
            created_at = datetime.now() - timedelta(days=days_ago)
            
            try:
                await conn.execute('''
                    INSERT INTO review (consumer_id, popup_id, rating, content, created_at)
                    VALUES ($1, $2, $3, $4, $5)
                ''', consumer_id, popup['id'], rating, content, created_at)
                total_reviews += 1
            except Exception as e:
                # 중복 에러 무시
                pass
        
        if (idx + 1) % 20 == 0:
            print(f"  ... {idx + 1}/{len(no_review_popups)} 팝업 처리 완료")
    
    print(f"✅ 더미 리뷰 추가 완료: {total_reviews}개")
    
    await conn.close()
    return total_reviews


async def verify_data():
    """데이터 검증"""
    dsn = os.getenv('PGVECTOR_CONNECTION').replace('postgresql+psycopg://', 'postgresql://')
    conn = await asyncpg.connect(dsn)
    
    print("\n" + "=" * 60)
    print("✅ 데이터 검증")
    print("=" * 60)
    
    # 1. PGVector market_id 확인
    embeddings = await conn.fetch('''
        SELECT cmetadata->>'market_id' as market_id
        FROM langchain_pg_embedding
        WHERE collection_id = (SELECT uuid FROM langchain_pg_collection WHERE name = 'itdaing_popups')
        LIMIT 10
    ''')
    print(f"\n🔗 PGVector market_id 샘플: {[e['market_id'] for e in embeddings]}")
    
    # 2. 조회수/좋아요 통계
    stats = await conn.fetch('''
        SELECT 
            COUNT(*) as total,
            AVG(view_count) as avg_views,
            AVG(favorite_count) as avg_favs,
            SUM(CASE WHEN view_count = 0 THEN 1 ELSE 0 END) as zero_views
        FROM popup
    ''')
    s = stats[0]
    print(f"\n📊 팝업 통계:")
    print(f"  전체: {s['total']}개")
    print(f"  평균 조회수: {s['avg_views']:.1f}")
    print(f"  평균 좋아요: {s['avg_favs']:.1f}")
    print(f"  조회수 0: {s['zero_views']}개")
    
    # 3. 리뷰 통계
    review_stats = await conn.fetch('''
        SELECT 
            COUNT(*) as total_reviews,
            AVG(rating) as avg_rating
        FROM review
    ''')
    r = review_stats[0]
    print(f"\n💬 리뷰 통계:")
    print(f"  전체 리뷰: {r['total_reviews']}개")
    print(f"  평균 별점: {r['avg_rating']:.2f}")
    
    # 4. 리뷰 없는 팝업
    no_review = await conn.fetchval('''
        SELECT COUNT(*) FROM popup p
        WHERE NOT EXISTS (SELECT 1 FROM review r WHERE r.popup_id = p.id)
    ''')
    print(f"  리뷰 없는 팝업: {no_review}개")
    
    await conn.close()


async def main():
    # 1. PGVector market_id 수정
    await fix_pgvector_market_ids()
    
    # 2. 조회수/좋아요 추가
    await add_view_counts()
    
    # 3. 더미 리뷰 추가
    await add_dummy_reviews()
    
    # 4. 검증
    await verify_data()
    
    print("\n" + "=" * 60)
    print("🎉 모든 더미 데이터 보강 완료!")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())

