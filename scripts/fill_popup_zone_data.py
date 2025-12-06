#!/usr/bin/env python3
"""
팝업 및 존 데이터 보강 스크립트

새로 seed된 popup 데이터에 대해:
1. seller_id를 다양한 셀러로 분배
2. zone_cell의 owner_id를 적절한 셀러로 분배
3. popup의 hashtags, sns_url 등 선택적 필드 채우기
"""

import asyncio
import random
from typing import List, Dict, Any

import asyncpg

# DB 연결 정보
DB_HOST = "itdaing-db.cl4qagmger70.ap-northeast-2.rds.amazonaws.com"
DB_PORT = 5432
DB_NAME = "itdaing-db"
DB_USER = "itdaing_admin"
DB_PASSWORD = "daitdaingpassword"

# 해시태그 풀 (마켓 컨셉별)
HASHTAG_POOLS = {
    "예술": ["#예술마켓", "#아트플리", "#작가마켓", "#핸드메이드", "#광주예술"],
    "야시장": ["#야시장", "#야간마켓", "#푸드마켓", "#거리음식", "#낭만야시장"],
    "청춘": ["#청춘마켓", "#MZ플리", "#감성마켓", "#스트리트", "#힙스터"],
    "수공예": ["#수공예", "#핸드크래프트", "#작가소품", "#원데이클래스", "#크라프트"],
    "가족": ["#가족나들이", "#키즈마켓", "#맘스마켓", "#패밀리", "#주말나들이"],
    "로컬": ["#로컬마켓", "#광주플리", "#지역상생", "#소상공인", "#로컬브랜드"],
    "친환경": ["#친환경", "#에코마켓", "#제로웨이스트", "#업사이클", "#지속가능"],
    "빈티지": ["#빈티지", "#레트로", "#중고마켓", "#구제", "#빈티지소품"],
    "음악": ["#버스킹", "#라이브", "#음악마켓", "#콘서트", "#인디음악"],
    "문화": ["#문화마켓", "#축제", "#페스티벌", "#복합문화", "#아트페어"],
    "default": ["#광주플리마켓", "#플리마켓", "#주말마켓", "#핫플", "#광주핫플"],
}

# SNS URL 템플릿
SNS_TEMPLATES = [
    "https://instagram.com/gwangju_market",
    "https://instagram.com/itdaing_flea",
    "https://blog.naver.com/gwangju_market",
    "https://www.instagram.com/gwangju_popup",
    None,  # 일부는 SNS 없음
]


def extract_keywords_from_name(name: str) -> List[str]:
    """마켓 이름에서 키워드 추출"""
    keywords = []
    keyword_map = {
        "예술": ["예술", "아트", "ART"],
        "야시장": ["야시장", "달밤", "야간"],
        "청춘": ["청춘", "청년"],
        "수공예": ["수공예", "핸드메이드", "공예"],
        "가족": ["맘스", "키즈", "패밀리", "가족"],
        "로컬": ["로컬", "상생", "소상공인", "지역"],
        "친환경": ["에코", "친환경", "지구", "푸릇"],
        "빈티지": ["빈티지", "레트로", "1923", "송정역"],
        "음악": ["음악", "콘서트", "버스킹", "난장"],
        "문화": ["문화", "컬처", "누리", "페스티벌"],
    }
    
    for category, terms in keyword_map.items():
        for term in terms:
            if term.lower() in name.lower():
                keywords.append(category)
                break
    
    return keywords if keywords else ["default"]


def generate_hashtags(name: str, max_tags: int = 5) -> str:
    """마켓 이름 기반 해시태그 생성"""
    keywords = extract_keywords_from_name(name)
    all_tags = []
    
    for kw in keywords:
        pool = HASHTAG_POOLS.get(kw, HASHTAG_POOLS["default"])
        all_tags.extend(pool)
    
    # 중복 제거 및 랜덤 선택
    unique_tags = list(set(all_tags))
    selected = random.sample(unique_tags, min(max_tags, len(unique_tags)))
    
    return ",".join(selected)


async def get_seller_ids(conn: asyncpg.Connection) -> List[int]:
    """seller_profile이 있는 user ID 목록 조회"""
    rows = await conn.fetch("""
        SELECT sp.user_id 
        FROM seller_profile sp
        JOIN users u ON sp.user_id = u.id
        WHERE u.role = 'SELLER' AND u.status = 'ACTIVE'
        ORDER BY sp.user_id
    """)
    return [r['user_id'] for r in rows]


async def update_popups(conn: asyncpg.Connection, seller_ids: List[int]):
    """popup 테이블 업데이트"""
    print("\n📦 팝업 데이터 업데이트 중...")
    
    # 최근 생성된 popup 조회 (오늘 생성된 것들)
    popups = await conn.fetch("""
        SELECT id, name, seller_id, hashtags, sns_url
        FROM popup
        WHERE hashtags IS NULL OR hashtags = ''
        ORDER BY id
    """)
    
    print(f"  📊 업데이트 대상 팝업: {len(popups)}개")
    
    for i, popup in enumerate(popups):
        popup_id = popup['id']
        name = popup['name']
        
        # 셀러 분배 (라운드 로빈)
        new_seller_id = seller_ids[i % len(seller_ids)]
        
        # 해시태그 생성
        hashtags = generate_hashtags(name)
        
        # SNS URL (랜덤, 70% 확률로 있음)
        sns_url = random.choice(SNS_TEMPLATES) if random.random() < 0.7 else None
        
        # 업데이트
        await conn.execute("""
            UPDATE popup 
            SET seller_id = $1, hashtags = $2, sns_url = $3, updated_at = NOW()
            WHERE id = $4
        """, new_seller_id, hashtags, sns_url, popup_id)
        
        print(f"  [{i+1}/{len(popups)}] ✅ {name[:30]}... → seller_id={new_seller_id}")
    
    return len(popups)


async def update_zone_cells(conn: asyncpg.Connection, seller_ids: List[int]):
    """zone_cell 테이블의 owner_id 업데이트"""
    print("\n📍 존 셀 데이터 업데이트 중...")
    
    # owner_id가 1인 zone_cell 조회 (새로 생성된 것들)
    cells = await conn.fetch("""
        SELECT id, label, owner_id, detailed_address
        FROM zone_cell
        WHERE owner_id = 1
        ORDER BY id
    """)
    
    print(f"  📊 업데이트 대상 존 셀: {len(cells)}개")
    
    for i, cell in enumerate(cells):
        cell_id = cell['id']
        label = cell['label'] or f"Cell-{cell_id}"
        
        # 셀러 분배 (라운드 로빈)
        new_owner_id = seller_ids[i % len(seller_ids)]
        
        await conn.execute("""
            UPDATE zone_cell 
            SET owner_id = $1, updated_at = NOW()
            WHERE id = $2
        """, new_owner_id, cell_id)
        
        print(f"  [{i+1}/{len(cells)}] ✅ {label} → owner_id={new_owner_id}")
    
    return len(cells)


async def verify_data(conn: asyncpg.Connection):
    """데이터 검증"""
    print("\n🔍 데이터 검증 중...")
    
    # popup 검증
    popup_stats = await conn.fetchrow("""
        SELECT 
            COUNT(*) as total,
            COUNT(hashtags) as with_hashtags,
            COUNT(sns_url) as with_sns,
            COUNT(DISTINCT seller_id) as unique_sellers
        FROM popup
    """)
    
    print(f"\n  📦 popup 통계:")
    print(f"     총 개수: {popup_stats['total']}")
    print(f"     해시태그 있음: {popup_stats['with_hashtags']}")
    print(f"     SNS URL 있음: {popup_stats['with_sns']}")
    print(f"     고유 셀러 수: {popup_stats['unique_sellers']}")
    
    # zone_cell 검증
    cell_stats = await conn.fetchrow("""
        SELECT 
            COUNT(*) as total,
            COUNT(DISTINCT owner_id) as unique_owners,
            COUNT(CASE WHEN owner_id = 1 THEN 1 END) as default_owner
        FROM zone_cell
    """)
    
    print(f"\n  📍 zone_cell 통계:")
    print(f"     총 개수: {cell_stats['total']}")
    print(f"     고유 오너 수: {cell_stats['unique_owners']}")
    print(f"     기본 오너(1) 수: {cell_stats['default_owner']}")
    
    # 샘플 출력
    print("\n  📋 popup 샘플 (최근 5개):")
    samples = await conn.fetch("""
        SELECT id, name, seller_id, hashtags, sns_url
        FROM popup
        ORDER BY id DESC
        LIMIT 5
    """)
    for s in samples:
        print(f"     ID:{s['id']} | seller:{s['seller_id']} | {s['hashtags'][:40] if s['hashtags'] else 'N/A'}...")


async def main():
    print("=" * 60)
    print("🔧 팝업 및 존 데이터 보강 스크립트")
    print("=" * 60)
    
    conn = await asyncpg.connect(
        host=DB_HOST, port=DB_PORT, database=DB_NAME, user=DB_USER, password=DB_PASSWORD
    )
    
    try:
        # 셀러 목록 조회
        seller_ids = await get_seller_ids(conn)
        print(f"\n👥 사용 가능한 셀러: {len(seller_ids)}명")
        print(f"   ID: {seller_ids[:10]}{'...' if len(seller_ids) > 10 else ''}")
        
        if not seller_ids:
            print("❌ 사용 가능한 셀러가 없습니다!")
            return
        
        # 팝업 업데이트
        popup_count = await update_popups(conn, seller_ids)
        
        # 존 셀 업데이트
        cell_count = await update_zone_cells(conn, seller_ids)
        
        # 검증
        await verify_data(conn)
        
        print("\n" + "=" * 60)
        print(f"✅ 완료! popup: {popup_count}개, zone_cell: {cell_count}개 업데이트됨")
        print("=" * 60)
        
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())

