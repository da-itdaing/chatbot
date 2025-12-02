# 잇다잉 챗봇 인프라 용량 분석

## 현재 인프라 구성

| 서버 | 인스턴스 타입 | vCPU | 메모리 | 역할 |
|------|-------------|------|--------|------|
| 챗봇 EC2 | m5.large | 2 | 8GB | FastAPI + LangGraph + PGVector |
| 백엔드 EC2 | (확인 필요) | - | - | Spring Boot + PostgreSQL |
| RDS | (공유) | - | - | PostgreSQL 데이터베이스 |

---

## 100명 동시 사용자 시나리오 분석

### 1. 챗봇 서버 (m5.large) 부하 분석

#### 리소스 사용량 (요청당)
| 항목 | 값 | 비고 |
|------|-----|------|
| OpenAI API 호출 | 2-4회 | intent 분류 + RAG + 응답 생성 |
| 평균 응답 시간 | 3-8초 | LLM 응답 대기 시간 포함 |
| 메모리 사용 | ~50MB/요청 | LangGraph 상태 + 임베딩 |
| CPU 사용 | 10-20% | 대부분 I/O 대기 |

#### 동시 처리 능력 추정

**m5.large (2 vCPU, 8GB RAM) 기준:**

```
최대 동시 요청 = 메모리 / 요청당 메모리
                = 8GB / 50MB
                ≈ 160개 (이론적 최대)

실제 안전 마진 적용 (70%):
                ≈ 112개 동시 요청
```

**결론: m5.large로 100명 동시 사용 가능** ✅

단, 아래 조건 충족 시:
- 모든 사용자가 동시에 요청하지 않음 (실제 동시 요청률 ~30%)
- OpenAI API Rate Limit 충분 (Tier 1: 500 RPM)

---

### 2. 병목 지점 분석

#### 2.1 OpenAI API Rate Limit (가장 큰 병목)

| Tier | RPM (분당 요청) | TPM (분당 토큰) |
|------|----------------|-----------------|
| Tier 1 (기본) | 500 | 30,000 |
| Tier 2 | 5,000 | 150,000 |
| Tier 3 | 10,000 | 1,000,000 |

**100명 동시 사용 시:**
- 평균 요청 간격: 30초 (채팅 특성상)
- 분당 요청: 100명 × 2회/분 = 200 RPM
- 요청당 LLM 호출: 3회
- **총 LLM 호출: 600 RPM** → Tier 1 초과 ⚠️

**권장: Tier 2 이상 업그레이드 필요**

#### 2.2 PGVector 검색 성능

| 데이터 규모 | 검색 시간 | 비고 |
|------------|----------|------|
| 100건 | ~10ms | 현재 |
| 1,000건 | ~50ms | 양호 |
| 10,000건 | ~200ms | 인덱스 최적화 필요 |

**현재 80건 데이터: 문제 없음** ✅

#### 2.3 네트워크 대역폭

- m5.large: 최대 10 Gbps
- 스트리밍 응답: ~10KB/요청
- 100명 동시: ~1MB/s
- **충분한 여유** ✅

---

### 3. 권장 인프라 구성

#### 옵션 A: 현재 구성 유지 (100명 이하)

```
[사용자] → [ALB] → [m5.large 단일] → [RDS]
                         ↓
                    [OpenAI API]
```

**장점:** 비용 최소화
**단점:** 단일 장애점, 스케일링 불가

**예상 비용:** ~$70/월 (EC2만)

---

#### 옵션 B: 수평 확장 (100-300명)

```
[사용자] → [ALB] → [m5.large × 2] → [RDS]
                         ↓
                    [OpenAI API]
```

**구성:**
- ALB (Application Load Balancer) 추가
- EC2 2대로 수평 확장
- 세션 스티키니스 또는 Redis 세션 스토어

**예상 비용:** ~$150/월

---

#### 옵션 C: Auto Scaling (100-500명, 권장)

```
[사용자] → [ALB] → [ASG: m5.large 1-4대] → [RDS]
                         ↓
                    [OpenAI API]
```

**Auto Scaling 정책:**
```yaml
ScaleUp:
  Metric: CPUUtilization > 70%
  Cooldown: 300초
  Adjustment: +1 인스턴스

ScaleDown:
  Metric: CPUUtilization < 30%
  Cooldown: 600초
  Adjustment: -1 인스턴스

Limits:
  Min: 1
  Max: 4
  DesiredCapacity: 2
```

**예상 비용:** ~$100-300/월 (트래픽에 따라)

---

### 4. 발표일 대응 체크리스트

#### 사전 준비 (D-1)

- [ ] OpenAI API Tier 확인 및 업그레이드 (Tier 2 권장)
- [ ] EC2 인스턴스 타입 업그레이드 (필요시 m5.xlarge)
- [ ] CloudWatch 알람 설정 (CPU > 80%, Memory > 80%)
- [ ] 로그 모니터링 설정 (CloudWatch Logs)

#### 발표 당일 (D-Day)

- [ ] 서버 상태 모니터링 대시보드 준비
- [ ] OpenAI API 사용량 모니터링
- [ ] 비상 연락망 준비 (서버 다운 시)

#### 비상 대응

```bash
# CPU 과부하 시 즉시 스케일업
aws ec2 modify-instance-attribute \
  --instance-id i-xxxxx \
  --instance-type m5.xlarge

# 또는 새 인스턴스 추가 (수동)
aws ec2 run-instances \
  --image-id ami-xxxxx \
  --instance-type m5.large \
  --count 1
```

---

### 5. 인스턴스 타입별 비교

| 타입 | vCPU | RAM | 네트워크 | 가격/시간 | 동시 사용자 |
|------|------|-----|----------|----------|------------|
| t3.medium | 2 | 4GB | 최대 5Gbps | $0.0416 | ~50명 |
| **m5.large** | 2 | 8GB | 최대 10Gbps | $0.096 | **~100명** |
| m5.xlarge | 4 | 16GB | 최대 10Gbps | $0.192 | ~200명 |
| m5.2xlarge | 8 | 32GB | 최대 10Gbps | $0.384 | ~400명 |

---

### 6. 최종 권장사항

**100명 동시 사용자 기준:**

| 항목 | 현재 | 권장 | 비고 |
|------|------|------|------|
| 챗봇 EC2 | m5.large | **m5.large 유지** | 충분함 |
| 백엔드 EC2 | - | m5.large 이상 | 확인 필요 |
| OpenAI Tier | Tier 1 | **Tier 2 필수** | 가장 중요 |
| ALB | 없음 | 선택적 | 고가용성 필요시 |
| Auto Scaling | 없음 | 선택적 | 트래픽 급증 대비 |

**핵심: OpenAI API Tier 업그레이드가 가장 중요합니다!**

---

### 7. 모니터링 명령어

```bash
# 실시간 CPU/메모리 모니터링
htop

# FastAPI 프로세스 확인
ps aux | grep uvicorn

# 네트워크 연결 수
netstat -an | grep :9000 | wc -l

# OpenAI API 호출 로그
tail -f /home/ubuntu/chatbot/logs/chatbot.log | grep "openai"

# 크롤러 상태 확인
sudo systemctl status daily-crawler.service
tail -f /home/ubuntu/chatbot/logs/daily_crawler.log
```

---

## 결론

**m5.large로 100명 동시 사용은 가능합니다.**

단, 다음 조건 필수:
1. ✅ OpenAI API Tier 2 이상 업그레이드
2. ✅ 발표 전 부하 테스트 수행
3. ✅ 모니터링 대시보드 준비

추가 안전 마진이 필요하면:
- m5.xlarge로 업그레이드 (비용 2배, 용량 2배)
- 또는 ALB + 2대 구성

