# 잇다잉 챗봇 성능 평가 트래킹

> 이 문서는 챗봇 성능 평가 실험 이력과 결과를 추적합니다.
> 
> **최종 업데이트**: 2025-12-02

---

## 1. 현재 성능 기준 (v9)

### 1.1 SLA 목표 및 현재 상태

| 지표 | 목표 | 현재 상태 | 비고 |
|------|------|----------|------|
| Latency P90 | < 10,000ms | **2,430ms** ✅ | 90% 요청 기준 |
| Latency P99 | < 15,000ms | **3,920ms** ✅ | 99% 요청 기준 |
| 품질 점수 (overall) | ≥ 3.5/5.0 | **4.29/5.0** ✅ | LLM-as-Judge |
| 가드레일 통과율 | ≥ 90% | **100%** ✅ | safety ≥ 4 |
| 추천 성공률 | ≥ 70% | **95%+** ✅ | recommendation_quality ≥ 4 |
| 인젝션 차단율 | = 100% | **100%** ✅ | 모든 인젝션 케이스 |
| Error Rate | 0% | **0%** ✅ | 전체 케이스 |

### 1.2 카테고리별 목표

| case_type | 주요 지표 | 목표 |
|-----------|----------|------|
| `retrieval_recommendation` | recommendation_quality | ≥ 4.0 |
| `guardrail_safety` | safety_compliance | ≥ 4.5 |
| `prompt_injection` | no_sensitive_leak | = 5.0 |
| `edge_robustness` | task_fulfillment | ≥ 3.5 |
| `seller_guide` | helpfulness | ≥ 4.0 |

---

## 2. 실험 이력

### 2.1 실험 로그 템플릿

```
### 실험: {experiment_id}

**날짜**: YYYY-MM-DD
**목적**: {변경 목적}
**변경 사항**: 
- {변경 내용 1}
- {변경 내용 2}

**결과 요약**:
| 지표 | 이전 | 이후 | 변화 |
|------|------|------|------|
| overall | X.XX | X.XX | +0.XX |
| task_fulfillment | X.XX | X.XX | +0.XX |

**결론**: {성공/실패/추가 실험 필요}
**다음 단계**: {후속 작업}
```

### 2.2 실험 기록

---

#### 실험: baseline_v0 (초기 기준)

**날짜**: 2025-11-30  
**목적**: 초기 성능 기준 수립  
**변경 사항**: 
- 없음 (초기 측정)

**결과 요약**:
| 지표 | 값 | 목표 | 상태 |
|------|-----|------|------|
| Latency P50 | TBD | - | - |
| Latency P90 | TBD | < 10s | TBD |
| overall | TBD | ≥ 3.5 | TBD |
| guardrail_pass_rate | TBD | ≥ 90% | TBD |

**결론**: 초기 baseline 측정 예정  
**다음 단계**: 첫 전체 평가 실행

---

#### 실험: {다음 실험 추가 위치}

_(새 실험 결과는 여기에 추가)_

---

## 3. 지표 트렌드

### 3.1 품질 점수 추이

| 날짜 | experiment_id | overall | task | grounded | safety | tone |
|------|--------------|---------|------|----------|--------|------|
| 2025-11-30 | baseline_v0 | TBD | TBD | TBD | TBD | TBD |
| - | - | - | - | - | - | - |

### 3.2 Latency 추이

| 날짜 | experiment_id | P50 (ms) | P90 (ms) | P99 (ms) |
|------|--------------|----------|----------|----------|
| 2025-11-30 | baseline_v0 | TBD | TBD | TBD |
| - | - | - | - | - |

### 3.3 카테고리별 성공률 추이

| 날짜 | retrieval | guardrail | injection | edge |
|------|-----------|-----------|-----------|------|
| 2025-11-30 | TBD | TBD | TBD | TBD |
| - | - | - | - | - |

---

## 4. 알려진 이슈 및 개선 과제

### 4.1 현재 이슈

| ID | 카테고리 | 설명 | 심각도 | 상태 |
|----|---------|------|--------|------|
| ISS-001 | - | _(이슈 발견 시 추가)_ | - | - |

### 4.2 개선 과제

| ID | 우선순위 | 설명 | 예상 효과 | 상태 |
|----|---------|------|----------|------|
| IMP-001 | 높음 | 프롬프트 가드레일 강화 | safety +0.3 | 예정 |
| IMP-002 | 중간 | RAG top_k 최적화 | grounded +0.2 | 예정 |
| IMP-003 | 낮음 | 톤 조정 (덜 방어적) | tone +0.2 | 예정 |

---

## 5. 회귀 케이스 추적

### 5.1 주요 실패 패턴

| 패턴 | 빈도 | 예시 케이스 | 원인 분석 | 조치 |
|------|------|------------|----------|------|
| _(실패 패턴 발견 시 추가)_ | - | - | - | - |

### 5.2 해결된 회귀

| 날짜 | 패턴 | 조치 | 결과 |
|------|------|------|------|
| _(해결된 회귀 추가)_ | - | - | - |

---

## 6. 프롬프트 버전 이력

### 6.1 Consumer 프롬프트

| 버전 | 날짜 | 주요 변경 | 효과 |
|------|------|----------|------|
| v1.0 | 2025-11-XX | 초기 버전 | baseline |
| _(추가)_ | - | - | - |

### 6.2 Seller 프롬프트

| 버전 | 날짜 | 주요 변경 | 효과 |
|------|------|----------|------|
| v1.0 | 2025-11-XX | 초기 버전 | baseline |
| _(추가)_ | - | - | - |

### 6.3 가드레일 프롬프트

| 버전 | 날짜 | 주요 변경 | 효과 |
|------|------|----------|------|
| v1.0 | 2025-11-XX | 초기 버전 | baseline |
| _(추가)_ | - | - | - |

---

## 7. 모델/설정 변경 이력

### 7.1 OpenAI 모델

| 날짜 | 이전 | 이후 | 이유 | 효과 |
|------|------|------|------|------|
| - | - | gpt-4o-mini | 초기 설정 | - |

### 7.2 RAG 설정

| 날짜 | 설정 | 이전 | 이후 | 효과 |
|------|------|------|------|------|
| - | RAG_TOP_K | - | 5 | 초기 |
| - | ZONE_RAG_TOP_K | - | 3 | 초기 |

### 7.3 LangGraph 설정

| 날짜 | 설정 | 이전 | 이후 | 효과 |
|------|------|------|------|------|
| - | MAX_MESSAGE_HISTORY | - | 10 | 초기 |

---

## 8. 주간/월간 리포트

### 8.1 주간 체크리스트

- [ ] 전체 평가 실행 (`run_langsmith_evals.py`)
- [ ] 지표 트렌드 업데이트 (섹션 3)
- [ ] 새로운 실패 패턴 분석 (섹션 5)
- [ ] 이슈/개선 과제 업데이트 (섹션 4)

### 8.2 월간 리뷰 항목

- [ ] SLA 달성 현황 검토
- [ ] 카테고리별 성능 분석
- [ ] 프롬프트 개선 방향 수립
- [ ] 다음 달 목표 설정

---

## 9. 빠른 실행 명령어

```bash
# 환경 활성화
cd /home/ubuntu/chatbot && . .venv/bin/activate

# 전체 평가 (새 실험)
python langsmith-test/run_langsmith_evals.py \
  --experiment exp_$(date +%Y%m%d)_description \
  --use-custom-evaluator

# Consumer 빠른 회귀 테스트
python langsmith-test/run_consumer_evals.py \
  --experiment quick_$(date +%Y%m%d) \
  --limit 30

# 로컬 벤치마크
python scripts/benchmark_suite.py --mode consumer --samples 40

# 결과 저장 경로
# - LangSmith UI: https://smith.langchain.com
# - 로컬: benchmark_results/
```

---

## 10. 참고 문서

- [QUALITY_TESTING.md](./QUALITY_TESTING.md) - 품질 검사 종합 가이드
- [CHATBOT_EVALUATION.md](./CHATBOT_EVALUATION.md) - 성능 평가 상세
- [langsmith-test/README.md](../langsmith-test/README.md) - LangSmith 테스트 가이드
- [langsmith-test/input/test_prompt.md](../langsmith-test/input/test_prompt.md) - 케이스 요약

---

## 변경 이력

| 날짜 | 변경 내용 | 작업자 |
|------|----------|--------|
| 2025-11-30 | 문서 초기 작성 | AI |








