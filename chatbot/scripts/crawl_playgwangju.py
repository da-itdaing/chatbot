#!/usr/bin/env python3
"""
플레이광주(playgwangju.co.kr) 행사/축제 데이터 크롤러

크롤링 대상:
- 행사/축제 카테고리 (https://playgwangju.co.kr/bbs/board.php?bo_table=festival)
- 각 구별 데이터 수집 (동구, 서구, 남구, 북구, 광산구)

수집 항목:
- 제목, 설명, 장소, 날짜, 이미지 URL, 카테고리
"""

import asyncio
import json
import re
import random
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
from bs4 import BeautifulSoup

# 발표일 기준
PRESENTATION_DATE = datetime(2025, 12, 10)

# 광주 5개구 좌표 (대표 위치)
DISTRICT_COORDS = {
    "동구": {"lat": 35.1457, "lon": 126.9227, "neighborhoods": ["충장로", "금남로", "대인동", "동명동", "계림동", "산수동", "지산동"]},
    "서구": {"lat": 35.1519, "lon": 126.8895, "neighborhoods": ["상무지구", "치평동", "풍암동", "화정동", "농성동", "양동"]},
    "남구": {"lat": 35.1333, "lon": 126.9025, "neighborhoods": ["양림동", "봉선동", "주월동", "진월동", "백운동", "방림동"]},
    "북구": {"lat": 35.1747, "lon": 126.9127, "neighborhoods": ["문흥동", "용봉동", "일곡동", "오룡동", "두암동", "운암동"]},
    "광산구": {"lat": 35.1922, "lon": 126.8136, "neighborhoods": ["수완지구", "첨단지구", "월곡동", "하남동", "신가동", "운남동"]},
}

# 카테고리별 URL
CATEGORY_URLS = {
    "행사축제": "https://playgwangju.co.kr/bbs/board.php?bo_table=festival",
    "전시": "https://playgwangju.co.kr/bbs/board.php?bo_table=exhibition",
    "콘서트뮤지컬": "https://playgwangju.co.kr/bbs/board.php?bo_table=concert",
}


async def fetch_page(client: httpx.AsyncClient, url: str) -> str:
    """페이지 HTML 가져오기"""
    try:
        resp = await client.get(url, timeout=30.0)
        resp.raise_for_status()
        return resp.text
    except Exception as e:
        print(f"[ERROR] Failed to fetch {url}: {e}")
        return ""


def parse_list_page(html: str) -> list[dict[str, str]]:
    """목록 페이지에서 개별 항목 링크 추출"""
    soup = BeautifulSoup(html, "html.parser")
    items = []
    
    # 다양한 선택자 시도
    selectors = [
        "div.bo_subject a",
        "td.td_subject a",
        "a.bo_cate_link",
        ".list-item a",
        "ul.gall_con li a",
    ]
    
    for selector in selectors:
        links = soup.select(selector)
        for link in links:
            href = link.get("href", "")
            title = link.get_text(strip=True)
            if href and "wr_id=" in href and title:
                full_url = href if href.startswith("http") else f"https://playgwangju.co.kr{href}"
                items.append({"url": full_url, "title": title})
    
    return items


def parse_detail_page(html: str, url: str) -> dict[str, Any]:
    """상세 페이지 파싱"""
    soup = BeautifulSoup(html, "html.parser")
    
    # 제목
    title_el = soup.select_one("h1, .view_title, #bo_v_title")
    title = title_el.get_text(strip=True) if title_el else ""
    
    # 본문
    content_el = soup.select_one("#bo_v_con, .view_content, .bo_v_con")
    content = content_el.get_text(strip=True)[:500] if content_el else ""
    
    # 이미지
    images = []
    for img in soup.select("#bo_v_con img, .view_content img, .bo_v_con img"):
        src = img.get("src", "")
        if src and not src.startswith("data:"):
            if not src.startswith("http"):
                src = f"https://playgwangju.co.kr{src}"
            images.append(src)
    
    # 날짜 추출 시도
    date_patterns = [
        r"(\d{4})[.-](\d{1,2})[.-](\d{1,2})",
        r"(\d{4})년\s*(\d{1,2})월\s*(\d{1,2})일",
    ]
    dates = []
    text = soup.get_text()
    for pattern in date_patterns:
        matches = re.findall(pattern, text)
        for m in matches:
            try:
                dates.append(datetime(int(m[0]), int(m[1]), int(m[2])))
            except:
                pass
    
    # 장소 추출 시도
    location = ""
    location_keywords = ["장소", "위치", "주소", "어디서"]
    for keyword in location_keywords:
        match = re.search(rf"{keyword}\s*[:：]\s*(.+?)(?:\n|<|$)", text)
        if match:
            location = match.group(1).strip()[:100]
            break
    
    return {
        "title": title,
        "content": content,
        "images": images[:3],  # 최대 3개
        "dates": dates,
        "location": location,
        "source_url": url,
    }


def determine_district(title: str, content: str, location: str) -> str:
    """제목/내용에서 구 추출"""
    text = f"{title} {content} {location}"
    
    for district in DISTRICT_COORDS.keys():
        if district in text:
            return district
        # 동네 이름으로도 확인
        for neighborhood in DISTRICT_COORDS[district]["neighborhoods"]:
            if neighborhood in text:
                return district
    
    # 찾지 못하면 랜덤 배정 (균형 맞추기)
    return random.choice(list(DISTRICT_COORDS.keys()))


def adjust_date_for_presentation(original_date: datetime | None) -> tuple[datetime, datetime]:
    """발표일(12/10) 기준으로 날짜 조정"""
    # 발표일 전후 7일 범위로 설정
    start_offset = random.randint(-5, 5)
    duration = random.randint(1, 7)
    
    start_date = PRESENTATION_DATE + timedelta(days=start_offset)
    end_date = start_date + timedelta(days=duration)
    
    return start_date, end_date


def generate_event_data(
    parsed: dict[str, Any],
    category: str,
    index: int,
) -> dict[str, Any]:
    """크롤링 데이터를 팝업 데이터 형식으로 변환"""
    
    district = determine_district(
        parsed["title"],
        parsed["content"],
        parsed["location"],
    )
    
    # 날짜 조정
    original_date = parsed["dates"][0] if parsed["dates"] else None
    start_date, end_date = adjust_date_for_presentation(original_date)
    
    # 좌표 (구 대표 좌표 + 약간의 변동)
    base_coords = DISTRICT_COORDS[district]
    lat = base_coords["lat"] + random.uniform(-0.01, 0.01)
    lon = base_coords["lon"] + random.uniform(-0.01, 0.01)
    
    # 동네 선택
    neighborhood = random.choice(base_coords["neighborhoods"])
    
    # 카테고리 매핑
    category_map = {
        "행사축제": "축제",
        "전시": "전시",
        "콘서트뮤지컬": "공연",
    }
    
    # 태그 생성
    tags = [district, category_map.get(category, "행사")]
    if neighborhood:
        tags.append(neighborhood)
    
    # 운영시간 랜덤
    operating_hours = random.choice([
        {"weekday": "10:00-18:00", "weekend": "10:00-20:00"},
        {"weekday": "11:00-19:00", "weekend": "10:00-21:00"},
        {"weekday": "09:00-17:00", "weekend": "10:00-18:00"},
    ])
    
    # 가격대
    price_range = random.choice(["무료", "₩", "₩₩"])
    
    # 속성
    attributes = random.sample([
        "무료입장", "실내", "야외", "체험가능", "포토존", "푸드존", 
        "주차가능", "반려동물가능", "가족추천",
    ], k=random.randint(2, 4))
    
    return {
        "crawl_id": f"PG-{category[:2].upper()}-{index:04d}",
        "name": parsed["title"] or f"{district} {category_map.get(category, '행사')} #{index}",
        "description": parsed["content"] or f"{district} {neighborhood}에서 열리는 {category_map.get(category, '행사')}입니다.",
        "district": district,
        "neighborhood": neighborhood,
        "address": f"광주광역시 {district} {neighborhood}",
        "lat": round(lat, 6),
        "lon": round(lon, 6),
        "start_date": start_date.strftime("%Y-%m-%d"),
        "end_date": end_date.strftime("%Y-%m-%d"),
        "operating_hours": operating_hours,
        "category": category_map.get(category, "행사"),
        "price_range": price_range,
        "attributes": attributes,
        "tags": tags,
        "images": parsed["images"],
        "source_url": parsed["source_url"],
        "rating": round(random.uniform(3.8, 4.9), 1),
    }


async def crawl_category(
    client: httpx.AsyncClient,
    category: str,
    url: str,
    max_pages: int = 10,
) -> list[dict[str, Any]]:
    """카테고리별 크롤링"""
    print(f"\n[INFO] Crawling {category}...")
    
    all_items = []
    
    for page in range(1, max_pages + 1):
        page_url = f"{url}&page={page}"
        print(f"  Page {page}: {page_url}")
        
        html = await fetch_page(client, page_url)
        if not html:
            break
        
        items = parse_list_page(html)
        if not items:
            print(f"  No items found on page {page}, stopping.")
            break
        
        print(f"  Found {len(items)} items")
        
        for item in items:
            detail_html = await fetch_page(client, item["url"])
            if detail_html:
                parsed = parse_detail_page(detail_html, item["url"])
                if parsed["title"]:
                    all_items.append({**parsed, "category": category})
            
            # 서버 부하 방지
            await asyncio.sleep(0.5)
    
    return all_items


async def main():
    """메인 크롤링 실행"""
    print("=" * 60)
    print("플레이광주 크롤러 시작")
    print(f"발표일 기준: {PRESENTATION_DATE.strftime('%Y-%m-%d')}")
    print("=" * 60)
    
    output_dir = Path(__file__).parent.parent / "data" / "crawled"
    output_dir.mkdir(parents=True, exist_ok=True)
    
    all_events = []
    
    async with httpx.AsyncClient(
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        },
        follow_redirects=True,
    ) as client:
        
        for category, url in CATEGORY_URLS.items():
            items = await crawl_category(client, category, url, max_pages=5)
            
            # 이벤트 데이터로 변환
            for i, item in enumerate(items, 1):
                event = generate_event_data(item, category, i)
                all_events.append(event)
            
            print(f"  → {category}: {len(items)} items crawled")
    
    # 구별 분포 확인
    district_counts = {}
    for event in all_events:
        d = event["district"]
        district_counts[d] = district_counts.get(d, 0) + 1
    
    print("\n" + "=" * 60)
    print("크롤링 완료!")
    print(f"총 {len(all_events)}개 이벤트")
    print("\n구별 분포:")
    for district, count in sorted(district_counts.items()):
        print(f"  {district}: {count}개")
    
    # 구별 불균형 보정 (부족한 구에 더미 데이터 추가)
    target_per_district = 30
    for district in DISTRICT_COORDS.keys():
        current = district_counts.get(district, 0)
        if current < target_per_district:
            needed = target_per_district - current
            print(f"\n[INFO] {district} 부족 ({current}개). {needed}개 더미 데이터 추가...")
            
            for i in range(needed):
                dummy = generate_dummy_event(district, len(all_events) + i + 1)
                all_events.append(dummy)
    
    # 저장
    output_file = output_dir / "playgwangju_events.json"
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(all_events, f, ensure_ascii=False, indent=2)
    
    print(f"\n저장 완료: {output_file}")
    print(f"최종 {len(all_events)}개 이벤트")
    
    # 최종 분포
    final_counts = {}
    for event in all_events:
        d = event["district"]
        final_counts[d] = final_counts.get(d, 0) + 1
    
    print("\n최종 구별 분포:")
    for district, count in sorted(final_counts.items()):
        print(f"  {district}: {count}개")


def generate_dummy_event(district: str, index: int) -> dict[str, Any]:
    """부족한 구에 대한 더미 이벤트 생성"""
    base_coords = DISTRICT_COORDS[district]
    neighborhood = random.choice(base_coords["neighborhoods"])
    
    # 이벤트 템플릿
    templates = [
        ("{neighborhood} 플리마켓", "플리마켓", "{neighborhood}에서 열리는 핸드메이드 플리마켓입니다. 다양한 수공예품과 빈티지 아이템을 만나보세요."),
        ("{neighborhood} 야시장", "야시장", "{neighborhood}에서 저녁마다 열리는 야시장입니다. 맛있는 먹거리와 쇼핑을 한번에!"),
        ("{neighborhood} 문화축제", "축제", "{neighborhood} 일대에서 열리는 문화축제입니다. 공연, 체험, 먹거리가 가득해요."),
        ("{neighborhood} 아트마켓", "전시", "{neighborhood}에서 열리는 아트마켓입니다. 지역 작가들의 작품을 만나보세요."),
        ("{neighborhood} 주말시장", "플리마켓", "매주 주말 {neighborhood}에서 열리는 주말시장입니다. 신선한 로컬 푸드와 수공예품!"),
        ("{neighborhood} 거리공연 페스타", "공연", "{neighborhood} 거리에서 펼쳐지는 공연 페스타입니다. 다양한 버스킹과 거리공연!"),
        ("{neighborhood} 푸드페스타", "축제", "{neighborhood}에서 열리는 푸드페스타! 다양한 맛집과 푸드트럭이 함께해요."),
        ("{district} 빈티지마켓", "플리마켓", "{district}에서 열리는 빈티지마켓입니다. 레트로 감성의 아이템을 찾아보세요."),
    ]
    
    template = random.choice(templates)
    name = template[0].format(district=district, neighborhood=neighborhood)
    category = template[1]
    desc = template[2].format(district=district, neighborhood=neighborhood)
    
    start_date, end_date = adjust_date_for_presentation(None)
    
    lat = base_coords["lat"] + random.uniform(-0.015, 0.015)
    lon = base_coords["lon"] + random.uniform(-0.015, 0.015)
    
    return {
        "crawl_id": f"DUMMY-{district[:1]}-{index:04d}",
        "name": name,
        "description": desc,
        "district": district,
        "neighborhood": neighborhood,
        "address": f"광주광역시 {district} {neighborhood}",
        "lat": round(lat, 6),
        "lon": round(lon, 6),
        "start_date": start_date.strftime("%Y-%m-%d"),
        "end_date": end_date.strftime("%Y-%m-%d"),
        "operating_hours": random.choice([
            {"weekday": "10:00-18:00", "weekend": "10:00-20:00"},
            {"weekday": "11:00-19:00", "weekend": "10:00-21:00"},
            {"weekday": "18:00-23:00", "weekend": "17:00-24:00"},  # 야시장용
        ]),
        "category": category,
        "price_range": random.choice(["무료", "₩"]),
        "attributes": random.sample([
            "무료입장", "실내", "야외", "체험가능", "포토존", "푸드존",
            "주차가능", "반려동물가능", "가족추천",
        ], k=random.randint(2, 4)),
        "tags": [district, neighborhood, category],
        "images": [],  # 더미는 이미지 없음 → 나중에 플레이스홀더 사용
        "source_url": "",
        "rating": round(random.uniform(4.0, 4.8), 1),
    }


if __name__ == "__main__":
    asyncio.run(main())

