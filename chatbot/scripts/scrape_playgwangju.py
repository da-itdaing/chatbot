#!/usr/bin/env python3
"""
playgwangju.co.kr에서 광주 플리마켓/팝업 행사 데이터를 수집하는 스크립트.

수집 대상:
- 행사/축제 (festival) 카테고리에서 플리마켓, 마켓, 팝업 관련 행사
- 전시 (exhibition) 카테고리에서 팝업스토어 관련 행사

제외 대상:
- 뮤지컬, 연극, 오페라, 클래식, 국악, 무용 (서비스 범위 외)
"""

from __future__ import annotations

import json
import re
import ssl
import time
import urllib3
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter

# SSL 경고 비활성화 (개발용)
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# 상수 정의
BASE_URL = "https://playgwangju.co.kr"
FESTIVAL_URL = f"{BASE_URL}/bbs/board.php?bo_table=festival"
EXHIBITION_URL = f"{BASE_URL}/bbs/board.php?bo_table=exhibition"

# 포함할 키워드 (플리마켓/팝업 관련)
INCLUDE_KEYWORDS = [
    "마켓", "플리", "팝업", "페스타", "축제", "야시장", "핸드메이드",
    "원데이", "체험", "공방", "수공예", "빈티지", "플라워", "푸드트럭",
    "문화", "거리", "광장", "전시", "아트", "갤러리"
]

# 제외할 키워드 (서비스 범위 외)
EXCLUDE_KEYWORDS = [
    "뮤지컬", "연극", "오페라", "클래식", "국악", "무용", "발레",
    "콘서트", "공연장", "정기연주", "오케스트라", "합창", "영화상영"
]

# 광주 5개 구
GWANGJU_DISTRICTS = ["동구", "서구", "남구", "북구", "광산구"]

# 출력 경로
OUTPUT_DIR = Path(__file__).resolve().parent.parent / "data"
OUTPUT_FILE = OUTPUT_DIR / "scraped_events.json"


@dataclass
class ScrapedEvent:
    """스크래핑된 행사 데이터"""
    event_id: str
    title: str
    description: str
    location: str
    address: str
    district: str  # 광주 구 (동구, 서구 등)
    start_date: str
    end_date: str
    image_url: str
    detail_url: str
    is_free: bool
    status: str  # D-N, 진행중, 종료
    source: str  # festival, exhibition
    scraped_at: str


class SSLAdapter(HTTPAdapter):
    """SSL 보안 수준을 낮춘 어댑터 (DH_KEY_TOO_SMALL 오류 우회)"""
    def init_poolmanager(self, *args, **kwargs):
        ctx = ssl.create_default_context()
        ctx.set_ciphers("DEFAULT@SECLEVEL=1")
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        kwargs["ssl_context"] = ctx
        return super().init_poolmanager(*args, **kwargs)


def get_session() -> requests.Session:
    """HTTP 세션 생성 (SSL 보안 수준 낮춤)"""
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
    })
    # SSL 어댑터 적용
    session.mount("https://", SSLAdapter())
    session.verify = False
    return session


def should_include_event(title: str, description: str = "") -> bool:
    """이벤트가 포함 대상인지 확인"""
    text = f"{title} {description}".lower()
    
    # 제외 키워드가 있으면 제외
    for keyword in EXCLUDE_KEYWORDS:
        if keyword.lower() in text:
            return False
    
    # 포함 키워드가 있으면 포함
    for keyword in INCLUDE_KEYWORDS:
        if keyword.lower() in text:
            return True
    
    return False


def extract_district(address: str, location: str) -> str:
    """주소에서 광주 구 추출"""
    text = f"{address} {location}"
    for district in GWANGJU_DISTRICTS:
        if district in text:
            return district
    return "동구"  # 기본값


def parse_date_range(date_text: str) -> tuple[str, str]:
    """날짜 문자열 파싱 (예: 2025-12-24 (수) ~ 2025-12-25 (목))"""
    # 날짜 패턴 매칭
    date_pattern = r"(\d{4}-\d{2}-\d{2})"
    matches = re.findall(date_pattern, date_text)
    
    if len(matches) >= 2:
        return matches[0], matches[1]
    elif len(matches) == 1:
        return matches[0], matches[0]
    else:
        # 기본값: 오늘부터 7일
        today = datetime.now().strftime("%Y-%m-%d")
        return today, today


def scrape_event_list(session: requests.Session, url: str, source: str, max_pages: int = 5) -> list[dict]:
    """이벤트 목록 페이지 스크래핑"""
    events = []
    seen_ids = set()
    
    for page in range(1, max_pages + 1):
        page_url = f"{url}&page={page}"
        print(f"[{source}] 페이지 {page} 스크래핑 중...")
        
        try:
            resp = session.get(page_url, timeout=30)
            resp.raise_for_status()
        except requests.RequestException as e:
            print(f"  오류: {e}")
            continue
        
        soup = BeautifulSoup(resp.text, "html.parser")
        
        # wr_id 링크를 직접 찾기
        links = soup.select("a[href*='wr_id=']")
        
        if not links:
            print(f"  이벤트 링크를 찾을 수 없음")
            break
        
        page_events = 0
        for link in links:
            try:
                href = link.get("href", "")
                detail_url = urljoin(BASE_URL, href)
                
                # 이벤트 ID 추출
                event_id_match = re.search(r"wr_id=(\d+)", href)
                if not event_id_match:
                    continue
                event_id = event_id_match.group(1)
                
                # 중복 체크
                if event_id in seen_ids:
                    continue
                seen_ids.add(event_id)
                
                # 제목 추출 (링크 텍스트에서)
                title_text = link.get_text(strip=True)
                # "D-25", "무료", "상세보기" 등 제거
                title = re.sub(r"^(D-\d+|오늘|진행중|종료|무료)\s*", "", title_text)
                title = re.sub(r"\s*상세보기$", "", title)
                title = title.strip()
                
                if not title or len(title) < 3:
                    continue
                
                # 포함 대상인지 확인
                if not should_include_event(title):
                    continue
                
                # 부모 요소에서 추가 정보 추출
                parent = link.find_parent("div") or link.find_parent("li")
                
                # 이미지 URL
                img_tag = parent.select_one("img") if parent else None
                image_url = ""
                if img_tag:
                    image_url = img_tag.get("src", "") or img_tag.get("data-src", "")
                    if image_url and not image_url.startswith("http"):
                        image_url = urljoin(BASE_URL, image_url)
                
                # 상태 추출
                status = "진행중"
                if "D-" in title_text:
                    status_match = re.search(r"D-(\d+)", title_text)
                    if status_match:
                        status = f"D-{status_match.group(1)}"
                elif "오늘" in title_text:
                    status = "오늘"
                elif "종료" in title_text:
                    status = "종료"
                elif "진행중" in title_text:
                    status = "진행중"
                
                # 무료 여부
                is_free = "무료" in title_text
                
                # 날짜 및 장소 (부모에서 찾기)
                date_text = ""
                location = ""
                if parent:
                    # 날짜 패턴 찾기
                    parent_text = parent.get_text()
                    date_match = re.search(r"(\d{4}-\d{2}-\d{2}[^~]*(?:~[^)]+)?)", parent_text)
                    if date_match:
                        date_text = date_match.group(1)
                    
                    # 장소 (광주 관련 텍스트)
                    loc_match = re.search(r"(광주[^\n]{5,50}|국립아시아문화전당[^\n]{0,30}|충장로[^\n]{0,30})", parent_text)
                    if loc_match:
                        location = loc_match.group(1).strip()
                
                start_date, end_date = parse_date_range(date_text)
                district = extract_district(location, title)
                
                events.append({
                    "event_id": f"{source}-{event_id}",
                    "title": title,
                    "description": "",
                    "location": location,
                    "address": f"광주광역시 {district}",
                    "district": district,
                    "start_date": start_date,
                    "end_date": end_date,
                    "image_url": image_url,
                    "detail_url": detail_url,
                    "is_free": is_free,
                    "status": status,
                    "source": source,
                })
                page_events += 1
                
            except Exception as e:
                print(f"  파싱 오류: {e}")
                continue
        
        print(f"  수집: {page_events}건")
        
        # 요청 간 딜레이
        time.sleep(1)
    
    return events


def scrape_event_detail(session: requests.Session, event: dict) -> dict:
    """이벤트 상세 페이지에서 추가 정보 수집"""
    try:
        resp = session.get(event["detail_url"], timeout=30)
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"  상세 페이지 오류: {e}")
        return event
    
    soup = BeautifulSoup(resp.text, "html.parser")
    
    # 설명 추출
    content_div = soup.select_one("div.bo_content, div.view_content")
    if content_div:
        # HTML 태그 제거하고 텍스트만 추출
        description = content_div.get_text(separator=" ", strip=True)
        # 너무 긴 설명은 자르기
        if len(description) > 500:
            description = description[:500] + "..."
        event["description"] = description
    
    # 더 정확한 이미지 URL 추출
    main_img = soup.select_one("div.bo_content img, div.view_content img")
    if main_img:
        img_src = main_img.get("src", "")
        if img_src and not img_src.startswith("http"):
            img_src = urljoin(BASE_URL, img_src)
        if img_src:
            event["image_url"] = img_src
    
    # 더 정확한 장소 정보
    info_items = soup.select("div.view_info li, table.view_info td")
    for item in info_items:
        text = item.get_text(strip=True)
        if "장소" in text or "위치" in text:
            # "장소:" 또는 "위치:" 뒤의 텍스트 추출
            location_match = re.search(r"[장소위치]\s*[:\s]\s*(.+)", text)
            if location_match:
                event["location"] = location_match.group(1).strip()
                event["district"] = extract_district(event["location"], event["title"])
                event["address"] = f"광주광역시 {event['district']} {event['location']}"
    
    return event


def scrape_all_events() -> list[ScrapedEvent]:
    """모든 이벤트 스크래핑"""
    session = get_session()
    all_events = []
    
    # 행사/축제 카테고리
    print("\n=== 행사/축제 카테고리 스크래핑 ===")
    festival_events = scrape_event_list(session, FESTIVAL_URL, "festival", max_pages=10)
    print(f"수집된 행사/축제: {len(festival_events)}건")
    all_events.extend(festival_events)
    
    # 전시 카테고리
    print("\n=== 전시 카테고리 스크래핑 ===")
    exhibition_events = scrape_event_list(session, EXHIBITION_URL, "exhibition", max_pages=5)
    print(f"수집된 전시: {len(exhibition_events)}건")
    all_events.extend(exhibition_events)
    
    # 상세 페이지 스크래핑 (상위 50개만)
    print(f"\n=== 상세 정보 수집 (상위 {min(50, len(all_events))}개) ===")
    for i, event in enumerate(all_events[:50]):
        print(f"[{i+1}/{min(50, len(all_events))}] {event['title'][:30]}...")
        event = scrape_event_detail(session, event)
        time.sleep(0.5)  # 요청 간 딜레이
    
    # ScrapedEvent 객체로 변환
    scraped_at = datetime.now().isoformat()
    result = []
    for event in all_events:
        result.append(ScrapedEvent(
            event_id=event["event_id"],
            title=event["title"],
            description=event.get("description", ""),
            location=event["location"],
            address=event["address"],
            district=event["district"],
            start_date=event["start_date"],
            end_date=event["end_date"],
            image_url=event["image_url"],
            detail_url=event["detail_url"],
            is_free=event["is_free"],
            status=event["status"],
            source=event["source"],
            scraped_at=scraped_at,
        ))
    
    return result


def save_events(events: list[ScrapedEvent], output_path: Path) -> None:
    """이벤트 데이터 저장"""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    data = [asdict(e) for e in events]
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    
    print(f"\n저장 완료: {output_path}")
    print(f"총 {len(events)}개 이벤트 수집됨")


def main():
    """메인 함수"""
    print("=" * 60)
    print("playgwangju.co.kr 스크래핑 시작")
    print("=" * 60)
    
    events = scrape_all_events()
    
    if events:
        save_events(events, OUTPUT_FILE)
        
        # 통계 출력
        print("\n=== 수집 통계 ===")
        districts = {}
        for e in events:
            districts[e.district] = districts.get(e.district, 0) + 1
        
        for district, count in sorted(districts.items()):
            print(f"  {district}: {count}건")
        
        free_count = sum(1 for e in events if e.is_free)
        print(f"  무료 행사: {free_count}건")
    else:
        print("수집된 이벤트가 없습니다.")


if __name__ == "__main__":
    main()

