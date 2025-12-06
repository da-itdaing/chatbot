"""
챗봇 전용 부하 테스트
100명 × 3회 순차 요청
"""
from locust import HttpUser, task, between, events
import random
import time

class ChatbotUser(HttpUser):
    """챗봇만 테스트하는 사용자"""
    wait_time = between(0.5, 1)  # 응답 후 짧은 대기
    
    def on_start(self):
        self.request_count = 0
        self.max_requests = 3
        self.user_id = f"user_{random.randint(1, 10000)}"
    
    @task
    def chatbot_request(self):
        if self.request_count >= self.max_requests:
            # 3회 완료 후 대기
            time.sleep(60)
            return
            
        # Consumer 70%, Seller 30%
        if random.random() < 0.7:
            self._consumer_chat()
        else:
            self._seller_chat()
        
        self.request_count += 1
    
    def _consumer_chat(self):
        messages = [
            "주말에 갈만한 플리마켓 추천해줘",
            "광주 팝업스토어 알려줘",
            "데이트 코스로 좋은 마켓 있어?",
            "가족과 함께 갈 수 있는 마켓 추천",
            "근처에 주차 가능한 플리마켓"
        ]
        with self.client.post(
            "/ai/api/chat/consumer/async",
            json={
                "user_id": self.user_id,
                "session_id": "loadtest",
                "message": random.choice(messages),
                "restart_thread": self.request_count == 0
            },
            name="POST /ai/chat/consumer",
            catch_response=True,
            timeout=60
        ) as resp:
            if resp.status_code == 200:
                resp.success()
            else:
                resp.failure(f"Status {resp.status_code}")
    
    def _seller_chat(self):
        messages = [
            "수공예품 판매하기 좋은 존 추천해줘",
            "광주에서 창업하려는데 어디가 좋을까?",
            "주말 마켓 운영 팁 알려줘",
            "푸드트럭 운영 가능한 곳",
            "인기 있는 판매 카테고리"
        ]
        with self.client.post(
            "/ai/api/chat/seller/async",
            json={
                "user_id": self.user_id,
                "session_id": "loadtest",
                "message": random.choice(messages),
                "restart_thread": self.request_count == 0
            },
            name="POST /ai/chat/seller",
            catch_response=True,
            timeout=60
        ) as resp:
            if resp.status_code == 200:
                resp.success()
            else:
                resp.failure(f"Status {resp.status_code}")
