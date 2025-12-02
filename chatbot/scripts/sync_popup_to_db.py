#!/usr/bin/env python3
"""
markets_seed.json 데이터를 PostgreSQL popup 테이블에 동기화하는 스크립트.

기존 테이블 구조를 그대로 유지하며, Spring Boot 백엔드 코드 수정 없이
데이터만 삽입합니다.

테이블 구조:
- popup: id, seller_id, zone_cell_id, name, description, start_date, end_date,
         operating_time, approval_status, rejection_reason, view_count,
         created_at, updated_at, favorite_count
- popup_image: id, popup_id, image_url, is_thumbnail, created_at, image_key

실행:
  python scripts/sync_popup_to_db.py --dry-run  # 테스트 실행
  python scripts/sync_popup_to_db.py            # 실제 실행
  python scripts/sync_popup_to_db.py --clear    # 기존 데이터 삭제 후 실행
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

import asyncpg

# 프로젝트 루트 추가
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# RDS 연결 정보 (chatbot.env에서 가져옴)
DB_URL = "postgresql://itdaing_admin:daitdaingpassword@itdaing-db.cl4qagmger70.ap-northeast-2.rds.amazonaws.com:5432/itdaing-db"

# 마켓 데이터 경로
MARKETS_SEED_PATH = Path("/home/ubuntu/markets_seed.json")

# 기본 셀러 ID (기존 셀러 사용)
DEFAULT_SELLER_ID = 3  # seller1 - 어반스타일

# 카테고리 매핑 (markets_seed.json category → DB category)
CATEGORY_MAPPING = {
    "플리마켓": "라이프",
    "야시장": "푸드",
    "축제": "문화",
    "전시": "공연/전시",
    "문화행사": "문화",
    "체험": "라이프",
    "아트": "문화",
    "핸드메이드": "라이프",
    "음식": "푸드",
}


def parse_date(date_str: str | None) -> date | None:
    """날짜 문자열을 date 객체로 변환"""
    if not date_str:
        return None
    try:
        return datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        try:
            return datetime.strptime(date_str, "%Y.%m.%d").date()
        except ValueError:
            return None


def extract_operating_time(hours: Dict[str, Any] | None) -> str | None:
    """운영시간 딕셔너리에서 문자열 추출"""
    if not hours:
        return None
    
    if "weekday" in hours:
        return hours.get("weekday")
    if "open" in hours and "close" in hours:
        return f"{hours['open']}-{hours['close']}"
    
    return None


async def get_zone_cell_for_district(conn: asyncpg.Connection, district: str) -> int:
    """
    구 이름에 해당하는 기존 zone_cell_id를 조회.
    없으면 기본값 반환 (기존 데이터 중 하나 사용)
    """
    # zone_area에서 구 이름으로 검색
    zone_area = await conn.fetchrow(
        "SELECT id FROM zone_area WHERE name LIKE $1 LIMIT 1",
        f"%{district}%"
    )
    
    if zone_area:
        # 해당 zone_area의 zone_cell 찾기
        zone_cell = await conn.fetchrow(
            "SELECT id FROM zone_cell WHERE zone_area_id = $1 LIMIT 1",
            zone_area["id"]
        )
        if zone_cell:
            return zone_cell["id"]
    
    # 없으면 기존 zone_cell 중 하나 반환
    default_cell = await conn.fetchval(
        "SELECT id FROM zone_cell ORDER BY id LIMIT 1"
    )
    return default_cell or 377  # 기본값


async def get_category_id(conn: asyncpg.Connection, category_name: str) -> int:
    """카테고리 이름으로 ID 조회"""
    # 매핑된 카테고리 이름 사용
    mapped_name = CATEGORY_MAPPING.get(category_name, "라이프")
    
    category = await conn.fetchrow(
        "SELECT id FROM category WHERE name = $1 AND type = 'POPUP'",
        mapped_name
    )
    
    if category:
        return category["id"]
    
    # 기본 카테고리 (라이프)
    return 3


async def sync_market_to_popup(
    conn: asyncpg.Connection,
    market: Dict[str, Any],
    seller_id: int,
    dry_run: bool = False,
) -> Optional[int]:
    """단일 마켓 데이터를 popup 테이블에 삽입"""
    
    # markets_seed.json 구조에 맞게 필드명 매핑
    name = market.get("market_name") or market.get("name", "")
    if not name:
        return None
    
    # 날짜 파싱 - event_dates가 딕셔너리 리스트일 수 있음
    event_dates = market.get("event_dates", [])
    start_date = None
    end_date = None
    
    if event_dates:
        # event_dates가 딕셔너리 리스트인 경우 (새 구조)
        if isinstance(event_dates[0], dict):
            first_date = event_dates[0].get("date")
            last_date = event_dates[-1].get("date") if len(event_dates) > 1 else first_date
            start_date = parse_date(first_date)
            end_date = parse_date(last_date)
        else:
            # 문자열 리스트인 경우 (이전 구조)
            start_date = parse_date(event_dates[0])
            end_date = parse_date(event_dates[-1]) if len(event_dates) > 1 else start_date
    
    # 날짜가 없으면 현재 날짜 기준으로 설정
    if not start_date:
        start_date = date.today()
    if not end_date:
        # end_date가 없거나 start_date와 같으면 최소 한 달 뒤로 설정
        end_date = start_date + timedelta(days=30)
    elif end_date == start_date:
        # 당일 종료 이벤트는 최소 한 달 뒤로 연장
        end_date = start_date + timedelta(days=30)
    
    # 운영 시간
    operating_time = extract_operating_time(market.get("operating_hours"))
    if not operating_time:
        operating_time = "10:00-18:00"  # 기본값
    
    # 설명
    description = market.get("market_description") or market.get("description", "")
    if not description:
        description = f"{name} - 광주광역시에서 열리는 플리마켓/팝업스토어입니다."
    
    # 주소에서 구 추출 - market_location 배열에서 가져옴
    address = ""
    locations = market.get("market_location", [])
    if locations and isinstance(locations, list) and len(locations) > 0:
        address = locations[0].get("address", "")
    if not address:
        address = market.get("address", "")
    
    district = "동구"  # 기본값
    for d in ["동구", "서구", "남구", "북구", "광산구"]:
        if d in address:
            district = d
            break
    
    # zone_cell_id 조회
    zone_cell_id = await get_zone_cell_for_district(conn, district)
    
    # 이미지 URL
    image_url = market.get("image_url", "")
    
    if dry_run:
        print(f"  [DRY-RUN] Would insert popup: {name}")
        print(f"    - seller_id: {seller_id}")
        print(f"    - zone_cell_id: {zone_cell_id}")
        print(f"    - start_date: {start_date}")
        print(f"    - end_date: {end_date}")
        print(f"    - operating_time: {operating_time}")
        print(f"    - image_url: {image_url[:50] if image_url else 'None'}...")
        return None
    
    now = datetime.now()
    
    # popup 삽입
    popup_id = await conn.fetchval(
        """
        INSERT INTO popup (
            seller_id, zone_cell_id, name, description,
            start_date, end_date, operating_time,
            approval_status, view_count, favorite_count,
            created_at, updated_at
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7, 'APPROVED', 0, 0, $8, $8)
        RETURNING id
        """,
        seller_id, zone_cell_id, name, description,
        start_date, end_date, operating_time, now
    )
    
    # 이미지 삽입 (image_url이 있는 경우)
    if image_url and popup_id:
        # image_key 생성 (S3 key 형식)
        image_key = f"uploads/popup/{seller_id}/{date.today().strftime('%Y-%m-%d')}/{popup_id}.jpg"
        
        await conn.execute(
            """
            INSERT INTO popup_image (popup_id, image_url, is_thumbnail, created_at, image_key)
            VALUES ($1, $2, true, $3, $4)
            """,
            popup_id, image_url, now, image_key
        )
    
    # 카테고리 연결
    category = market.get("market_category") or market.get("category", "플리마켓")
    category_id = await get_category_id(conn, category)
    
    if popup_id and category_id:
        # popup_category 테이블 구조 확인 후 삽입
        try:
            await conn.execute(
                """
                INSERT INTO popup_category (popup_id, category_id, category_role)
                VALUES ($1, $2, 'POPUP')
                ON CONFLICT DO NOTHING
                """,
                popup_id, category_id
            )
        except Exception as e:
            # 테이블 구조가 다를 수 있으므로 에러 무시
            pass
    
    return popup_id


async def main(dry_run: bool = False, clear_existing: bool = False):
    """메인 동기화 함수"""
    
    print("=" * 60)
    print("PostgreSQL popup 테이블 동기화")
    print("=" * 60)
    
    # 마켓 데이터 로드
    if not MARKETS_SEED_PATH.exists():
        print(f"Error: {MARKETS_SEED_PATH} not found")
        return
    
    with open(MARKETS_SEED_PATH, "r", encoding="utf-8") as f:
        markets = json.load(f)
    
    print(f"로드된 마켓 수: {len(markets)}")
    
    print(f"\nDB 연결 중...")
    
    try:
        conn = await asyncpg.connect(DB_URL, ssl='prefer')
        print("DB 연결 성공")
        
        # 기존 데이터 수 확인
        existing_count = await conn.fetchval("SELECT COUNT(*) FROM popup")
        print(f"기존 popup 레코드 수: {existing_count}")
        
        # 기존 데이터 삭제 (옵션) - 관련 테이블 순서대로 삭제
        if clear_existing and not dry_run:
            print("\n기존 popup 관련 데이터 삭제 중...")
            
            # FK 제약 때문에 순서대로 삭제
            await conn.execute("DELETE FROM popup_image WHERE popup_id IN (SELECT id FROM popup WHERE name LIKE '%플리마켓%' OR name LIKE '%야시장%' OR name LIKE '%축제%' OR name LIKE '%마켓%')")
            await conn.execute("DELETE FROM popup_category WHERE popup_id IN (SELECT id FROM popup WHERE name LIKE '%플리마켓%' OR name LIKE '%야시장%' OR name LIKE '%축제%' OR name LIKE '%마켓%')")
            await conn.execute("DELETE FROM popup_style WHERE popup_id IN (SELECT id FROM popup WHERE name LIKE '%플리마켓%' OR name LIKE '%야시장%' OR name LIKE '%축제%' OR name LIKE '%마켓%')")
            await conn.execute("DELETE FROM popup_feature WHERE popup_id IN (SELECT id FROM popup WHERE name LIKE '%플리마켓%' OR name LIKE '%야시장%' OR name LIKE '%축제%' OR name LIKE '%마켓%')")
            await conn.execute("DELETE FROM wishlist WHERE popup_id IN (SELECT id FROM popup WHERE name LIKE '%플리마켓%' OR name LIKE '%야시장%' OR name LIKE '%축제%' OR name LIKE '%마켓%')")
            await conn.execute("DELETE FROM review WHERE popup_id IN (SELECT id FROM popup WHERE name LIKE '%플리마켓%' OR name LIKE '%야시장%' OR name LIKE '%축제%' OR name LIKE '%마켓%')")
            
            # 플리마켓/야시장/축제 관련 popup만 삭제 (기존 다른 데이터 유지)
            deleted = await conn.execute(
                "DELETE FROM popup WHERE name LIKE '%플리마켓%' OR name LIKE '%야시장%' OR name LIKE '%축제%' OR name LIKE '%마켓%'"
            )
            print(f"플리마켓 관련 popup 삭제 완료")
        
        # 셀러 ID 확인
        seller = await conn.fetchrow(
            "SELECT id, name FROM users WHERE id = $1",
            DEFAULT_SELLER_ID
        )
        if seller:
            print(f"셀러: id={seller['id']}, name={seller['name']}")
        else:
            print(f"Warning: 셀러 ID {DEFAULT_SELLER_ID}를 찾을 수 없음. 기본값 사용")
        
        seller_id = seller["id"] if seller else DEFAULT_SELLER_ID
        
        # 마켓 데이터 동기화
        print(f"\n{'[DRY-RUN] ' if dry_run else ''}마켓 데이터 동기화 중...")
        
        success_count = 0
        error_count = 0
        
        for idx, market in enumerate(markets, 1):
            market_name = market.get('market_name') or market.get('name') or 'Unknown'
            try:
                popup_id = await sync_market_to_popup(
                    conn, market, seller_id, dry_run=dry_run
                )
                if popup_id or dry_run:
                    success_count += 1
                    if not dry_run:
                        print(f"  [{idx}/{len(markets)}] {market_name[:30]}... -> popup_id={popup_id}")
            except Exception as e:
                error_count += 1
                print(f"  [{idx}/{len(markets)}] Error: {market_name[:30]}... - {e}")
        
        print(f"\n{'='*60}")
        print(f"동기화 완료")
        print(f"  성공: {success_count}")
        print(f"  실패: {error_count}")
        print(f"{'='*60}")
        
        # 결과 확인
        if not dry_run:
            final_count = await conn.fetchval("SELECT COUNT(*) FROM popup")
            image_count = await conn.fetchval("SELECT COUNT(*) FROM popup_image")
            print(f"\n현재 popup 테이블 레코드 수: {final_count}")
            print(f"현재 popup_image 테이블 레코드 수: {image_count}")
            
            # 최근 삽입된 데이터 샘플
            print("\n=== 최근 삽입된 popup 샘플 ===")
            recent = await conn.fetch(
                "SELECT id, name, start_date, end_date FROM popup ORDER BY id DESC LIMIT 5"
            )
            for r in recent:
                print(f"  id={r['id']}, name={r['name'][:30]}..., dates={r['start_date']}~{r['end_date']}")
        
        await conn.close()
        
    except Exception as e:
        print(f"DB 오류: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="popup 테이블 동기화")
    parser.add_argument("--dry-run", action="store_true", help="실제 실행하지 않고 테스트만")
    parser.add_argument("--clear", action="store_true", help="기존 플리마켓 관련 데이터 삭제 후 삽입")
    
    args = parser.parse_args()
    
    asyncio.run(main(dry_run=args.dry_run, clear_existing=args.clear))
