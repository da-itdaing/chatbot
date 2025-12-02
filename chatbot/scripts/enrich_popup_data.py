#!/usr/bin/env python3
"""
팝업 데이터 보강 스크립트
- 설명 50자 미만인 팝업에 풍부한 설명 추가
- 이미지 없는 팝업에 기본 이미지 URL 추가
- 연습용 존 삭제 또는 데이터 정리
"""
import asyncio
import asyncpg
import os
import random
from datetime import datetime
from dotenv import load_dotenv

load_dotenv('chatbot.env')

# 팝업 유형별 상세 설명 템플릿
POPUP_DESCRIPTIONS = {
    "플리마켓": [
        """다양한 빈티지 아이템과 핸드메이드 소품들을 만나볼 수 있는 플리마켓입니다.
의류, 액세서리, 인테리어 소품, 그릇, 책 등 다채로운 상품들이 준비되어 있습니다.
판매자들이 직접 만든 수제 작품부터 상태 좋은 중고 물품까지, 보물찾기 하듯 특별한 아이템을 발견해보세요.
푸드트럭도 함께 운영되어 간단한 먹거리도 즐길 수 있습니다.""",
        """감성 가득한 플리마켓에서 나만의 특별한 아이템을 찾아보세요!
셀러들이 정성껏 준비한 빈티지 의류, 수제 악세서리, 친환경 생활용품 등이 여러분을 기다립니다.
매주 다른 셀러들이 참여하여 항상 새로운 아이템을 만나실 수 있어요.
주차장 완비, 반려동물 동반 가능합니다.""",
        """지역 크리에이터와 작가들이 모인 특별한 플리마켓!
수공예 악세서리, 일러스트 엽서, 캔들, 비누, 드라이플라워 등 정성 가득한 핸드메이드 제품들을 만나보세요.
작가님과 직접 소통하며 맞춤 제작도 가능합니다.
무료 주차, 카드결제 가능."""
    ],
    "야시장": [
        """화려한 조명 아래 펼쳐지는 활기찬 야시장!
전국 각지의 맛집들이 모여 다양한 길거리 음식을 선보입니다.
떡볶이, 순대, 타코야키, 꼬치구이, 붕어빵 등 먹거리가 가득합니다.
버스킹 공연과 함께 즐거운 밤을 보내세요.""",
        """매주 금~일요일 저녁 5시부터 열리는 푸드마켓입니다.
신선한 재료로 만든 수제 음식들을 합리적인 가격에 맛보실 수 있어요.
파전, 순대국밥, 닭강정, 회, 곱창 등 다양한 메뉴가 준비되어 있습니다.
가족, 연인, 친구와 함께 편하게 방문해주세요."""
    ],
    "문화축제": [
        """지역 주민과 함께하는 문화예술 축제입니다.
버스킹 공연, 전통 문화 체험, 플리마켓, 먹거리 장터가 한데 어우러져
온 가족이 즐길 수 있는 풍성한 프로그램이 마련되어 있습니다.
무료 입장이며 사전 예약 없이 자유롭게 참여하실 수 있습니다.""",
        """예술과 문화가 함께하는 특별한 축제!
지역 예술가들의 전시, 라이브 음악 공연, 워크숍 등 다채로운 프로그램이 준비되어 있습니다.
어린이를 위한 체험 부스도 운영됩니다.
주차 공간이 협소하니 대중교통을 이용해 주세요."""
    ],
    "아트마켓": [
        """예술가와 관람객이 직접 만나는 아트마켓입니다.
회화, 일러스트, 사진, 도예, 공예품 등 다양한 장르의 작품들을 합리적인 가격에 구매할 수 있습니다.
작가님들과 직접 대화하며 작품의 스토리를 들어보세요.
소규모 전시회와 아티스트 토크도 진행됩니다.""",
        """창작자들의 열정이 가득한 아트마켓!
캔버스 작품, 일러스트 프린트, 수제 도자기, 유리공예, 금속공예 등
다양한 분야의 예술 작품들을 만나볼 수 있습니다.
커피와 디저트를 즐기며 여유로운 시간을 보내세요."""
    ],
    "푸드페스타": [
        """광주의 맛집들이 한자리에 모인 푸드 페스티벌!
전통 한식부터 퓨전 요리, 디저트까지 다양한 음식을 맛볼 수 있습니다.
유명 셰프의 쿠킹쇼와 시식 이벤트도 진행됩니다.
가족 나들이 장소로 추천드립니다.""",
        """지역 맛집들이 총출동한 미식 축제!
국밥, 떡갈비, 육회비빔밥 등 광주 대표 음식부터
트렌디한 퓨전 요리까지 한 자리에서 즐기실 수 있습니다.
인스타그램 인증샷 이벤트도 참여해 보세요."""
    ],
    "주말시장": [
        """매주 주말 열리는 활기찬 마켓!
신선한 농산물부터 핸드메이드 제품, 빈티지 아이템까지
다양한 상품들을 합리적인 가격에 만나보실 수 있습니다.
라이브 음악 공연과 함께 즐거운 주말 오후를 보내세요.""",
        """주말 나들이 코스로 딱 좋은 감성 마켓입니다.
로컬 푸드, 수제 빵, 유기농 채소, 꽃, 핸드메이드 소품 등
정성이 담긴 상품들이 가득합니다.
반려동물과 함께 방문 가능합니다."""
    ],
    "거리공연 페스타": [
        """거리 위의 작은 무대, 버스킹 페스티벌!
인디밴드, 싱어송라이터, 마술사, 댄서 등 다양한 장르의 공연을 무료로 관람하실 수 있습니다.
관객 참여 이벤트와 함께 특별한 추억을 만들어보세요.
간단한 먹거리와 음료도 판매합니다.""",
        """음악과 예술이 넘치는 거리공연 축제!
어쿠스틱 공연부터 힙합, 재즈, 클래식까지 다양한 장르의 라이브 공연이 펼쳐집니다.
신진 아티스트 발굴 오디션도 함께 진행됩니다.
포토존에서 인증샷도 남겨보세요."""
    ],
    "빈티지마켓": [
        """레트로 감성 가득한 빈티지 마켓!
70~90년대 복고풍 아이템부터 앤틱 가구, 빈티지 의류, LP판까지
시간 여행을 떠나는 듯한 특별한 경험을 선사합니다.
사진 찍기 좋은 포토존도 마련되어 있어요.""",
        """오래된 것들의 새로운 가치를 발견하는 빈티지 마켓입니다.
상태 좋은 구제 의류, 액세서리, 소품, 가구 등을 만나보실 수 있습니다.
친환경 소비, 지속가능한 패션에 관심 있으신 분들께 추천드려요.
현금/카드 모두 가능합니다."""
    ]
}

# 기본 이미지 URL (S3 또는 외부 이미지)
DEFAULT_IMAGES = [
    "https://images.unsplash.com/photo-1533900298318-6b8da08a523e?w=800",  # 플리마켓
    "https://images.unsplash.com/photo-1555939594-58d7cb561ad1?w=800",  # 야시장
    "https://images.unsplash.com/photo-1492684223066-81342ee5ff30?w=800",  # 축제
    "https://images.unsplash.com/photo-1501281668745-f7f57925c3b4?w=800",  # 마켓
    "https://images.unsplash.com/photo-1556909114-f6e7ad7d3136?w=800",  # 푸드
]


def get_popup_type(name: str) -> str:
    """팝업 이름에서 유형 추출"""
    for popup_type in POPUP_DESCRIPTIONS.keys():
        if popup_type in name:
            return popup_type
    return "플리마켓"  # 기본값


def get_description(name: str) -> str:
    """팝업 유형에 맞는 설명 생성"""
    popup_type = get_popup_type(name)
    descriptions = POPUP_DESCRIPTIONS.get(popup_type, POPUP_DESCRIPTIONS["플리마켓"])
    return random.choice(descriptions)


async def enrich_popups():
    dsn = os.getenv('PGVECTOR_CONNECTION').replace('postgresql+psycopg://', 'postgresql://')
    conn = await asyncpg.connect(dsn)
    
    print("=" * 60)
    print("📝 팝업 데이터 보강 시작")
    print("=" * 60)
    
    # 1. 설명이 50자 미만인 팝업 업데이트
    short_desc_popups = await conn.fetch('''
        SELECT id, name, description
        FROM popup
        WHERE description IS NULL OR LENGTH(description) < 80
    ''')
    
    print(f"\n📄 설명 부족 팝업: {len(short_desc_popups)}개")
    
    updated_count = 0
    for popup in short_desc_popups:
        new_desc = get_description(popup['name'])
        
        await conn.execute('''
            UPDATE popup
            SET description = $1, updated_at = $2
            WHERE id = $3
        ''', new_desc, datetime.now(), popup['id'])
        
        updated_count += 1
        if updated_count % 20 == 0:
            print(f"  ... {updated_count}개 업데이트 완료")
    
    print(f"✅ 설명 업데이트 완료: {updated_count}개")
    
    # 2. 이미지 없는 팝업에 기본 이미지 추가
    no_image_popups = await conn.fetch('''
        SELECT p.id, p.name
        FROM popup p
        WHERE NOT EXISTS (SELECT 1 FROM popup_image pi WHERE pi.popup_id = p.id)
    ''')
    
    print(f"\n🖼️ 이미지 없는 팝업: {len(no_image_popups)}개")
    
    image_added = 0
    for popup in no_image_popups:
        image_url = random.choice(DEFAULT_IMAGES)
        
        await conn.execute('''
            INSERT INTO popup_image (popup_id, image_url, is_thumbnail, created_at)
            VALUES ($1, $2, true, $3)
        ''', popup['id'], image_url, datetime.now())
        
        image_added += 1
        if image_added % 20 == 0:
            print(f"  ... {image_added}개 이미지 추가 완료")
    
    print(f"✅ 이미지 추가 완료: {image_added}개")
    
    await conn.close()
    
    print("\n" + "=" * 60)
    print("✅ 팝업 데이터 보강 완료!")
    print("=" * 60)


async def cleanup_test_zones():
    """연습용 존 정리 (데이터 확인 후 삭제 또는 수정)"""
    dsn = os.getenv('PGVECTOR_CONNECTION').replace('postgresql+psycopg://', 'postgresql://')
    conn = await asyncpg.connect(dsn)
    
    print("=" * 60)
    print("🧹 연습용 존 정리")
    print("=" * 60)
    
    # 연습용 존 조회
    test_zones = await conn.fetch('''
        SELECT id, name, notice, region_id
        FROM zone_area
        WHERE name LIKE '%연습용%' OR notice LIKE '%연습%'
    ''')
    
    print(f"\n🔍 연습용 존 발견: {len(test_zones)}개")
    for zone in test_zones:
        print(f"  [{zone['id']}] {zone['name']} (region={zone['region_id']})")
    
    if len(test_zones) > 0:
        # 연습용 존에 연결된 셀 개수 확인
        for zone in test_zones:
            cell_count = await conn.fetchval('''
                SELECT COUNT(*) FROM zone_cell WHERE zone_area_id = $1
            ''', zone['id'])
            
            popup_count = await conn.fetchval('''
                SELECT COUNT(*) FROM popup p
                JOIN zone_cell zc ON p.zone_cell_id = zc.id
                WHERE zc.zone_area_id = $1
            ''', zone['id'])
            
            print(f"  → 존 {zone['id']}: 셀 {cell_count}개, 팝업 {popup_count}개")
            
            # 팝업이 없으면 삭제 가능
            if popup_count == 0:
                print(f"     ⚠️ 팝업 없음 - 삭제 가능")
    
    # 실제 삭제는 주석 처리 (필요 시 활성화)
    # for zone in test_zones:
    #     await conn.execute('DELETE FROM zone_cell WHERE zone_area_id = $1', zone['id'])
    #     await conn.execute('DELETE FROM zone_area WHERE id = $1', zone['id'])
    #     print(f"  🗑️ 존 {zone['id']} 삭제 완료")
    
    await conn.close()
    print("\n⚠️ 실제 삭제는 비활성화됨 (스크립트에서 주석 해제 필요)")


if __name__ == "__main__":
    asyncio.run(enrich_popups())
    asyncio.run(cleanup_test_zones())

