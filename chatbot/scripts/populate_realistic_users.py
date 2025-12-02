#!/usr/bin/env python3
"""
사용자 데이터를 실제감 있게 업데이트하는 스크립트

- 관리자: 광주광역시 각 구청 및 관련 기관
- 판매자: 한국인 이름 + 실제 프로필 사진
- 소비자: 한국인 이름 + 실제 프로필 사진
- 리뷰: 팝업에 대한 리뷰 추가
"""

import asyncio
import asyncpg
import os
import random
import uuid
import boto3
import requests
from datetime import datetime, timedelta
from dotenv import load_dotenv
from io import BytesIO

load_dotenv('/home/ubuntu/chatbot/chatbot.env')

# S3 설정
S3_BUCKET = "daitdaing-static-files"
S3_REGION = "ap-northeast-2"

# 광주광역시 구청/기관 정보 (관리자용)
GWANGJU_ADMINS = [
    {
        "id": 2,  # admin1
        "name": "광주광역시청",
        "nickname": "광주시청",
        "email": "admin@gwangju.go.kr",
        "logo_url": "https://www.gwangju.go.kr/images/common/ci_symbol.png",
    },
    {
        "id": 7,  # admin2
        "name": "광주 동구청",
        "nickname": "동구청",
        "email": "admin@donggu.gwangju.kr",
        "logo_url": "https://www.donggu.kr/images/common/ci_symbol.png",
    },
    {
        "id": 8,  # admin3
        "name": "광주 서구청",
        "nickname": "서구청",
        "email": "admin@seogu.gwangju.kr",
        "logo_url": "https://www.seogu.kr/images/common/ci_symbol.png",
    },
    {
        "id": 9,  # admin4
        "name": "광주 남구청",
        "nickname": "남구청",
        "email": "admin@namgu.gwangju.kr",
        "logo_url": "https://www.namgu.kr/images/common/ci_symbol.png",
    },
    {
        "id": 10,  # admin5
        "name": "광주 북구청",
        "nickname": "북구청",
        "email": "admin@bukgu.gwangju.kr",
        "logo_url": "https://www.bukgu.kr/images/common/ci_symbol.png",
    },
]

# 한국인 이름 (성 + 이름)
KOREAN_SURNAMES = ["김", "이", "박", "최", "정", "강", "조", "윤", "장", "임", "한", "오", "서", "신", "권", "황", "안", "송", "류", "홍"]
KOREAN_NAMES_MALE = ["민준", "서준", "도윤", "예준", "시우", "하준", "지호", "준서", "준우", "현우", "지훈", "건우", "우진", "선우", "서진", "민재", "현준", "연우", "유준", "정우"]
KOREAN_NAMES_FEMALE = ["서연", "서윤", "지우", "서현", "민서", "하은", "하윤", "윤서", "지유", "채원", "수아", "지아", "지민", "서아", "수빈", "다은", "예은", "지원", "소율", "예린"]

# 판매자 브랜드명 (한국식)
SELLER_BRANDS = [
    "달빛공방", "소담공예", "하루소품", "꽃비아뜰리에", "나무결공방",
    "푸른달상회", "손끝예술", "바람꽃작업실", "별빛가게", "정원의아침",
    "숲속공방", "햇살마켓", "고요한작업실", "물빛공예", "산들바람상회",
    "초록마을", "달콤한하루", "빈티지공간", "수공예마을", "아트플레이스",
    "크리에이티브랩", "메이커스페이스", "로컬마켓", "핸드메이드샵", "아티스트마켓"
]

# 소비자 닉네임 패턴
CONSUMER_NICKNAMES = [
    "팝업러버", "마켓탐험가", "플리마켓팬", "문화생활러", "광주사람",
    "동구민", "서구민", "남구민", "북구민", "광산구민",
    "쇼핑홀릭", "감성여행자", "일상탐험가", "주말나들이", "취미생활러"
]

# 리뷰 템플릿
REVIEW_TEMPLATES = [
    "정말 좋은 경험이었어요! 다음에도 꼭 방문하고 싶습니다.",
    "분위기가 너무 좋았어요. 친구들에게 추천하고 싶어요.",
    "다양한 상품들이 있어서 구경하는 재미가 있었습니다.",
    "판매자분들이 친절하셔서 좋았어요.",
    "주말에 가족과 함께 방문했는데 아이들도 좋아했어요.",
    "핸드메이드 제품들이 정말 예뻤어요. 선물용으로 구매했습니다.",
    "맛있는 먹거리도 많고 볼거리도 많아서 시간 가는 줄 몰랐어요.",
    "사진 찍기 좋은 곳이에요. 인스타 감성 충만!",
    "가격도 합리적이고 품질도 좋았습니다.",
    "다음 행사도 기대됩니다. 또 올게요!",
    "광주에 이런 곳이 있다니! 자주 와야겠어요.",
    "주차가 조금 불편했지만 그래도 만족스러웠어요.",
    "독특한 제품들이 많아서 구경하는 재미가 있었습니다.",
    "커피도 맛있고 분위기도 좋았어요.",
    "아기자기한 소품들이 많아서 좋았습니다.",
]

# 실제 한국인 프로필 사진 URL
# pravatar.cc 또는 ui-avatars 사용 (저작권 프리)
def get_korean_profile_url(gender: str, seed: int) -> str:
    """한국인 스타일 프로필 사진 URL 생성"""
    # pravatar.cc 사용 (다양한 프로필 사진 제공)
    # 또는 DiceBear 아바타 생성 서비스 사용
    # return f"https://i.pravatar.cc/300?img={seed % 70}"
    
    # DiceBear 아바타 (더 다양한 스타일)
    styles = ["adventurer", "adventurer-neutral", "avataaars", "big-ears", "big-smile", "lorelei", "micah", "miniavs", "personas"]
    style = styles[seed % len(styles)]
    return f"https://api.dicebear.com/7.x/{style}/svg?seed={seed}&backgroundColor=b6e3f4,c0aede,d1d4f9,ffd5dc,ffdfbf"


def get_institution_logo_url(name: str) -> str:
    """기관 로고 URL 생성 (실제 로고 대신 placeholder 사용)"""
    # 실제로는 각 기관의 공식 로고를 사용해야 함
    # 여기서는 UI Avatars 서비스 사용
    encoded_name = name.replace(" ", "+")
    return f"https://ui-avatars.com/api/?name={encoded_name}&size=200&background=eb0000&color=fff&bold=true&font-size=0.33"


async def download_and_upload_image(url: str, s3_key: str) -> str | None:
    """이미지를 다운로드하여 S3에 업로드"""
    try:
        # 이미지 다운로드
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }
        response = requests.get(url, headers=headers, timeout=10)
        if response.status_code != 200:
            print(f"  이미지 다운로드 실패: {url} (status: {response.status_code})")
            return None
        
        # S3 업로드
        s3_client = boto3.client('s3', region_name=S3_REGION)
        content_type = response.headers.get('Content-Type', 'image/jpeg')
        
        s3_client.put_object(
            Bucket=S3_BUCKET,
            Key=s3_key,
            Body=response.content,
            ContentType=content_type,
        )
        
        s3_url = f"https://{S3_BUCKET}.s3.{S3_REGION}.amazonaws.com/{s3_key}"
        return s3_url
    except Exception as e:
        print(f"  이미지 업로드 실패: {e}")
        return None


def generate_korean_name(gender: str) -> tuple[str, str]:
    """한국인 이름 생성 (성, 이름)"""
    surname = random.choice(KOREAN_SURNAMES)
    if gender == "F":
        given_name = random.choice(KOREAN_NAMES_FEMALE)
    else:
        given_name = random.choice(KOREAN_NAMES_MALE)
    return surname, given_name


async def update_admins(conn: asyncpg.Connection):
    """관리자 계정을 광주 구청/기관으로 업데이트"""
    print("\n=== 관리자 계정 업데이트 ===")
    
    for admin in GWANGJU_ADMINS:
        # 기관 로고 URL 생성 및 S3 업로드
        logo_url = get_institution_logo_url(admin["name"])
        s3_key = f"uploads/admin/{admin['id']}/profile.png"
        
        print(f"  {admin['name']} 로고 업로드 중...")
        s3_url = await download_and_upload_image(logo_url, s3_key)
        
        if not s3_url:
            s3_url = logo_url  # 업로드 실패 시 원본 URL 사용
            s3_key = None
        
        # DB 업데이트
        await conn.execute("""
            UPDATE users SET
                name = $1,
                nickname = $2,
                email = $3,
                profile_image_url = $4,
                profile_image_key = $5,
                updated_at = NOW()
            WHERE id = $6
        """, admin["name"], admin["nickname"], admin["email"], s3_url, s3_key, admin["id"])
        
        print(f"  ✓ {admin['name']} 업데이트 완료")


async def update_sellers(conn: asyncpg.Connection):
    """판매자 계정을 한국인 이름/프로필로 업데이트"""
    print("\n=== 판매자 계정 업데이트 ===")
    
    # 판매자 목록 조회
    sellers = await conn.fetch("""
        SELECT id, login_id FROM users WHERE role = 'SELLER' ORDER BY id
    """)
    
    brand_idx = 0
    for seller in sellers:
        # 랜덤 성별 및 이름 생성
        gender = random.choice(["M", "F"])
        surname, given_name = generate_korean_name(gender)
        full_name = surname + given_name
        
        # 브랜드명 (또는 개인 이름)
        if brand_idx < len(SELLER_BRANDS):
            nickname = SELLER_BRANDS[brand_idx]
            brand_idx += 1
        else:
            nickname = f"{full_name}의 공방"
        
        # 프로필 사진 URL
        profile_url = get_korean_profile_url(gender, seller["id"])
        s3_key = f"uploads/seller/{seller['id']}/profile.jpg"
        
        # S3 업로드
        s3_url = await download_and_upload_image(profile_url, s3_key)
        if not s3_url:
            s3_url = profile_url
            s3_key = None
        
        # 연령대 랜덤 (20~40대)
        age_group = random.choice([20, 30, 40])
        mbti = random.choice(["ENFJ", "INFP", "ENTP", "ISFJ", "ENTJ", "ISFP", "ENFP", "INTJ"])
        
        # DB 업데이트
        await conn.execute("""
            UPDATE users SET
                name = $1,
                nickname = $2,
                profile_image_url = $3,
                profile_image_key = $4,
                age_group = $5,
                mbti = $6,
                updated_at = NOW()
            WHERE id = $7
        """, full_name, nickname, s3_url, s3_key, age_group, mbti, seller["id"])
        
        # seller_profile도 업데이트
        profile_exists = await conn.fetchval(
            "SELECT 1 FROM seller_profile WHERE user_id = $1", seller["id"]
        )
        
        if profile_exists:
            await conn.execute("""
                UPDATE seller_profile SET
                    profile_image_url = $1,
                    profile_image_key = $2,
                    introduction = $3,
                    updated_at = NOW()
                WHERE user_id = $4
            """, s3_url, s3_key, f"{nickname}입니다. 광주에서 활동하는 셀러예요!", seller["id"])
        
        if seller["id"] % 10 == 0:
            print(f"  ✓ 판매자 {seller['id']}까지 업데이트 완료")
    
    print(f"  ✓ 총 {len(sellers)}명의 판매자 업데이트 완료")


async def update_consumers(conn: asyncpg.Connection):
    """소비자 계정을 한국인 이름/프로필로 업데이트"""
    print("\n=== 소비자 계정 업데이트 ===")
    
    # 소비자 목록 조회
    consumers = await conn.fetch("""
        SELECT id, login_id FROM users WHERE role = 'CONSUMER' ORDER BY id
    """)
    
    for i, consumer in enumerate(consumers):
        # 랜덤 성별 및 이름 생성
        gender = random.choice(["M", "F"])
        surname, given_name = generate_korean_name(gender)
        full_name = surname + given_name
        
        # 닉네임
        nickname_base = random.choice(CONSUMER_NICKNAMES)
        nickname = f"{nickname_base}{i+1}"
        
        # 프로필 사진 URL
        profile_url = get_korean_profile_url(gender, consumer["id"] + 50)  # 판매자와 다른 seed
        s3_key = f"uploads/consumer/{consumer['id']}/profile.jpg"
        
        # S3 업로드
        s3_url = await download_and_upload_image(profile_url, s3_key)
        if not s3_url:
            s3_url = profile_url
            s3_key = None
        
        # 연령대 랜덤 (10~50대)
        age_group = random.choice([10, 20, 20, 30, 30, 30, 40, 40, 50])
        mbti = random.choice(["ENFJ", "INFP", "ENTP", "ISFJ", "ENTJ", "ISFP", "ENFP", "INTJ", "ESFJ", "ISTP"])
        
        # DB 업데이트
        await conn.execute("""
            UPDATE users SET
                name = $1,
                nickname = $2,
                profile_image_url = $3,
                profile_image_key = $4,
                age_group = $5,
                mbti = $6,
                updated_at = NOW()
            WHERE id = $7
        """, full_name, nickname, s3_url, s3_key, age_group, mbti, consumer["id"])
        
        if consumer["id"] % 10 == 0:
            print(f"  ✓ 소비자 {consumer['id']}까지 업데이트 완료")
    
    print(f"  ✓ 총 {len(consumers)}명의 소비자 업데이트 완료")


async def create_reviews(conn: asyncpg.Connection):
    """팝업에 대한 리뷰 생성"""
    print("\n=== 리뷰 생성 ===")
    
    # 기존 리뷰 수 확인
    existing_reviews = await conn.fetchval("SELECT COUNT(*) FROM review")
    if existing_reviews > 0:
        print(f"  이미 {existing_reviews}개의 리뷰가 존재합니다. 스킵합니다.")
        return
    
    # 소비자 목록 조회
    consumers = await conn.fetch("""
        SELECT id FROM users WHERE role = 'CONSUMER' ORDER BY id
    """)
    consumer_ids = [c["id"] for c in consumers]
    
    # 팝업 목록 조회
    popups = await conn.fetch("""
        SELECT id FROM popup WHERE approval_status = 'APPROVED' ORDER BY id
    """)
    
    review_count = 0
    for popup in popups:
        # 각 팝업에 1~5개의 리뷰 생성
        num_reviews = random.randint(1, 5)
        reviewers = random.sample(consumer_ids, min(num_reviews, len(consumer_ids)))
        
        for consumer_id in reviewers:
            rating = random.choices([3, 4, 4, 5, 5, 5], weights=[1, 2, 3, 4, 5, 5])[0]  # 긍정적 편향
            content = random.choice(REVIEW_TEMPLATES)
            
            # 리뷰 생성 날짜 (최근 30일 내)
            created_at = datetime.now() - timedelta(days=random.randint(1, 30))
            
            await conn.execute("""
                INSERT INTO review (consumer_id, popup_id, rating, content, created_at)
                VALUES ($1, $2, $3, $4, $5)
            """, consumer_id, popup["id"], rating, content, created_at)
            
            review_count += 1
        
        if popup["id"] % 20 == 0:
            print(f"  ✓ 팝업 {popup['id']}까지 리뷰 생성 완료")
    
    print(f"  ✓ 총 {review_count}개의 리뷰 생성 완료")


async def main():
    print("=" * 60)
    print("사용자 데이터 실제화 스크립트")
    print("=" * 60)
    
    db_url = os.getenv('PGVECTOR_CONNECTION', '').replace('postgresql+psycopg', 'postgresql')
    conn = await asyncpg.connect(db_url)
    
    try:
        # 1. 관리자 업데이트
        await update_admins(conn)
        
        # 2. 판매자 업데이트
        await update_sellers(conn)
        
        # 3. 소비자 업데이트
        await update_consumers(conn)
        
        # 4. 리뷰 생성
        await create_reviews(conn)
        
        print("\n" + "=" * 60)
        print("✓ 모든 업데이트 완료!")
        print("=" * 60)
        
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())

