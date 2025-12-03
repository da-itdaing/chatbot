# 🚀 Itdaing 서비스 배포 가이드

## 📋 개요

프론트엔드, 백엔드(Spring), AI 서비스(챗봇)의 배포 방법을 설명합니다.

---

## 1. 프론트엔드 (React + Vite)

### 저장소
- **GitHub:** `da-itdaing/sub-repo` → `itdaing-app/`
- **배포 위치:** S3 (`daitdaing-frontend-prod`) → CloudFront

### 배포 방법

```bash
# 배포 서버 (itdaing-service-ec2)에서 실행
cd /home/ubuntu/itdaing-app

# 1. 최신 코드 가져오기
git pull origin main

# 2. 빌드
npm run build

# 3. S3에 업로드
aws s3 sync dist/ s3://daitdaing-frontend-prod/ --delete

# 4. CloudFront 캐시 무효화 (선택, 즉시 반영 필요 시)
aws cloudfront create-invalidation \
  --distribution-id E3V0JILQTE8I63 \
  --paths "/*"
```

### 원라인 배포 스크립트
```bash
cd /home/ubuntu/itdaing-app && \
git pull origin main && \
npm run build && \
aws s3 sync dist/ s3://daitdaing-frontend-prod/ --delete && \
aws cloudfront create-invalidation --distribution-id E3V0JILQTE8I63 --paths "/*"
```

### 롤백
```bash
# 이전 커밋으로 롤백
cd /home/ubuntu/itdaing-app
git checkout <previous-commit-hash>
npm run build
aws s3 sync dist/ s3://daitdaing-frontend-prod/ --delete
```

---

## 2. 백엔드 (Spring Boot)

### 저장소
- **GitHub:** `da-itdaing/itdaing` (또는 별도 백엔드 저장소)
- **배포 위치:** EC2 (`itdaing-service-ec2`)

### 배포 방법

```bash
# 배포 서버 (itdaing-service-ec2)에서 실행
cd /home/ubuntu/itdaing

# 1. 최신 코드 가져오기
git pull origin main

# 2. 빌드 (Gradle)
./gradlew bootJar

# 3. JAR 파일 복사 (필요 시)
cp build/libs/*.jar app.jar

# 4. 서비스 재시작
sudo systemctl restart itdaing-backend
```

### 원라인 배포 스크립트
```bash
cd /home/ubuntu/itdaing && \
git pull origin main && \
./gradlew bootJar && \
cp build/libs/*.jar app.jar && \
sudo systemctl restart itdaing-backend
```

### 롤백
```bash
# 이전 JAR로 롤백 (백업 필요)
cd /home/ubuntu/itdaing
git checkout <previous-commit-hash>
./gradlew bootJar
cp build/libs/*.jar app.jar
sudo systemctl restart itdaing-backend
```

### 주의사항
- Spring ASG가 활성화되어 있으면, 모든 인스턴스에 배포 필요
- 현재 ASG 중지 상태이므로 단일 서버만 업데이트

---

## 3. AI 서비스 (FastAPI 챗봇)

### 저장소
- **GitHub:** `da-itdaing/sub-repo` → `chatbot/`
- **배포 위치:** EC2 (`hj-chatbot-ec2`) + ASG 인스턴스

### 배포 방법 (원본 서버)

```bash
# 챗봇 서버 (hj-chatbot-ec2)에서 실행
cd /home/ubuntu/chatbot

# 1. 최신 코드 가져오기
git pull origin main

# 2. 의존성 업데이트 (필요 시)
source .venv/bin/activate
pip install -r requirements.txt

# 3. 서비스 재시작
sudo systemctl restart chatbot

# 4. 상태 확인
sudo systemctl status chatbot
```

### 원라인 배포 스크립트
```bash
cd /home/ubuntu/chatbot && \
git pull origin main && \
source .venv/bin/activate && \
pip install -r requirements.txt && \
sudo systemctl restart chatbot
```

### ASG 인스턴스 배포

ASG로 생성된 인스턴스에는 SSM을 통해 배포:

```bash
# ASG 인스턴스 ID 확인
aws autoscaling describe-auto-scaling-instances \
  --query 'AutoScalingInstances[?AutoScalingGroupName==`itdaing-chatbot-asg`].InstanceId' \
  --output text

# 각 인스턴스에 배포 명령 실행
aws ssm send-command \
  --instance-ids "<INSTANCE_ID>" \
  --document-name "AWS-RunShellScript" \
  --parameters 'commands=[
    "cd /home/ubuntu/chatbot",
    "git pull origin main",
    "source .venv/bin/activate",
    "pip install -r requirements.txt",
    "sudo systemctl restart chatbot"
  ]'
```

### 롤백
```bash
cd /home/ubuntu/chatbot
git checkout <previous-commit-hash>
sudo systemctl restart chatbot
```

---

## 📝 Git Workflow

### 브랜치 전략
- `main`: 프로덕션 배포 브랜치
- `develop`: 개발 브랜치 (있는 경우)
- `feature/*`: 기능 개발

### 커밋 메시지
- Gitmoji 사용: `✨ feat:`, `🐛 fix:`, `🔧 chore:` 등

### 배포 트리거
- `main` 브랜치에 push 시 수동 배포 실행
- 향후 GitHub Actions로 자동화 가능

---

## 🔐 환경 변수 관리

### 프론트엔드
- 빌드 시 `.env` 파일 또는 환경 변수 주입
- API URL: `https://aischool.daitdaing.com`

### 백엔드 (Spring)
- `application.yml` 또는 환경 변수
- AWS Secrets Manager에서 주입 (권장)

### 챗봇
- `chatbot.env` 파일
- Secrets Manager에서 생성: `scripts/generate-chatbot-env.sh`

---

## ⚠️ 주의사항

1. **배포 전 테스트**
   - 로컬 또는 스테이징 환경에서 테스트 후 배포

2. **다운타임**
   - Spring/챗봇 재시작 시 약 10~30초 다운타임 발생
   - ASG가 활성화되어 있으면 롤링 업데이트로 무중단 가능

3. **캐시 무효화**
   - 프론트엔드 변경 후 CloudFront 캐시 무효화 필요
   - 브라우저 캐시도 고려 (버전 해시로 해결됨)

4. **DB 마이그레이션**
   - 스키마 변경 시 배포 전에 마이그레이션 실행

---

## 📅 업데이트 기록

- 2025-12-03: 초기 문서 작성

