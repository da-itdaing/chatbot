# 🏗️ Itdaing 인프라 설정 문서 (2025-12-03)

## 📋 개요

12/10 발표일 200명 동시 접속 대비를 위한 인프라 구성 작업 내역.

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
| Launch Template | lt-01f9a920f20edddfa (itdaing-chatbot-lt) |
| AMI | ami-0473d782d15e59980 |
| Min/Max/Desired | 1 / 3 / 1 |
| 스케일링 정책 | CPU 60% Target Tracking |

#### Spring ASG (⏸️ 일시 중지)
| 항목 | 값 |
|------|-----|
| 이름 | itdaing-spring-asg |
| Launch Template | lt-0349ad778d742625d (itdaing-spring-lt) |
| AMI | ami-045db7c1e23eb9269 (⚠️ 문제 있음) |
| Min/Max/Desired | 0 / 4 / 0 |
| 상태 | **일시 중지** (AMI 재생성 필요) |

---

## ⏳ 발표 전 해야 할 작업

### 1. Spring AMI 재생성

**문제:** AMI 생성 시 `/var/www/html` 삭제로 Nginx 오류 발생

**해결 방법:**
```bash
# 1. 기존 Spring 서버에서 수정
ssh ubuntu@<spring-server-ip>

# 2. /var/www/html 생성 및 기본 파일 추가
sudo mkdir -p /var/www/html
echo "OK" | sudo tee /var/www/html/index.html

# 3. Nginx default 설정에 /actuator/health 추가
# /etc/nginx/sites-enabled/default 수정

# 4. 새 AMI 생성
aws ec2 create-image \
  --instance-id i-0f3c3ae4ce27bb373 \
  --name "itdaing-spring-ami-$(date +%Y%m%d%H%M)" \
  --description "Fixed Spring Boot AMI" \
  --no-reboot

# 5. Launch Template 업데이트
aws ec2 create-launch-template-version \
  --launch-template-id lt-0349ad778d742625d \
  --source-version 2 \
  --launch-template-data '{"ImageId": "<NEW_AMI_ID>"}'

# 6. Spring ASG 다시 활성화
aws autoscaling update-auto-scaling-group \
  --auto-scaling-group-name itdaing-spring-asg \
  --min-size 1 \
  --desired-capacity 1
```

### 2. 부하 테스트

**도구:** Locust 또는 k6

```bash
# Locust 설치
pip install locust

# 테스트 스크립트 작성 (locustfile.py)
# 200명 동시 접속 시뮬레이션
locust -f locustfile.py --host=https://aischool.daitdaing.com
```

---

## 📊 현재 인프라 구성도

```
사용자 → Route53 (aischool.daitdaing.com)
       → CloudFront (E3V0JILQTE8I63)
         ├── /* (정적) → S3 (daitdaing-frontend-prod)
         └── /api/*, /ai/* → ALB (aischool-bastion-alb)
                              ├── /ai/* → chatbot-tg
                              │           └── 챗봇 ASG (1~3대)
                              └── default → private-tg
                                            └── Spring 서버 (1대, ASG 중지)
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
| 챗봇 Launch Template | lt-01f9a920f20edddfa |
| Spring Launch Template | lt-0349ad778d742625d |
| 챗봇 AMI | ami-0473d782d15e59980 |
| Spring AMI | ami-045db7c1e23eb9269 (⚠️ 문제) |
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

## 📅 작업 일시

- **2025-12-03 15:30~17:00 UTC**
- 작성자: AI Assistant

