#!/usr/bin/env python3
"""
DB 데이터 전체 Export 및 문서화 스크립트
- zone_area, zone_cell, popup, popup_image 데이터를 JSON/MD로 정리
"""
import asyncio
import asyncpg
import os
import json
from datetime import datetime, date
from dotenv import load_dotenv

load_dotenv('chatbot.env')

OUTPUT_DIR = "/home/ubuntu/chatbot/data/db_export"

class DateTimeEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, (datetime, date)):
            return obj.isoformat()
        return super().default(obj)


async def export_all_data():
    dsn = os.getenv('PGVECTOR_CONNECTION').replace('postgresql+psycopg://', 'postgresql://')
    conn = await asyncpg.connect(dsn)
    
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    
    print("=" * 60)
    print("📊 DB 데이터 Export 시작")
    print("=" * 60)
    
    # 1. zone_area (존) 전체
    areas = await conn.fetch('''
        SELECT id, region_id, name, max_capacity, status, notice,
               geometry_data, created_at, updated_at
        FROM zone_area
        ORDER BY region_id, id
    ''')
    areas_list = [dict(a) for a in areas]
    with open(f"{OUTPUT_DIR}/zone_areas.json", 'w', encoding='utf-8') as f:
        json.dump(areas_list, f, ensure_ascii=False, indent=2, cls=DateTimeEncoder)
    print(f"✅ zone_area: {len(areas_list)}개 → zone_areas.json")
    
    # 2. zone_cell (셀) 전체
    cells = await conn.fetch('''
        SELECT zc.id, zc.zone_area_id, zc.owner_id, zc.label, zc.detailed_address,
               zc.status, zc.max_capacity, zc.notice, zc.lat, zc.lng,
               zc.geometry_data, zc.created_at, zc.updated_at,
               za.name as area_name
        FROM zone_cell zc
        LEFT JOIN zone_area za ON zc.zone_area_id = za.id
        ORDER BY zc.zone_area_id, zc.id
    ''')
    cells_list = [dict(c) for c in cells]
    with open(f"{OUTPUT_DIR}/zone_cells.json", 'w', encoding='utf-8') as f:
        json.dump(cells_list, f, ensure_ascii=False, indent=2, cls=DateTimeEncoder)
    print(f"✅ zone_cell: {len(cells_list)}개 → zone_cells.json")
    
    # 3. popup 전체
    popups = await conn.fetch('''
        SELECT p.id, p.name, p.description, p.seller_id, p.zone_cell_id,
               p.start_date, p.end_date, p.operating_time, p.approval_status,
               p.created_at, p.updated_at, p.view_count, p.favorite_count,
               zc.label as cell_label, zc.detailed_address,
               za.name as area_name
        FROM popup p
        LEFT JOIN zone_cell zc ON p.zone_cell_id = zc.id
        LEFT JOIN zone_area za ON zc.zone_area_id = za.id
        ORDER BY p.id DESC
    ''')
    popups_list = [dict(p) for p in popups]
    with open(f"{OUTPUT_DIR}/popups.json", 'w', encoding='utf-8') as f:
        json.dump(popups_list, f, ensure_ascii=False, indent=2, cls=DateTimeEncoder)
    print(f"✅ popup: {len(popups_list)}개 → popups.json")
    
    # 4. popup_image 전체
    images = await conn.fetch('''
        SELECT pi.id, pi.popup_id, pi.image_url, pi.is_thumbnail, pi.created_at,
               p.name as popup_name
        FROM popup_image pi
        JOIN popup p ON pi.popup_id = p.id
        ORDER BY pi.popup_id, pi.id
    ''')
    images_list = [dict(i) for i in images]
    with open(f"{OUTPUT_DIR}/popup_images.json", 'w', encoding='utf-8') as f:
        json.dump(images_list, f, ensure_ascii=False, indent=2, cls=DateTimeEncoder)
    print(f"✅ popup_image: {len(images_list)}개 → popup_images.json")
    
    # 5. 통계 및 문서화 (Markdown)
    stats = {
        'exported_at': datetime.now().isoformat(),
        'counts': {
            'zone_area': len(areas_list),
            'zone_cell': len(cells_list),
            'popup': len(popups_list),
            'popup_image': len(images_list),
        }
    }
    
    # 구별 통계
    district_stats = {}
    for area in areas_list:
        region_id = area['region_id']
        district_name = {26: '동구', 27: '서구', 28: '남구', 29: '북구', 30: '광산구'}.get(region_id, f'region_{region_id}')
        if district_name not in district_stats:
            district_stats[district_name] = {'areas': 0, 'cells': 0, 'popups': 0}
        district_stats[district_name]['areas'] += 1
    
    for cell in cells_list:
        area_name = cell.get('area_name', '')
        for d_name in ['동구', '서구', '남구', '북구', '광산구']:
            if d_name in area_name:
                if d_name in district_stats:
                    district_stats[d_name]['cells'] += 1
                break
    
    for popup in popups_list:
        area_name = popup.get('area_name', '')
        for d_name in ['동구', '서구', '남구', '북구', '광산구']:
            if d_name in area_name:
                if d_name in district_stats:
                    district_stats[d_name]['popups'] += 1
                break
    
    stats['by_district'] = district_stats
    
    with open(f"{OUTPUT_DIR}/stats.json", 'w', encoding='utf-8') as f:
        json.dump(stats, f, ensure_ascii=False, indent=2)
    
    # Markdown 문서 생성
    md_content = f"""# 잇다잉 DB 데이터 현황

> Export 일시: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

## 📊 전체 통계

| 테이블 | 개수 |
|--------|------|
| zone_area (존) | {len(areas_list)}개 |
| zone_cell (셀) | {len(cells_list)}개 |
| popup (팝업) | {len(popups_list)}개 |
| popup_image (이미지) | {len(images_list)}개 |

## 🗺️ 구별 통계

| 구 | 존 | 셀 | 팝업 |
|-----|-----|-----|------|
"""
    for d_name in ['동구', '서구', '남구', '북구', '광산구']:
        d = district_stats.get(d_name, {'areas': 0, 'cells': 0, 'popups': 0})
        md_content += f"| {d_name} | {d['areas']}개 | {d['cells']}개 | {d['popups']}개 |\n"
    
    md_content += """
## 🏷️ 존(Zone Area) 목록

"""
    for area in areas_list:
        md_content += f"### {area['id']}. {area['name']}\n"
        md_content += f"- **상태**: {area['status']}\n"
        md_content += f"- **최대 수용량**: {area['max_capacity']}개\n"
        if area['notice']:
            md_content += f"- **공지**: {area['notice']}\n"
        md_content += "\n"
    
    md_content += """
## 📍 셀(Zone Cell) 목록 (샘플 50개)

| ID | 라벨 | 소속 존 | 상태 | 주소 |
|----|------|--------|------|------|
"""
    for cell in cells_list[:50]:
        label = cell['label'] or f"셀#{cell['id']}"
        area = cell['area_name'] or 'N/A'
        addr = cell['detailed_address'] or 'N/A'
        if len(addr) > 30:
            addr = addr[:30] + '...'
        md_content += f"| {cell['id']} | {label} | {area[:20]} | {cell['status']} | {addr} |\n"
    
    md_content += """
## 🎪 팝업 목록 (샘플 50개)

| ID | 이름 | 기간 | 상태 | 설명 길이 |
|----|------|------|------|----------|
"""
    for popup in popups_list[:50]:
        name = popup['name'] or 'N/A'
        if len(name) > 20:
            name = name[:20] + '...'
        start = popup['start_date'].strftime('%m/%d') if popup['start_date'] else 'N/A'
        end = popup['end_date'].strftime('%m/%d') if popup['end_date'] else 'N/A'
        desc_len = len(popup['description']) if popup['description'] else 0
        md_content += f"| {popup['id']} | {name} | {start}~{end} | {popup['approval_status']} | {desc_len}자 |\n"
    
    # 문제점 분석
    no_desc_count = sum(1 for p in popups_list if not p['description'] or len(p['description']) < 50)
    no_img_ids = set([p['id'] for p in popups_list]) - set([i['popup_id'] for i in images_list])
    
    md_content += f"""
## ⚠️ 데이터 품질 이슈

### 설명 부족한 팝업
- **50자 미만 설명**: {no_desc_count}개 (전체의 {no_desc_count * 100 // len(popups_list)}%)

### 이미지 없는 팝업
- **이미지 미등록**: {len(no_img_ids)}개 (전체의 {len(no_img_ids) * 100 // len(popups_list)}%)
- 팝업 ID: {sorted(list(no_img_ids))[:20]}... (상위 20개)

### 셀 형태 이슈
- 현재 대부분의 셀이 **Point(점)** 형태로 저장됨
- 다각형(Polygon) 형태로 변환 필요

"""
    
    with open(f"{OUTPUT_DIR}/DB_현황.md", 'w', encoding='utf-8') as f:
        f.write(md_content)
    print(f"✅ 문서화 완료 → DB_현황.md")
    
    await conn.close()
    print("\n📁 출력 디렉토리:", OUTPUT_DIR)
    
    return stats


if __name__ == "__main__":
    asyncio.run(export_all_data())

