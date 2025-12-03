# 잇다잉 챗봇 품질 검사 종합 가이드

> 이 문서는 Itdaing 챗봇의 품질 검사 체계 전반을 설명합니다.

---

## 1. 품질 검사 개요

### 1.1 목표

- **일관된 품질 유지**: 프롬프트/RAG/가드레일 변경 시 품질 퇴보 방지
- **정량적 평가**: LLM-as-Judge 기반 점수화로 객관적 비교 가능
- **지속적 개선**: 실험별 결과 추적 및 분석

### 1.2 평가 축 (Evaluation Dimensions)

| 축 | 설명 | 적용 대상 |
|----|------|----------|
| `task_fulfillment` | 사용자 요청 충족도 | 모든 케이스 |
| `grounded_in_markets_zones` | 시드 데이터 기반 답변 여부 | 추천 케이스 |
| `clarity` | 답변 명확성/간결성 | 모든 케이스 |
| `safety` | 위험/불법 요청 거절 | 가드레일 케이스 |
| `no_sensitive_leak` | 민감 정보 비노출 | 인젝션 케이스 |
| `policy_explanation_quality` | 정책 설명 품질 | 범위 외 케이스 |

### 1.3 품질 검사 레이어

```
┌─────────────────────────────────────────────────────────────┐
│                    품질 검사 체계                             │
├─────────────────────────────────────────────────────────────┤
│  1. 로컬 벤치마크        scripts/benchmark_suite.py          │
│     - 빠른 피드백 (30-50개 케이스)                            │
│     - LLM-as-Judge 기반 품질 점수                             │
├─────────────────────────────────────────────────────────────┤
│  2. LangSmith 평가       langsmith-test/run_*_evals.py       │
│     - 전체 Dataset 대상 (278개 케이스)                        │
│     - 트레이싱 + 평가 + 모니터링                               │
├─────────────────────────────────────────────────────────────┤
│  3. 수동 품질 체크       langsmith-test/deep_check_*.py      │
│     - 메타데이터 일관성 검증                                   │
│     - 엣지 케이스 발굴                                        │
└─────────────────────────────────────────────────────────────┘
```

---

## 2. 테스트 데이터셋 구조

### 2.1 위치 및 파일

```
langsmith-test/input/
├── test_prompts.json              # 전체 278개 (기준)
├── test_prompts_30_se.json        # 30개 빠른 회귀
├── test_prompts_30_se_hard.json   # 30개 가드레일/인젝션
├── test_prompts_100.json          # 100개 일반
├── test_prompts_100_hard.json     # 100개 난이도 높음
├── test_prompts_consumer_single.json  # Consumer 단일턴
├── test_prompts_consumer_multi.json   # Consumer 멀티턴
├── test_prompts_labeled.json      # LLM 라벨링 결과
└── test_prompt.md                 # 케이스 요약 문서
```

### 2.2 케이스 메타데이터 스키마

```json
{
  "id": "C-1",
  "case_group": "C-1",
  "mode": "consumer",
  "case_type": "retrieval_recommendation",
  "turn_type": "single",
  "transport": "sync",
  "difficulty": "normal",
  "expected_behavior": "must_recommend_from_seed",
  "section": "지역+카테고리 추천",
  "input": "이번 주말에 광주 북구에서 열리는 빈티지 감성 플리마켓 추천해줘",
  "constraints": {
    "table": "popup",
    "where": { "source": "markets_seed" }
  }
}
```

### 2.3 케이스 분류

| case_type | 설명 | 예시 수 |
|-----------|------|---------|
| `retrieval_recommendation` | RAG 기반 추천 | ~150 |
| `seller_guide` | 판매자 가이드 | ~50 |
| `guardrail_safety` | 안전 가드레일 | ~30 |
| `edge_robustness` | 엣지 케이스 | ~20 |
| `prompt_injection` | 인젝션 공격 | ~15 |
| `policy_bypass` | 정책 우회 시도 | ~10 |
| `performance_stress` | 성능/스팸 | ~5 |

---

## 3. 로컬 벤치마크 실행

### 3.1 기본 실행

```bash
cd /home/ubuntu/chatbot
. .venv/bin/activate

# 빠른 테스트 (10개 샘플)
python scripts/benchmark_suite.py --mode consumer --samples 10

# 전체 테스트
python scripts/benchmark_suite.py --mode consumer --full

# 품질 평가 없이 latency만
python scripts/benchmark_suite.py --mode consumer --samples 30 --no-quality
```

### 3.2 출력 예시

```
══════════════════════════════════════════════════════════════════════
🤖 잇다잉 챗봇 벤치마크 결과
══════════════════════════════════════════════════════════════════════
실행 시간: 2025-11-30T14:30:00
총 테스트: 40개
성공: 38개 | 실패: 2개

──────────────────────────────────────────────────────────────────────
📊 Latency (응답 시간)
──────────────────────────────────────────────────────────────────────
  P50 (중앙값): 3,200ms
  P90:         5,800ms
  P99:         8,500ms

──────────────────────────────────────────────────────────────────────
⭐ Quality (LLM-as-Judge, 1-5점)
──────────────────────────────────────────────────────────────────────
  [핵심 지표]
    요청 충족도 (task_fulfillment):     4.2
    데이터 기반 (grounded_in_data):     4.5
    가드레일 준수 (safety_compliance):  4.8
    톤 적절성 (tone_appropriateness):   4.3
```

### 3.3 SLA 기준

| 지표 | 목표 | 설명 |
|------|------|------|
| Latency P90 | < 10초 | 90%의 요청이 10초 이내 응답 |
| 품질 점수 | ≥ 3.5/5 | 전체 평균 |
| 가드레일 통과율 | ≥ 90% | safety_compliance ≥ 4 |
| 추천 성공률 | ≥ 70% | recommendation_quality ≥ 4 |

---

## 4. LangSmith 평가 실행

### 4.1 사전 준비

```bash
cd /home/ubuntu/chatbot
. .venv/bin/activate

# 환경 변수 확인
cat chatbot.env | grep LANGSMITH

# RAG 시드 데이터 로딩 (필요 시)
python -m app.data.markets_loader --reset
python -m app.data.zones_loader --reset
```

### 4.2 Dataset 업로드

```bash
# 전체 라벨링 데이터셋
python langsmith-test/upload_dataset.py \
  --dataset-name itdaing-chatbot-labeled \
  --mode canonical_v2

# Consumer 단일턴만
python langsmith-test/upload_dataset.py \
  --dataset-name itdaing-consumer-single \
  --mode consumer_single
```

### 4.3 평가 실행

```bash
# 전체 Dataset 평가
python langsmith-test/run_langsmith_evals.py \
  --experiment baseline_v1

# LLM-as-Judge 포함
python langsmith-test/run_langsmith_evals.py \
  --experiment guardrail_v1 \
  --use-custom-evaluator

# Consumer만 필터링
python langsmith-test/run_consumer_evals.py \
  --experiment consumer_rag_v2 \
  --use-judge
```

### 4.4 LangSmith UI 확인

1. https://smith.langchain.com 접속
2. 프로젝트: `chatbot-aws` 선택
3. Datasets & Experiments 탭에서 결과 확인

---

## 5. 품질 체크 도구

### 5.1 테스트 프롬프트 검증

```bash
python langsmith-test/validate_test_prompts.py
```

출력:
```
Validating dataset at .../input/test_prompts.json
All required fields present.
=== Counts by mode/case_type ===
consumer/retrieval_recommendation: 120
consumer/guardrail_safety: 25
seller/seller_guide: 45
...
```

### 5.2 Consumer 깊은 품질 체크

```bash
python langsmith-test/deep_check_consumer.py
```

이 스크립트는 다음을 검사합니다:

1. **메타 설명형 input**: 실제 사용자 발화처럼 읽히지 않는 케이스
2. **멀티턴 마커**: 단일턴인데 "지난번", "아까" 등의 표현 포함
3. **모호한 retrieval**: 조건 없이 "아무 데나" 같은 요청
4. **case_type vs expected_behavior 불일치**: 라벨링 오류
5. **빈/짧은 input**: 품질 의심 케이스
6. **placeholder input**: "비속어 포함 문장" 같은 추상적 입력

---

## 6. 평가 워크플로우

### 6.1 변경 전/후 비교

```bash
# 1. 변경 전 baseline
python langsmith-test/run_langsmith_evals.py \
  --experiment baseline_before_change \
  --use-custom-evaluator

# 2. 코드/프롬프트 변경

# 3. 변경 후 평가
python langsmith-test/run_langsmith_evals.py \
  --experiment after_prompt_v2 \
  --use-custom-evaluator

# 4. LangSmith UI에서 비교
```

### 6.2 회귀 테스트 (PR 단위)

```bash
# 빠른 회귀 (30개 케이스)
python langsmith-test/run_consumer_evals.py \
  --experiment pr_123_quick \
  --limit 30 \
  --difficulty normal
```

### 6.3 가드레일 집중 테스트

```bash
# 가드레일/인젝션 케이스만
python langsmith-test/run_consumer_evals.py \
  --experiment guardrail_focus_v1 \
  --case-type guardrail_safety \
  --use-judge

python langsmith-test/run_consumer_evals.py \
  --experiment injection_focus_v1 \
  --case-type prompt_injection \
  --use-judge
```

---

## 7. 평가 지표 해석

### 7.1 LLM-as-Judge 점수 기준

| 점수 | 의미 |
|------|------|
| 5 | 매우 우수 - 완벽한 응답 |
| 4 | 우수 - 약간의 개선 여지 |
| 3 | 보통 - 기본 요구사항 충족 |
| 2 | 미흡 - 주요 문제 있음 |
| 1 | 매우 부족 - 심각한 문제 |

### 7.2 카테고리별 기대치

| case_type | 주요 지표 | 목표 점수 |
|-----------|----------|----------|
| `retrieval_recommendation` | grounded, recommendation_quality | ≥ 4.0 |
| `guardrail_safety` | safety, no_sensitive_leak | ≥ 4.5 |
| `prompt_injection` | safety, no_sensitive_leak | = 5.0 |
| `edge_robustness` | task_fulfillment, clarity | ≥ 3.5 |

### 7.3 문제 진단

| 증상 | 가능한 원인 | 조치 |
|------|------------|------|
| grounded_in_data 낮음 | RAG 검색 실패, 할루시네이션 | 시드 데이터 확인, 프롬프트 강화 |
| safety 낮음 | 가드레일 프롬프트 약함 | 시스템 프롬프트 강화 |
| tone 낮음 | 과도하게 방어적 | 톤 조정 |
| latency 높음 | Tool 호출 과다, 모델 지연 | 파이프라인 최적화 |

---

## 8. 관련 문서

- [CHATBOT_EVALUATION.md](./CHATBOT_EVALUATION.md) - 성능 평가 상세
- [EVALUATION_TRACKING.md](./EVALUATION_TRACKING.md) - 평가 트래킹
- [langsmith-test/README.md](../langsmith-test/README.md) - LangSmith 테스트 가이드
- [langsmith-test/input/test_prompt.md](../langsmith-test/input/test_prompt.md) - 케이스 요약

---

## 9. 빠른 참조 명령어

```bash
# 환경 활성화
cd /home/ubuntu/chatbot && . .venv/bin/activate

# 로컬 벤치마크 (빠름)
python scripts/benchmark_suite.py --mode consumer --samples 20

# LangSmith 전체 평가
python langsmith-test/run_langsmith_evals.py --experiment exp_name --use-custom-evaluator

# 데이터셋 검증
python langsmith-test/validate_test_prompts.py

# Consumer 품질 체크
python langsmith-test/deep_check_consumer.py
```










