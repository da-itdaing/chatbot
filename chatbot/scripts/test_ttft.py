#!/usr/bin/env python3
"""TTFT(Time To First Token) 측정 테스트 스크립트"""

import asyncio
import time
import httpx


async def test_ttft():
    """TTFT(Time To First Token) 측정 테스트"""
    print("=== TTFT 측정 테스트 ===\n")
    
    url = "http://localhost:9000/api/chat/consumer/async/stream"
    payload = {
        "user_id": "ttft-test",
        "session_id": f"ttft-{int(time.time())}",
        "message": "플리마켓 추천해줘",
        "restart_thread": True
    }
    
    async with httpx.AsyncClient(timeout=120.0) as client:
        start_time = time.time()
        first_token_time = None
        total_tokens = 0
        ttft = 0
        
        try:
            async with client.stream("POST", url, json=payload) as response:
                async for line in response.aiter_lines():
                    if line.strip():
                        current_time = time.time()
                        if first_token_time is None:
                            first_token_time = current_time
                            ttft = first_token_time - start_time
                            print(f"✅ 첫 토큰 도착! TTFT: {ttft:.2f}초")
                            print(f"   첫 청크: {line[:150]}...")
                        total_tokens += 1
        except Exception as e:
            print(f"❌ 에러: {e}")
            return
        
        total_time = time.time() - start_time
        print(f"\n📊 결과:")
        print(f"   TTFT (첫 토큰): {ttft:.2f}초")
        print(f"   총 소요 시간: {total_time:.2f}초")
        print(f"   청크 수: {total_tokens}개")
        
        if total_time > 0:
            print(f"   TTFT / 총 시간: {ttft/total_time*100:.1f}%")
            
            # 개선 효과
            print(f"\n📈 개선 효과:")
            print(f"   이전 방식: TTFT ≈ 총 시간 (100%)")
            print(f"   새 방식: TTFT = {ttft/total_time*100:.1f}%")
            
            if ttft < total_time * 0.5:
                print(f"   ✅ TTFT가 총 시간의 50% 미만 - 체감 속도 크게 개선!")


async def test_simple_query():
    """간단한 질문 테스트 (인사)"""
    print("\n=== 간단한 질문 테스트 (인사) ===\n")
    
    url = "http://localhost:9000/api/chat/consumer/async/stream"
    payload = {
        "user_id": "ttft-test",
        "session_id": f"simple-{int(time.time())}",
        "message": "안녕",
        "restart_thread": True
    }
    
    async with httpx.AsyncClient(timeout=120.0) as client:
        start_time = time.time()
        first_token_time = None
        total_tokens = 0
        ttft = 0
        
        try:
            async with client.stream("POST", url, json=payload) as response:
                async for line in response.aiter_lines():
                    if line.strip():
                        current_time = time.time()
                        if first_token_time is None:
                            first_token_time = current_time
                            ttft = first_token_time - start_time
                            print(f"✅ 첫 토큰 도착! TTFT: {ttft:.2f}초")
                            print(f"   첫 청크: {line[:150]}...")
                        total_tokens += 1
        except Exception as e:
            print(f"❌ 에러: {e}")
            return
        
        total_time = time.time() - start_time
        print(f"\n📊 결과:")
        print(f"   TTFT (첫 토큰): {ttft:.2f}초")
        print(f"   총 소요 시간: {total_time:.2f}초")
        print(f"   청크 수: {total_tokens}개")


async def main():
    # 서버 헬스체크
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            resp = await client.get("http://localhost:9000/health")
            print(f"서버 상태: {resp.status_code}")
        except Exception as e:
            print(f"❌ 서버 연결 실패: {e}")
            print("서버가 실행 중인지 확인하세요.")
            return
    
    await test_ttft()
    await test_simple_query()


if __name__ == "__main__":
    asyncio.run(main())

