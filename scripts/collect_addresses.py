"""
도로명 주소 수집 스크립트
1. mainhome_flea.csv에서 45개 매칭
2. 매칭 안 된 14개는 Nominatim 역지오코딩
"""
import asyncio
import asyncpg
import csv
import json
import os
import re
import time
import requests

def normalize(s: str) -> str:
    """정규화: 공백/특수문자 제거, 소문자"""
    if not s:
        return ""
    return re.sub(r'[\s\-_&·]', '', s.lower())

def extract_coords(geometry_data: str) -> tuple:
    """GeoJSON에서 중심점 좌표 추출"""
    if not geometry_data:
        return None, None
    try:
        geo = json.loads(geometry_data)
        coords = geo.get('coordinates', [])
        if geo.get('type') == 'Point':
            return coords[1], coords[0]  # lat, lng
        elif geo.get('type') == 'Polygon' and coords:
            ring = coords[0]
            lats = [c[1] for c in ring]
            lngs = [c[0] for c in ring]
            return sum(lats)/len(lats), sum(lngs)/len(lngs)
    except:
        pass
    return None, None

def reverse_geocode_nominatim(lat: float, lng: float) -> str:
    """Nominatim API로 역지오코딩 (무료, 1초 딜레이 필요)"""
    try:
        url = f"https://nominatim.openstreetmap.org/reverse"
        params = {
            "lat": lat,
            "lon": lng,
            "format": "json",
            "addressdetails": 1,
            "accept-language": "ko"
        }
        headers = {"User-Agent": "ItdaingChatbot/1.0"}
        response = requests.get(url, params=params, headers=headers, timeout=10)
        if response.status_code == 200:
            data = response.json()
            return data.get("display_name", "")
    except Exception as e:
        print(f"  역지오코딩 실패: {e}")
    return ""

async def collect_addresses():
    # 1. CSV 로드
    csv_data = {}
    with open('/home/ubuntu/mainhome_flea.csv', 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            title_norm = normalize(row['제목'])
            csv_data[title_norm] = {
                'title': row['제목'],
                'address': row['주소'],
                'lat': float(row['위도']),
                'lng': float(row['경도'])
            }
    
    print(f"CSV 데이터: {len(csv_data)}개")
    
    # 2. DB 연결
    conn_str = os.environ.get("PGVECTOR_CONNECTION").replace("postgresql+psycopg://", "postgresql://")
    conn = await asyncpg.connect(conn_str)
    
    try:
        # 3. DB 팝업 조회
        db_popups = await conn.fetch("""
            SELECT p.id, p.name, zc.id as cell_id, zc.detailed_address, zc.geometry_data
            FROM popup p
            LEFT JOIN zone_cell zc ON p.zone_cell_id = zc.id
            WHERE p.approval_status = 'APPROVED'
            ORDER BY p.id
        """)
        
        print(f"DB 팝업: {len(db_popups)}개")
        
        # 4. 매칭 및 주소 수집
        address_updates = []  # [(cell_id, new_address, popup_id, popup_name)]
        
        for db_popup in db_popups:
            popup_id = db_popup['id']
            popup_name = db_popup['name']
            cell_id = db_popup['cell_id']
            db_norm = normalize(popup_name)
            
            # CSV 매칭 시도
            matched = False
            matched_address = None
            
            for csv_norm, csv_item in csv_data.items():
                if csv_norm in db_norm or db_norm in csv_norm or csv_norm == db_norm:
                    matched = True
                    matched_address = csv_item['address']
                    break
            
            if matched and matched_address:
                address_updates.append({
                    'cell_id': cell_id,
                    'popup_id': popup_id,
                    'popup_name': popup_name,
                    'new_address': matched_address,
                    'source': 'csv'
                })
            else:
                # 역지오코딩 시도
                lat, lng = extract_coords(db_popup['geometry_data'])
                if lat and lng:
                    print(f"  역지오코딩: {popup_name} ({lat:.6f}, {lng:.6f})")
                    time.sleep(1.1)  # Nominatim 사용 정책 (1초 딜레이)
                    address = reverse_geocode_nominatim(lat, lng)
                    if address:
                        # 한국 주소 포맷으로 정리
                        address_parts = address.split(', ')
                        # 역순으로 (대한민국, 광주광역시, 구, 도로명 순)
                        kr_address = ", ".join(reversed(address_parts[:5])) if len(address_parts) > 5 else address
                        address_updates.append({
                            'cell_id': cell_id,
                            'popup_id': popup_id,
                            'popup_name': popup_name,
                            'new_address': kr_address,
                            'source': 'nominatim'
                        })
                    else:
                        print(f"  ⚠️ 역지오코딩 실패: {popup_name}")
                else:
                    print(f"  ⚠️ 좌표 없음: {popup_name}")
        
        # 5. 결과 저장
        output_file = '/home/ubuntu/scripts/address_updates.json'
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(address_updates, f, ensure_ascii=False, indent=2)
        
        print(f"\n=== 결과 ===")
        csv_count = len([u for u in address_updates if u['source'] == 'csv'])
        nominatim_count = len([u for u in address_updates if u['source'] == 'nominatim'])
        print(f"CSV 매칭: {csv_count}개")
        print(f"역지오코딩: {nominatim_count}개")
        print(f"총: {len(address_updates)}개")
        print(f"저장: {output_file}")
        
        # 샘플 출력
        print("\n=== 샘플 (처음 5개) ===")
        for item in address_updates[:5]:
            print(f"  [{item['source']}] {item['popup_name'][:30]}: {item['new_address'][:50]}")
        
    finally:
        await conn.close()

if __name__ == "__main__":
    asyncio.run(collect_addresses())
