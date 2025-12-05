#!/usr/bin/env python3
"""
메인홈 플리마켓 데이터 시드 스크립트

CSV 파일의 플리마켓 데이터를 DB에 삽입합니다.
기존 팝업을 삭제하고 새로 삽입합니다.

개선사항 (v2):
- 다양한 셀러를 할당 (zone_cell owner 또는 랜덤 셀러)
- homepage_url, sns_url, hashtags 필드 채움
- zone_cell과 popup의 일관성 유지
"""

import csv
import sys
import asyncio
from datetime import date, timedelta
import random
import json

import asyncpg

# DB 연결 정보
DB_HOST = "itdaing-db.cl4qagmger70.ap-northeast-2.rds.amazonaws.com"
DB_PORT = 5432
DB_NAME = "itdaing-db"
DB_USER = "itdaing_admin"
DB_PASSWORD = "daitdaingpassword"

# 기본값 설정
DEFAULT_CATEGORY_IDS = [1, 2, 3, 4, 5]  # 패션, 푸드, 라이프, 뷰티, 문화
PLACEHOLDER_IMAGE = "/placeholder-popup.jpg"

# 해시태그 풀 (플리마켓 관련)
HASHTAG_POOL = [
    "#플리마켓", "#광주플리마켓", "#광주맛집", "#주말나들이", "#수공예",
    "#핸드메이드", "#빈티지", "#감성소품", "#야시장", "#푸드트럭",
    "#아트마켓", "#로컬푸드", "#친환경", "#지속가능", "#업사이클",
    "#가족나들이", "#데이트코스", "#포토존", "#SNS맛집", "#인생사진",
    "#동명동", "#충장로", "#대인시장", "#송정역시장", "#첨단",
    "#상무지구", "#문화전당", "#양림동", "#용봉동", "#수완지구",
    "#광주동구", "#광주서구", "#광주남구", "#광주북구", "#광산구",
    "#청춘마켓", "#셀러마켓", "#문화행사", "#주말장터", "#야외마켓",
]

# 셀러 목록 캐시
_seller_ids_cache = []


async def get_seller_ids(conn) -> list[int]:
    """SELLER role을 가진 사용자 ID 목록 조회"""
    global _seller_ids_cache
    if not _seller_ids_cache:
        rows = await conn.fetch("""
            SELECT id FROM users 
            WHERE role = 'SELLER' 
            ORDER BY id
        """)
        _seller_ids_cache = [r['id'] for r in rows]
        print(f"📋 사용 가능한 셀러 수: {len(_seller_ids_cache)}명")
    return _seller_ids_cache


def generate_hashtags(name: str, num: int = 5) -> str:
    """마켓 이름을 기반으로 해시태그 생성"""
    # 기본 해시태그 풀에서 랜덤 선택
    selected = random.sample(HASHTAG_POOL, min(num, len(HASHTAG_POOL)))
    
    # 마켓 이름에서 키워드 추출하여 해시태그 추가
    keywords = ["플리마켓", "마켓", "장터", "야시장", "파티"]
    for kw in keywords:
        if kw in name and f"#{kw}" not in selected:
            selected.insert(0, f"#{kw}")
            break
    
    return " ".join(selected[:num])


def generate_sns_url(seller_id: int, market_name: str) -> str:
    """SNS URL 생성"""
    # 마켓 이름을 영문으로 변환 (간단히)
    slug = market_name.replace(" ", "_")[:20]
    return f"https://instagram.com/itdaing_market_{seller_id}"


def generate_homepage_url(market_name: str) -> str:
    """홈페이지 URL 생성 (이벤트브라이트 스타일)"""
    slug = market_name.replace(" ", "-")[:30]
    return f"https://itdaing.com/popup/{slug}"


async def get_or_create_zone_cell(conn, lat: float, lng: float, address: str, seller_ids: list[int]) -> tuple[int, int]:
    """
    위도/경도로 가장 가까운 zone_cell을 찾거나, 없으면 새로 생성
    반환: (zone_cell_id, owner_id)
    """
    # 가장 가까운 zone_cell 찾기
    row = await conn.fetchrow("""
        SELECT id, zone_area_id, owner_id
        FROM zone_cell 
        ORDER BY POWER(lat - $1, 2) + POWER(lng - $2, 2)
        LIMIT 1
    """, lat, lng)
    
    if row:
        return row['id'], row['owner_id']
    
    # 없으면 기본 zone_area로 생성
    zone_area_id = await conn.fetchval("SELECT id FROM zone_area LIMIT 1")
    if not zone_area_id:
        raise Exception("No zone_area found in database")
    
    # 랜덤 셀러 선택
    owner_id = random.choice(seller_ids) if seller_ids else 1
    
    cell_id = await conn.fetchval("""
        INSERT INTO zone_cell (zone_area_id, owner_id, label, detailed_address, lat, lng, status, created_at, updated_at, geometry_data)
        VALUES ($1, $2, $3, $4, $5, $6, 'APPROVED', NOW(), NOW(), $7)
        RETURNING id
    """, zone_area_id, owner_id, f"Cell-{lat:.2f}-{lng:.2f}", address, lat, lng,
        json.dumps({"type": "Point", "coordinates": [lng, lat]}))
    
    return cell_id, owner_id


async def seed_popups(csv_path: str, clear_existing: bool = True):
    """
    CSV 파일에서 팝업 데이터를 읽어 DB에 삽입
    """
    conn = await asyncpg.connect(
        host=DB_HOST,
        port=DB_PORT,
        database=DB_NAME,
        user=DB_USER,
        password=DB_PASSWORD
    )
    
    try:
        # 셀러 목록 조회
        seller_ids = await get_seller_ids(conn)
        if not seller_ids:
            print("⚠️  셀러가 없습니다. 기본 seller_id=1 사용")
            seller_ids = [1]
        
        # 기존 데이터 삭제 (옵션)
        if clear_existing:
            print("🗑️  기존 팝업 데이터 삭제 중...")
            # 관련 테이블 먼저 삭제 (외래키 제약)
            await conn.execute("DELETE FROM popup_style")
            await conn.execute("DELETE FROM popup_feature")
            await conn.execute("DELETE FROM popup_category")
            await conn.execute("DELETE FROM popup_image")
            await conn.execute("DELETE FROM popup")
            print("✅ 기존 데이터 삭제 완료")
        
        # CSV 파일 읽기
        print(f"📄 CSV 파일 읽기: {csv_path}")
        popups_data = []
        
        with open(csv_path, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                if not row.get('제목') or not row.get('위도'):
                    continue
                popups_data.append(row)
        
        print(f"📊 읽은 데이터: {len(popups_data)}개")
        
        # 팝업 삽입
        inserted_ids = []
        today = date.today()
        
        for i, data in enumerate(popups_data, 1):
            name = data['제목']
            address = data['주소']
            lat = float(data['위도'])
            lng = float(data['경도'])
            description = data['컨텐츠']
            
            # zone_cell 찾기/생성 (owner_id도 함께 반환)
            zone_cell_id, zone_owner_id = await get_or_create_zone_cell(conn, lat, lng, address, seller_ids)
            
            # 셀러 결정: zone_cell의 owner를 사용하거나, 랜덤 셀러 할당
            # 50% 확률로 zone_cell owner 사용, 50% 확률로 랜덤 셀러
            if random.random() < 0.5 and zone_owner_id in seller_ids:
                seller_id = zone_owner_id
            else:
                seller_id = random.choice(seller_ids)
            
            # 날짜 설정 (현재부터 30~90일 후까지 운영)
            start_date = today - timedelta(days=random.randint(0, 7))
            end_date = today + timedelta(days=random.randint(30, 90))
            
            # 운영 시간 랜덤 설정
            operating_times = [
                "10:00 - 18:00",
                "11:00 - 20:00",
                "12:00 - 21:00",
                "10:00 - 19:00",
                "09:00 - 18:00",
            ]
            operating_time = random.choice(operating_times)
            
            # 추가 필드 생성
            homepage_url = generate_homepage_url(name)
            sns_url = generate_sns_url(seller_id, name)
            hashtags = generate_hashtags(name)
            
            # 팝업 삽입 (모든 필드 포함)
            popup_id = await conn.fetchval("""
                INSERT INTO popup (
                    seller_id, zone_cell_id, name, description,
                    start_date, end_date, operating_time,
                    approval_status, view_count, favorite_count,
                    homepage_url, sns_url, hashtags,
                    created_at, updated_at
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, 'APPROVED', $8, $9, $10, $11, $12, NOW(), NOW())
                RETURNING id
            """, seller_id, zone_cell_id, name, description, 
                start_date, end_date, operating_time,
                random.randint(10, 500),  # view_count
                random.randint(0, 50),    # favorite_count
                homepage_url, sns_url, hashtags)
            
            inserted_ids.append(popup_id)
            
            # 썸네일 이미지 추가 (placeholder)
            await conn.execute("""
                INSERT INTO popup_image (popup_id, image_url, is_thumbnail, created_at)
                VALUES ($1, $2, true, NOW())
            """, popup_id, PLACEHOLDER_IMAGE)
            
            # 카테고리 추가 (랜덤 1~2개)
            num_categories = random.randint(1, 2)
            selected_categories = random.sample(DEFAULT_CATEGORY_IDS, num_categories)
            for cat_id in selected_categories:
                await conn.execute("""
                    INSERT INTO popup_category (popup_id, category_id, category_role)
                    VALUES ($1, $2, 'POPUP')
                    ON CONFLICT DO NOTHING
                """, popup_id, cat_id)
            
            # 스타일 추가 (랜덤 1~2개, style 테이블에서)
            style_ids = await conn.fetch("SELECT id FROM style LIMIT 10")
            if style_ids:
                num_styles = random.randint(1, 2)
                selected_styles = random.sample([s['id'] for s in style_ids], min(num_styles, len(style_ids)))
                for style_id in selected_styles:
                    await conn.execute("""
                        INSERT INTO popup_style (popup_id, style_id)
                        VALUES ($1, $2)
                        ON CONFLICT DO NOTHING
                    """, popup_id, style_id)
            
            # 피처 추가 (랜덤 1~3개, feature 테이블에서)
            feature_ids = await conn.fetch("SELECT id FROM feature LIMIT 10")
            if feature_ids:
                num_features = random.randint(1, 3)
                selected_features = random.sample([f['id'] for f in feature_ids], min(num_features, len(feature_ids)))
                for feature_id in selected_features:
                    await conn.execute("""
                        INSERT INTO popup_feature (popup_id, feature_id)
                        VALUES ($1, $2)
                        ON CONFLICT DO NOTHING
                    """, popup_id, feature_id)
            
            print(f"  [{i}/{len(popups_data)}] ✅ {name} (ID: {popup_id}, Seller: {seller_id})")
        
        print(f"\n🎉 총 {len(inserted_ids)}개의 팝업이 삽입되었습니다!")
        print(f"📌 팝업 ID 목록: {inserted_ids}")
        
        # 데이터 검증
        print("\n" + "=" * 60)
        print("📊 데이터 검증")
        print("=" * 60)
        
        # 셀러 분포 확인
        seller_dist = await conn.fetch("""
            SELECT seller_id, COUNT(*) as cnt 
            FROM popup 
            WHERE id = ANY($1)
            GROUP BY seller_id 
            ORDER BY cnt DESC
        """, inserted_ids)
        print(f"  셀러 분포: {len(seller_dist)}명의 셀러에게 분산됨")
        
        # 빈 필드 확인
        null_check = await conn.fetchrow("""
            SELECT 
                COUNT(*) FILTER (WHERE homepage_url IS NULL) as null_homepage,
                COUNT(*) FILTER (WHERE sns_url IS NULL) as null_sns,
                COUNT(*) FILTER (WHERE hashtags IS NULL) as null_hashtags
            FROM popup
            WHERE id = ANY($1)
        """, inserted_ids)
        print(f"  빈 homepage_url: {null_check['null_homepage']}")
        print(f"  빈 sns_url: {null_check['null_sns']}")
        print(f"  빈 hashtags: {null_check['null_hashtags']}")
        
        return inserted_ids
        
    finally:
        await conn.close()


async def main():
    csv_path = "/home/ubuntu/mainhome_flea.csv"
    
    if len(sys.argv) > 1:
        csv_path = sys.argv[1]
    
    print("=" * 60)
    print("🌱 메인홈 플리마켓 데이터 시드 스크립트 (v2)")
    print("=" * 60)
    
    popup_ids = await seed_popups(csv_path)
    
    print("\n" + "=" * 60)
    print("📋 homePopupConfig.js에 아래 ID를 사용하세요:")
    print("=" * 60)
    print(f"export const POPUP_WHITELIST = {popup_ids};")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
