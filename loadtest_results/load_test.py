"""
다잇다잉 부하 테스트 스크립트 v3
Locust 기반 - 200명 동시 접속 시뮬레이션
"""
from locust import HttpUser, task, between
import random

class ConsumerUser(HttpUser):
    """소비자 시뮬레이션 - 70%"""
    wait_time = between(1, 3)
    weight = 7
    
    @task(5)
    def home_page(self):
        """홈페이지 접속"""
        self.client.get("/", name="GET /")
    
    @task(4)
    def popup_list(self):
        """팝업 목록 조회"""
        page = random.randint(0, 5)
        self.client.get(f"/api/popups?page={page}&size=10", name="GET /api/popups")
    
    @task(1)
    def chatbot_consumer(self):
        """소비자 챗봇"""
        messages = [
            "주말에 갈만한 플리마켓 추천해줘",
            "광주 팝업스토어 알려줘",
            "데이트 코스로 좋은 마켓 있어?"
        ]
        with self.client.post(
            "/ai/api/chat/consumer/async",
            json={
                "user_id": f"loadtest_{random.randint(1,100)}",
                "session_id": "loadtest",
                "message": random.choice(messages),
                "restart_thread": True
            },
            name="POST /ai/chat/consumer",
            catch_response=True,
            timeout=30
        ) as resp:
            if resp.status_code == 200:
                resp.success()
            else:
                resp.failure(f"Status {resp.status_code}")

class SellerUser(HttpUser):
    """판매자 시뮬레이션 - 30%"""
    wait_time = between(2, 5)
    weight = 3
    
    @task(4)
    def home_page(self):
        """홈페이지 접속"""
        self.client.get("/", name="GET /")
    
    @task(3)
    def zones_list(self):
        """존 목록 조회"""
        self.client.get("/api/zones?page=0&size=10", name="GET /api/zones")
    
    @task(1)
    def chatbot_seller(self):
        """판매자 챗봇"""
        messages = [
            "수공예품 판매하기 좋은 존 추천해줘",
            "광주에서 창업하려는데 어디가 좋을까?",
            "주말 마켓 운영 팁 알려줘"
        ]
        with self.client.post(
            "/ai/api/chat/seller/async",
            json={
                "user_id": f"seller_{random.randint(1,50)}",
                "session_id": "loadtest",
                "message": random.choice(messages),
                "restart_thread": True
            },
            name="POST /ai/chat/seller",
            catch_response=True,
            timeout=30
        ) as resp:
            if resp.status_code == 200:
                resp.success()
            else:
                resp.failure(f"Status {resp.status_code}")
