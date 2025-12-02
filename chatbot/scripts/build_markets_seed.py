#!/usr/bin/env python3
"""
스크래핑된 이벤트 데이터를 markets_seed.json 형식으로 변환하는 스크립트.

markets_seed.json 스키마:
- market_id: 고유 ID
- market_name: 마켓 이름
- market_description: 설명
- market_category: 카테고리
- market_attribute: 속성 태그 리스트
- market_ameni: 편의시설 리스트
- market_rating: 평점
- market_location: 위치 정보 리스트
- operating_hours: 운영 시간
- operating_days: 운영 요일
- price_range: 가격대
- event_type: 이벤트 유형
- event_dates: 이벤트 날짜 리스트
- contact_phone: 연락처
- search_tags: 검색 태그
- image_url: 이미지 URL
- sns_links: SNS 링크
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

# 경로 설정
SCRIPT_DIR = Path(__file__).resolve().parent
DATA_DIR = SCRIPT_DIR.parent / "data"
INPUT_FILE = DATA_DIR / "events_with_s3_images.json"
OUTPUT_FILE = Path("/home/ubuntu/markets_seed.json")

# 카테고리 매핑
CATEGORY_MAPPING = {
    "플리마켓": "플리마켓",
    "야시장": "야시장",
    "마켓": "플리마켓",
    "핸드메이드": "핸드메이드",
    "전시": "전시",
    "축제": "축제",
    "페스타": "축제",
    "체험": "체험",
    "아트": "아트",
    "문화": "문화행사",
}

# 광주 구별 좌표 (중심점)
DISTRICT_COORDS = {
    "동구": {"lat": 35.1464, "lon": 126.9231},
    "서구": {"lat": 35.1521, "lon": 126.8895},
    "남구": {"lat": 35.1333, "lon": 126.9025},
    "북구": {"lat": 35.1744, "lon": 126.9120},
    "광산구": {"lat": 35.1396, "lon": 126.7936},
}

# Zone ID 매핑
ZONE_ID_MAPPING = {
    "동구": "east-001",
    "서구": "west-001",
    "남구": "south-001",
    "북구": "north-001",
    "광산구": "gwangsan-001",
}


def extract_category(title: str, description: str) -> str:
    """제목과 설명에서 카테고리 추출"""
    text = f"{title} {description}".lower()
    
    for keyword, category in CATEGORY_MAPPING.items():
        if keyword.lower() in text:
            return category
    
    return "문화행사"  # 기본 카테고리


def extract_attributes(title: str, description: str, is_free: bool) -> list[str]:
    """속성 태그 추출"""
    text = f"{title} {description}".lower()
    attrs = []
    
    # 키워드 기반 속성
    attr_keywords = {
        "포토존": ["포토", "사진", "인스타"],
        "체험가능": ["체험", "만들기", "클래스", "워크샵"],
        "야외": ["야외", "광장", "공원", "거리"],
        "실내": ["실내", "센터", "관", "홀"],
        "가족과 함께": ["가족", "어린이", "키즈", "아이"],
        "친구와 함께": ["친구", "동행"],
        "혼자 가기 좋아요": ["혼자", "솔로"],
        "데이트": ["데이트", "커플"],
        "아기자기한": ["아기자기", "소품", "핸드메이드"],
        "레트로/빈티지": ["빈티지", "레트로", "복고"],
        "감성적인": ["감성", "아트", "예술"],
    }
    
    for attr, keywords in attr_keywords.items():
        for keyword in keywords:
            if keyword in text:
                attrs.append(attr)
                break
    
    # 무료 여부
    if is_free:
        attrs.append("무료입장")
    
    # 기본 속성 추가
    if not attrs:
        attrs.append("문화체험")
    
    return list(set(attrs))[:5]  # 최대 5개


def extract_amenities(title: str, description: str) -> list[str]:
    """편의시설 추출"""
    text = f"{title} {description}".lower()
    amenities = []
    
    ameni_keywords = {
        "주차가능": ["주차"],
        "wifi": ["와이파이", "wifi"],
        "화장실": ["화장실"],
        "휠체어": ["휠체어", "장애인"],
        "유아시설": ["유아", "수유"],
    }
    
    for ameni, keywords in ameni_keywords.items():
        for keyword in keywords:
            if keyword in text:
                amenities.append(ameni)
                break
    
    return amenities


def extract_search_tags(title: str, description: str, category: str, district: str) -> list[str]:
    """검색 태그 추출"""
    tags = [category, district]
    
    # 제목에서 주요 단어 추출
    text = f"{title} {description}"
    
    # 특정 키워드 추가
    tag_keywords = [
        "플리마켓", "야시장", "핸드메이드", "전시", "축제", "체험",
        "아트", "문화", "공방", "마켓", "페스타", "갤러리",
        "충장로", "동명동", "대인시장", "ACC", "아시아문화전당",
    ]
    
    for keyword in tag_keywords:
        if keyword in text:
            tags.append(keyword)
    
    return list(set(tags))[:10]


def generate_event_dates(start_date: str, end_date: str) -> list[dict]:
    """이벤트 날짜 리스트 생성"""
    dates = []
    day_names = ["월", "화", "수", "목", "금", "토", "일"]
    
    try:
        start = datetime.strptime(start_date, "%Y-%m-%d")
        end = datetime.strptime(end_date, "%Y-%m-%d")
        
        current = start
        while current <= end:
            dates.append({
                "date": current.strftime("%Y-%m-%d"),
                "day_of_week": day_names[current.weekday()],
                "time": "10:00-18:00",  # 기본 시간
                "type": "regular",
            })
            current += timedelta(days=1)
            
            # 최대 14일
            if len(dates) >= 14:
                break
                
    except ValueError:
        pass
    
    return dates


def convert_event_to_market(event: dict, index: int) -> dict:
    """이벤트를 market 형식으로 변환"""
    title = event.get("title", "").strip()
    # 제목에서 "무료", "전시중" 등 제거
    title = re.sub(r"^(무료|전시중|진행중|D-\d+)\s*", "", title)
    
    description = event.get("description", "") or f"{title} - 광주광역시 {event.get('district', '동구')}에서 열리는 문화 행사입니다."
    
    district = event.get("district", "동구")
    coords = DISTRICT_COORDS.get(district, DISTRICT_COORDS["동구"])
    
    category = extract_category(title, description)
    attributes = extract_attributes(title, description, event.get("is_free", False))
    amenities = extract_amenities(title, description)
    search_tags = extract_search_tags(title, description, category, district)
    
    # 이미지 URL (S3 우선, 없으면 원본)
    image_url = event.get("s3_image_url") or event.get("image_url", "")
    
    return {
        "market_id": f"REAL-{index:03d}",
        "market_name": title,
        "market_description": description[:500] if description else "",
        "market_category": category,
        "market_attribute": attributes,
        "market_ameni": amenities,
        "market_rating": 4.0 + (index % 10) * 0.1,  # 4.0 ~ 4.9
        "market_location": [
            {
                "address": f"광주광역시 {district}",
                "lat": coords["lat"] + (index % 10) * 0.001,
                "lon": coords["lon"] + (index % 10) * 0.001,
                "distance_km": 1.0 + (index % 5) * 0.5,
                "zone_id": ZONE_ID_MAPPING.get(district, "east-001"),
            }
        ],
        "operating_hours": {
            "weekday": "10:00-18:00",
            "weekend": "10:00-20:00",
        },
        "operating_days": ["토", "일"] if "주말" in description else ["금", "토", "일"],
        "price_range": "무료" if event.get("is_free", False) else "₩₩",
        "event_type": "special" if "축제" in title or "페스타" in title else "regular",
        "event_dates": generate_event_dates(
            event.get("start_date", datetime.now().strftime("%Y-%m-%d")),
            event.get("end_date", datetime.now().strftime("%Y-%m-%d")),
        ),
        "contact_phone": "",
        "search_tags": search_tags,
        "image_url": image_url,
        "sns_links": {
            "instagram": None,
            "naver_blog": None,
        },
        "source_url": event.get("detail_url", ""),
        "source": "playgwangju.co.kr",
    }


def main():
    """메인 함수"""
    print("=" * 60)
    print("markets_seed.json 생성")
    print("=" * 60)
    
    # 입력 데이터 로드
    with open(INPUT_FILE, "r", encoding="utf-8") as f:
        events = json.load(f)
    
    print(f"입력 이벤트: {len(events)}개")
    
    # 변환
    markets = []
    for i, event in enumerate(events, start=1):
        market = convert_event_to_market(event, i)
        markets.append(market)
        print(f"[{i}/{len(events)}] {market['market_name'][:40]}...")
    
    # 저장
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(markets, f, ensure_ascii=False, indent=2)
    
    print(f"\n저장 완료: {OUTPUT_FILE}")
    print(f"총 {len(markets)}개 마켓 생성")
    
    # 통계
    categories = {}
    districts = {}
    for m in markets:
        cat = m["market_category"]
        categories[cat] = categories.get(cat, 0) + 1
        
        dist = m["market_location"][0]["address"].replace("광주광역시 ", "")
        districts[dist] = districts.get(dist, 0) + 1
    
    print("\n=== 카테고리별 ===")
    for cat, count in sorted(categories.items(), key=lambda x: -x[1]):
        print(f"  {cat}: {count}건")
    
    print("\n=== 구별 ===")
    for dist, count in sorted(districts.items(), key=lambda x: -x[1]):
        print(f"  {dist}: {count}건")


if __name__ == "__main__":
    main()

