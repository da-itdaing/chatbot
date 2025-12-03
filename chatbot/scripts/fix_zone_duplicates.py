#!/usr/bin/env python3
"""
중복 zone_area 정리 스크립트

문제:
- "광주 동구 금남로 플리마켓존": id=[55, 82, 87, 88] (4개 중복)
- "광주 서구 풍암지구 플리마켓존": id=[58, 81] (2개 중복)

해결:
- 가장 낮은 id만 유지 (55, 58)
- 중복 id 삭제 (81, 82, 87, 88)
- 연결된 zone_cell은 유지되는 zone_area로 재배치
"""

import asyncio
import os
import sys
from pathlib import Path

# 프로젝트 루트 추가
sys.path.insert(0, str(Path(__file__).parent.parent))

import asyncpg
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / "chatbot.env")


async def fix_zone_duplicates():
    """중복 zone_area 정리"""
    
    conn = await asyncpg.connect(
        host=os.environ["POSTGRES_HOST"],
        database=os.environ["POSTGRES_DB"],
        user=os.environ["POSTGRES_USER"],
        password=os.environ["POSTGRES_PASSWORD"],
        port=int(os.environ.get("POSTGRES_PORT", 5432)),
    )
    
    try:
        print("=" * 60)
        print("🔧 중복 zone_area 정리 시작")
        print("=" * 60)
        
        # 1. 중복 확인
        print("\n[1] 현재 중복 zone_area 확인...")
        duplicates = await conn.fetch("""
            SELECT name, array_agg(id ORDER BY id) as ids, COUNT(*) as cnt 
            FROM zone_area 
            GROUP BY name 
            HAVING COUNT(*) > 1
        """)
        
        if not duplicates:
            print("✅ 중복된 zone_area가 없습니다.")
            return
        
        for d in duplicates:
            print(f"  ⚠️ \"{d['name']}\": id={list(d['ids'])} ({d['cnt']}개)")
        
        # 2. 삭제 대상 ID 결정
        ids_to_delete = []
        id_mapping = {}  # old_id -> new_id (재배치용)
        
        for d in duplicates:
            ids = sorted(d["ids"])
            keep_id = ids[0]  # 가장 낮은 id 유지
            delete_ids = ids[1:]  # 나머지 삭제
            
            ids_to_delete.extend(delete_ids)
            for old_id in delete_ids:
                id_mapping[old_id] = keep_id
        
        print(f"\n[2] 삭제 대상 ID: {ids_to_delete}")
        print(f"    재배치 매핑: {id_mapping}")
        
        # 3. zone_cell 재배치
        print("\n[3] zone_cell 재배치...")
        for old_id, new_id in id_mapping.items():
            affected = await conn.fetchval(
                "SELECT COUNT(*) FROM zone_cell WHERE zone_area_id = $1",
                old_id
            )
            if affected > 0:
                await conn.execute(
                    "UPDATE zone_cell SET zone_area_id = $1 WHERE zone_area_id = $2",
                    new_id, old_id
                )
                print(f"    zone_cell {affected}개: zone_area_id {old_id} → {new_id}")
        
        # 4. popup 재배치 (zone_cell을 통해 연결되므로 직접 수정 불필요)
        # popup은 zone_cell_id로 연결되어 있어 zone_cell만 수정하면 됨
        
        # 5. 중복 zone_area 삭제
        print("\n[4] 중복 zone_area 삭제...")
        for zone_id in ids_to_delete:
            # 먼저 연결된 zone_cell이 있는지 확인
            cell_count = await conn.fetchval(
                "SELECT COUNT(*) FROM zone_cell WHERE zone_area_id = $1",
                zone_id
            )
            if cell_count > 0:
                print(f"    ⚠️ zone_area {zone_id}에 아직 {cell_count}개 셀 연결됨, 건너뜀")
                continue
            
            await conn.execute("DELETE FROM zone_area WHERE id = $1", zone_id)
            print(f"    ✅ zone_area id={zone_id} 삭제됨")
        
        # 6. 결과 확인
        print("\n[5] 정리 후 결과 확인...")
        remaining = await conn.fetch("""
            SELECT name, array_agg(id) as ids, COUNT(*) as cnt 
            FROM zone_area 
            GROUP BY name 
            HAVING COUNT(*) > 1
        """)
        
        if remaining:
            print("⚠️ 아직 중복 남음:")
            for r in remaining:
                print(f"    - {r['name']}: {list(r['ids'])}")
        else:
            print("✅ 모든 중복 제거됨!")
        
        # 최종 zone_area 목록
        zones = await conn.fetch("SELECT id, name FROM zone_area ORDER BY id")
        print(f"\n최종 zone_area 목록 ({len(zones)}개):")
        for z in zones:
            print(f"  - id={z['id']}: {z['name']}")
        
        print("\n" + "=" * 60)
        print("✅ 중복 zone_area 정리 완료!")
        print("=" * 60)
        
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(fix_zone_duplicates())


