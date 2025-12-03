# LangSmith 테스트 프레임워크

잇다잉(Itdaing) 챗봇 평가 및 테스트를 위한 프레임워크입니다.

## 📁 폴더 구조

```
langsmith-test/
├── consumer/                    # 소비자 챗봇 테스트
│   ├── __init__.py
│   ├── datasets/                # 테스트 데이터셋
│   │   ├── single_turn_v1.json  # 싱글턴 203개
│   │   ├── multi_turn_v1.json   # 멀티턴 36개
│   │   ├── test_prompt.md       # 테스트 케이스 설계 문서
│   │   └── archive/             # 이전 버전 데이터셋
│   ├── experiments/             # 실험 결과
│   │   ├── CHANGELOG.md         # 실험 기록
│   │   ├── analysis/            # 분석 결과
│   │   └── *.json, *.log        # 실험 로그
│   ├── run_consumer_evals.py    # 소비자 전용 평가
│   ├── run_evaluation_v3.py     # v3 평가 스크립트
│   └── deep_check_consumer.py   # 심층 분석
│
├── seller/                      # 판매자 챗봇 테스트
│   ├── __init__.py
│   ├── datasets/                # 테스트 데이터셋
│   │   ├── single_turn_v1.json  # 싱글턴 50개
│   │   └── multi_turn_v1.json   # 멀티턴 15개
│   └── experiments/             # 실험 결과
│       └── CHANGELOG.md         # 실험 기록
│
├── shared/                      # 공통 모듈
│   ├── __init__.py
│   ├── evaluators/              # 평가자
│   │   ├── __init__.py
│   │   ├── rule_based.py        # 규칙 기반 + LLM 평가자
│   │   └── mobile_optimized.py  # 모바일 최적화 평가
│   └── utils/                   # 유틸리티
│       └── __init__.py
│
├── run_experiment.py            # 통합 실험 실행기
├── target_function.py           # 챗봇 호출 함수
├── upload_dataset.py            # LangSmith 데이터셋 업로드
├── validate_test_prompts.py     # 테스트 케이스 검증
├── analyze_p99.py               # P99 지연시간 분석
├── label_dataset.py             # 데이터셋 라벨링
└── README.md
```

## 🚀 빠른 시작

### 1. 소비자 챗봇 테스트

```bash
cd /home/ubuntu/chatbot
source .venv/bin/activate

# 싱글턴 테스트
python langsmith-test/run_experiment.py \
    --dataset langsmith-test/consumer/datasets/single_turn_v1.json \
    --experiment-id "consumer-single-v15" \
    --mode consumer

# 멀티턴 테스트
python langsmith-test/run_experiment.py \
    --dataset langsmith-test/consumer/datasets/multi_turn_v1.json \
    --experiment-id "consumer-multi-v15" \
    --mode consumer \
    --multi-turn
```

### 2. 판매자 챗봇 테스트

```bash
# 싱글턴 테스트
python langsmith-test/run_experiment.py \
    --dataset langsmith-test/seller/datasets/single_turn_v1.json \
    --experiment-id "seller-single-v1" \
    --mode seller

# 멀티턴 테스트
python langsmith-test/run_experiment.py \
    --dataset langsmith-test/seller/datasets/multi_turn_v1.json \
    --experiment-id "seller-multi-v1" \
    --mode seller \
    --multi-turn
```

## 📊 평가 축

### 소비자 챗봇

| 축 | 설명 | 가중치 |
|---|------|-------|
| Task Fulfillment | 1~3개 마켓/팝업 추천 | 30% |
| Grounded in Data | 실제 데이터 기반 응답 | 25% |
| Clarity | 간결한 한국어 응답 | 15% |
| Safety | 가드레일 준수 | 15% |
| Recommendation Quality | 조건 매칭 + 추천 이유 | 15% |

### 판매자 챗봇

| 축 | 설명 | 가중치 |
|---|------|-------|
| Task Fulfillment | 1~3개 존 추천 | 25% |
| Grounded in Data | 실제 상권 데이터 사용 | 25% |
| Commercial Accuracy | 유동인구/임대료 정확성 | 20% |
| Practical Value | 셀러에게 유용한 정보 | 15% |
| Clarity | 간결한 한국어 응답 | 15% |

## 📈 Latency 목표

| 지표 | 소비자 | 판매자 |
|-----|-------|-------|
| P50 | < 2.5초 | < 3초 |
| P99 | < 6초 | < 8초 |
| Error Rate | < 1% | < 1% |

## 🔧 데이터셋 형식

### 싱글턴

```json
{
  "id": "C-001",
  "category": "greeting",
  "input": "안녕하세요",
  "expected_behavior": "친근한 인사와 서비스 안내",
  "evaluation_criteria": ["greeting_appropriate", "service_intro"]
}
```

### 멀티턴

```json
{
  "id": "CM-001",
  "category": "refinement",
  "turns": [
    {"role": "user", "content": "동구 플리마켓 추천해줘"},
    {"role": "assistant", "content": ""},
    {"role": "user", "content": "그 중에서 카페 있는 곳은?"}
  ],
  "expected_behavior": "이전 추천 기억하고 조건 필터링",
  "evaluation_criteria": ["context_recall", "refinement_handling"]
}
```

## 📝 실험 기록

- **소비자**: `consumer/experiments/CHANGELOG.md`
- **판매자**: `seller/experiments/CHANGELOG.md`

## 🔗 관련 문서

- [LangSmith Docs](https://docs.langchain.com/langsmith/)
- [LangGraph Docs](https://docs.langchain.com/oss/python/langgraph/)
