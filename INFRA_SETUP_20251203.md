# 🏗️ Itdaing 인프라 설정 문서 (2025-12-03)

## 📋 개요

12/10 발표일 200명 동시 접속 대비를 위한 인프라 구성 작업 내역.

**부하 테스트 결과 파일:** `/home/ubuntu/loadtest_results/`

---

## ✅ 완료된 작업

### 1. S3 + CloudFront 설정 (프론트엔드)

| 항목 | 값 |
|------|-----|
| S3 버킷 | `daitdaing-frontend-prod` |
| CloudFront Distribution ID | `E3V0JILQTE8I63` |
| CloudFront Domain | `d13zy39nisv09l.cloudfront.net` |
| 도메인 | `aischool.daitdaing.com` → CloudFront |
| ACM 인증서 (us-east-1) | `arn:aws:acm:us-east-1:166357011361:certificate/04f5ce99-63c3-4705-9106-0c19b4be594f` |

**CloudFront 라우팅:**
- `/*` (기본) → S3 (정적 파일)
- `/api/*` → ALB → Spring Boot
- `/ai/*` → ALB → 챗봇 서버

**프론트엔드 배포 명령:**
```bash
# 배포 서버에서 실행
cd /home/ubuntu/itdaing-app
git pull origin main
npm run build
aws s3 sync dist/ s3://daitdaing-frontend-prod/ --delete

# CloudFront 캐시 무효화 (필요 시)
aws cloudfront create-invalidation --distribution-id E3V0JILQTE8I63 --paths "/*"
```

---

### 2. RDS 업그레이드

| 항목 | 이전 | 이후 |
|------|------|------|
| 인스턴스 클래스 | db.t3.micro | **db.t3.medium** |
| max_connections | ~80 | **~400** |
| RAM | 1GB | 4GB |

**⚠️ 주의:** RDS 업그레이드 후 모든 서버 재시작 필요 (DB 연결 갱신)

---

### 3. ALB 규칙 설정

**HTTPS 리스너 규칙:**
| 우선순위 | 경로 | Target Group |
|---------|------|--------------|
| 10 | `/ai/*` | chatbot-tg |
| default | 나머지 | private-tg (Spring) |

**HTTP 리스너 규칙:**
| 우선순위 | 경로 | Target Group |
|---------|------|--------------|
| 10 | `/ai/*` | chatbot-tg |
| default | 나머지 | private-tg (Spring) |

---

### 4. 챗봇 서버 구성

#### Target Group
- **이름:** chatbot-tg
- **ARN:** `arn:aws:elasticloadbalancing:ap-northeast-2:166357011361:targetgroup/chatbot-tg/af56bc0bdf58096a`
- **포트:** 9000
- **헬스체크:** `/health`

#### Nginx 설정 (챗봇 서버)
```nginx
# /etc/nginx/sites-enabled/chatbot
server {
    listen 9000;
    server_name _;

    location = /health {
        proxy_pass http://127.0.0.1:9001/health;
    }

    location /ai/ {
        proxy_pass http://127.0.0.1:9001/;
        # SSE/Streaming 설정...
    }

    location / {
        proxy_pass http://127.0.0.1:9001/;
        # SSE/Streaming 설정...
    }
}
```

#### systemd 서비스
```ini
# /etc/systemd/system/chatbot.service
[Unit]
Description=Itdaing Chatbot FastAPI Application
After=network.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/chatbot
Environment="PATH=/home/ubuntu/chatbot/.venv/bin"
ExecStart=/home/ubuntu/chatbot/.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 9001
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

#### Security Group 규칙 추가
- `sg-0bc40953c3ef23c99` (private-ec2-sg)에 ALB SG로부터 9000 포트 허용

---

### 5. Auto Scaling Group

#### 챗봇 ASG (✅ 작동 중)
| 항목 | 값 |
|------|-----|
| 이름 | itdaing-chatbot-asg |
| Launch Template | lt-01f9a920f20edddfa v4 |
| AMI | ami-04e2c5aea47b94efb (itdaing-chatbot-ami-202512031807) |
| Min/Max/Desired | 1 / 3 / 1 |
| 스케일링 정책 | CPU 60% Target Tracking |

#### Spring ASG (✅ 활성화됨)
| 항목 | 값 |
|------|-----|
| 이름 | itdaing-spring-asg |
| Launch Template | lt-0349ad778d742625d v4 |
| AMI | ami-0f3ec82f44f09f1e4 (itdaing-spring-ami-202512031815-fixed) |
| Min/Max/Desired | 1 / 4 / 1 |
| 상태 | **활성화됨** (Nginx 문제 해결됨) |

---

## ⏳ 발표 전 해야 할 작업

### ~~1. Spring AMI 재생성~~ ✅ 완료

- **해결됨:** ami-0f3ec82f44f09f1e4 (itdaing-spring-ami-202512031815-fixed)
- Nginx 설정 수정 완료 (`Connection '''` → `Connection ''`)
- Launch Template v4로 업데이트됨

### ~~2. 부하 테스트~~ ✅ 완료

- 테스트 결과: `/home/ubuntu/loadtest_results/`
- 상세 결과는 "10. 부하 테스트 결과" 섹션 참조

---

## 📊 현재 인프라 구성도

```
사용자 → Route53 (aischool.daitdaing.com)
       → CloudFront (E3V0JILQTE8I63)
         ├── /* (정적) → S3 (daitdaing-frontend-prod)
         └── /api/*, /ai/* → ALB (aischool-bastion-alb)
                              ├── /ai/* → chatbot-tg
                              │           ├── hj-chatbot-ec2 (원본)
                              │           └── itdaing-chatbot-asg (1~3대)
                              └── default → private-tg
                                            ├── itdaing-service-ec2 (원본)
                                            └── itdaing-spring-asg (1~4대)
```

---

## 🔑 주요 리소스 ARN

| 리소스 | ARN / ID |
|-------|----------|
| CloudFront | E3V0JILQTE8I63 |
| S3 버킷 | daitdaing-frontend-prod |
| ALB | aischool-bastion-alb |
| 챗봇 TG | arn:aws:elasticloadbalancing:ap-northeast-2:166357011361:targetgroup/chatbot-tg/af56bc0bdf58096a |
| Spring TG | arn:aws:elasticloadbalancing:ap-northeast-2:166357011361:targetgroup/private-tg/e29ff30cb7c93b23 |
| 챗봇 ASG | itdaing-chatbot-asg |
| Spring ASG | itdaing-spring-asg |
| 챗봇 Launch Template | lt-01f9a920f20edddfa (v4) |
| Spring Launch Template | lt-0349ad778d742625d (v4) |
| 챗봇 AMI | ami-04e2c5aea47b94efb ✅ |
| Spring AMI | ami-0f3ec82f44f09f1e4 ✅ |
| RDS | itdaing-db (db.t3.medium) |

---

## 🚨 트러블슈팅

### RDS 업그레이드 후 연결 오류
```
psycopg.OperationalError: the connection is closed
```
**해결:** 모든 서버(챗봇, Spring) 재시작

### Spring ASG 인스턴스 헬스 체크 실패
```
rewrite or internal redirection cycle while internally redirecting to "/index.html"
```
**원인:** `/var/www/html` 디렉토리 없음
**해결:** 디렉토리 생성 또는 AMI 재생성

---

## 🖥️ EC2 인스턴스 현황 (2025-12-03 업데이트)

### 운영 중 (유지 필수)

| 이름 | Instance ID | Type | Private IP | 역할 | Target Group |
|------|-------------|------|------------|------|--------------|
| bastion-ec2-2 | i-02e2b0f805000e241 | t3.medium | 10.0.0.214 | Bastion 접근용 | - |
| hj-chatbot-ec2 | i-0dcb1780b49300e0e | m5.large | 10.0.150.137 | 챗봇 개발/운영 | chatbot-tg ✅ |
| itdaing-service-ec2 | i-0f3c3ae4ce27bb373 | m5.large | 10.0.145.136 | Spring 배포 | private-tg ✅ |
| itdaing-chatbot-asg | i-0c3572f8fadc34c56 | m5.large | 10.0.145.228 | ASG 챗봇 | chatbot-tg ✅ |

### 정리 대상 (확인 필요)

| 이름 | Instance ID | Type | 상태 | 비고 |
|------|-------------|------|------|------|
| 11-29-cr | i-06707a55733b66fe3 | m5.large | **running** | 테스트? (비용 발생) |
| 11-29-hj | i-0e910e5371d8ae13a | m5.large | **running** | 테스트? (비용 발생) |
| hj-chatbot-ec2-2 | i-09cf8f36f0d5055e3 | t3.large | stopped | 이전 버전 |
| 1125-EC2-cr | i-03de3b2d4ad079919 | m5.large | stopped | 테스트 |
| 11-29-jc | i-0de01d6497be6700a | m5.large | stopped | 테스트 |

**⚠️ 정리 권장:** running 상태 테스트 인스턴스 (m5.large × 2) 비용 절감 필요

---

## 📅 작업 일시

- **2025-12-03 15:30~17:00 UTC** - 초기 인프라 구성 (S3, CloudFront, ALB)
- **2025-12-03 17:40 UTC** - EC2 현황 업데이트, RDS 업그레이드
- **2025-12-03 18:06 UTC** - Spring AMI 생성 (ami-00d904d2bdd860056)
- **2025-12-03 18:07 UTC** - Chatbot AMI 생성 (ami-04e2c5aea47b94efb)
- **2025-12-03 18:15 UTC** - Spring AMI 재생성 (ami-0f3ec82f44f09f1e4, Nginx 수정)
- **2025-12-03 18:20~18:50 UTC** - 부하 테스트 실행 (50명, 200명, 100명×3회)


---

## 10. 부하 테스트 결과 (2025-12-03 18:20~18:50 UTC)

### 테스트 환경
- **도구:** Locust 2.42.6
- **대상:** https://aischool.daitdaing.com
- **시나리오:** Consumer 70%, Seller 30%
- **결과 파일:** `/home/ubuntu/loadtest_results/`

### 테스트 1: 50명 동시접속 (90초)

| 엔드포인트 | 요청수 | 실패 | 평균 응답 | P95 | P99 |
|-----------|-------|------|----------|-----|-----|
| GET / | 605 | 0 | 4ms | 5ms | 25ms |
| GET /api/popups | 328 | 0 | 59ms | 120ms | 290ms |
| GET /api/zones | 67 | 0 | 22ms | 52ms | 75ms |
| POST /ai/chat/consumer | 76 | 0 | 8.9s | 12s | 13s |
| POST /ai/chat/seller | 33 | 0 | 17.4s | 23s | 25s |

**결과:** ✅ 50명 동시접속은 문제 없음

### 테스트 2: 200명 동시접속 (120초)

| 엔드포인트 | 요청수 | 실패율 | 평균 응답 | P95 |
|-----------|-------|-------|----------|-----|
| GET / | 2,544 | 0% | 7ms | 23ms |
| GET /api/popups | 1,619 | 0% | 54ms | 100ms |
| GET /api/zones | 423 | 0% | 20ms | 44ms |
| POST /ai/chat/consumer | 344 | 82.6% | 21.7s | 30s (timeout) |
| POST /ai/chat/seller | 124 | 90.3% | 22.3s | 30s (timeout) |

**결과:** 
- ✅ 정적 콘텐츠/Spring API: 200명 동시접속 충분히 처리 가능
- ⚠️ 챗봇 API: ALB 30초 타임아웃으로 대부분 실패

### 테스트 3: 챗봇 전용 - 100명 × 3회 순차 요청

#### 서버 1대 운영 시
| 챗봇 | 요청 | 성공 | 실패 | 실패율 | 평균 응답 |
|------|-----|------|------|-------|----------|
| Consumer | 209 | 71 | 138 | 66.0% | 23.1s |
| Seller | 91 | 10 | 81 | 89.0% | 27.6s |
| **전체** | **300** | **81** | **219** | **73.0%** | 24.5s |

#### 서버 3대 운영 시 (원본1 + ASG2)
| 챗봇 | 요청 | 성공 | 실패 | 실패율 | 평균 응답 |
|------|-----|------|------|-------|----------|
| Consumer | 216 | 178 | 38 | 17.6% | 19.4s |
| Seller | 84 | 25 | 59 | 70.2% | 27.8s |
| **전체** | **300** | **203** | **97** | **32.3%** | 21.8s |

#### 서버 증설 효과
| 지표 | 1대 | 3대 | 개선 |
|-----|-----|-----|------|
| Consumer 성공률 | 34% | 82.4% | **↑48.4%p** |
| Seller 성공률 | 11% | 29.8% | **↑18.8%p** |
| 전체 성공률 | 27% | 67.7% | **↑40.7%p** |

### 분석 및 결론

**✅ 문제 없음:**
- CloudFront + S3 (정적 콘텐츠): 무제한 확장
- Spring API: 200명 동시접속 안정적

**⚠️ 병목 구간:**
- 챗봇 서버: OpenAI API 응답 시간 (10-25초)
- ALB 타임아웃: 30초 (기본값)
- 동시 LLM 요청 제한

### 발표일 체크리스트

```bash
# ✅ 발표 30분 전 실행
aws autoscaling update-auto-scaling-group \
  --auto-scaling-group-name itdaing-chatbot-asg \
  --desired-capacity 2

# ✅ 발표 종료 후 실행
aws autoscaling update-auto-scaling-group \
  --auto-scaling-group-name itdaing-chatbot-asg \
  --desired-capacity 1
```

### 테스트 파일 목록

```
/home/ubuntu/loadtest_results/
├── load_test.py                          # 일반 부하 테스트 스크립트
├── load_test_chatbot.py                  # 챗봇 전용 테스트 스크립트
├── loadtest_50users_*.csv                # 50명 테스트 결과
├── loadtest_200users_*.csv               # 200명 테스트 결과
├── loadtest_chatbot_100x3_*.csv          # 챗봇 1대 테스트 결과
└── loadtest_chatbot_2instances_*.csv     # 챗봇 3대 테스트 결과
```
