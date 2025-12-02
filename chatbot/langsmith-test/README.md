# Itdaing LangSmith 테스트 & 평가 가이드

**v11 업데이트 (2025-12)**

이 디렉터리는 **Itdaing LangGraph 챗봇**을 LangSmith와 연동해서  
데이터셋 구축, 평가, 품질 개선을 수행하는 전체 워크플로우를 담고 있습니다.

## 아키텍처 개요

```
┌─────────────────────────────────────────────────────────────────┐
│                        FastAPI (9000)                           │
│  ┌─────────────────────────────────────────────────────────────┐│
│  │                    Consumer Graph (Async)                   ││
│  │  extract_query → full_classify → schedule_tool → generate  ││
│  │                                                             ││
│  │  도구: consumer_retrieve_async (RAG)                        ││
│  │        popup_sql_lookup_async (정형 DB)                     ││
│  │        web_search_async (실시간 정보)                       ││
│  └─────────────────────────────────────────────────────────────┘│
│                                                                 │
│  ┌─────────────────────────────────────────────────────────────┐│
│  │                     Seller Graph (Async)                    ││
│  │  [개발 예정]                                                ││
│  └─────────────────────────────────────────────────────────────┘│
│                                                                 │
│  ┌──────────────────┐  ┌──────────────────┐  ┌───────────────┐ │
│  │ AsyncPostgres    │  │ PGVector         │  │ asyncpg       │ │
│  │ Saver            │  │ (RAG)            │  │ (SQL Lookup)  │ │
│  │ (Checkpoint)     │  │ itdaing_popups   │  │               │ │
│  └──────────────────┘  └──────────────────┘  └───────────────┘ │
└─────────────────────────────────────────────────────────────────┘
```

---

## 1. 폴더 구조

```
langsmith-test/
├── input/                          # 테스트 데이터셋
│   ├── consumer_single_1202_v1.json  # 싱글턴 203개
│   ├── consumer_multi_1202_v1.json   # 멀티턴 36개
│   ├── test_prompt.md                # 케이스 요약
│   └── archive/                      # 이전 버전 백업
├── evaluators/                     # 평가기
│   ├── rule_based.py               # 규칙 기반 평가
│   └── mobile_optimized.py         # 모바일 최적화 평가
├── experiments/                    # 실험 기록
│   ├── CHANGELOG.md                # 버전별 변경 사항
│   └── profile_nodes.py            # 노드 프로파일링
├── target_function.py              # LangSmith Target Function
├── run_experiment.py               # 실험 실행 스크립트
├── run_langsmith_evals.py          # LangSmith 평가 러너
├── analyze_p99.py                  # P99 지연시간 분석
└── README.md                       # (현재 문서)
```

---

## 2. 환경 설정

### 2.1 가상환경 활성화

```bash
cd /home/ubuntu/chatbot
. .venv/bin/activate
```

### 2.2 환경 변수 (`chatbot.env`)

```bash
# OpenAI
OPENAI_API_KEY=sk-...
OPENAI_MODEL=gpt-4o-mini
OPENAI_EMBEDDING_MODEL=text-embedding-3-small

# LangSmith
LANGSMITH_API_KEY=lsv2_pt_...
LANGSMITH_PROJECT=chatbot-aws
LANGSMITH_TRACING=true

# PostgreSQL (AsyncPostgresSaver + asyncpg)
POSTGRES_HOST=...
POSTGRES_PORT=5432
POSTGRES_DB=itdaing
POSTGRES_USER=...
POSTGRES_PASSWORD=...

# PGVector (RAG)
PGVECTOR_CONNECTION=postgresql://...
VECTOR_COLLECTION=itdaing_popups
PGVECTOR_ZONE_COLLECTION=itdaing_zone
```

### 2.3 RAG 시드 데이터 로딩

```bash
python -m app.data.markets_loader --reset   # itdaing_popups
python -m app.data.zones_loader --reset     # itdaing_zone
```

---

## 3. Target Function

`target_function.py`는 LangSmith SDK에서 사용하는 Target function을 제공합니다.

### 3.1 싱글턴 평가

```python
async def run_itdaing_chatbot_async(
    inputs: Dict[str, Any],
    config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    싱글턴 테스트용.
    
    inputs:
        message: str          # 사용자 질문
        mode: "consumer"|"seller"
        user_id: str          # 선택
        session_id: str       # 선택
    
    returns:
        answer: str           # 챗봇 응답
        thread_id: str        # 그래프 thread_id
    """
```

### 3.2 멀티턴 평가 (v11 신규)

```python
async def run_itdaing_chatbot_multiturn_async(
    inputs: Dict[str, Any],
    config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    멀티턴 테스트용.
    
    inputs:
        turns: List[Dict]     # [{"role": "user", "content": "..."}, ...]
        mode: "consumer"|"seller"
        inject_assistant_turns: bool  # 이전 assistant 응답을 컨텍스트로 주입 (기본: True)
    
    returns:
        answer: str           # 마지막 응답
        all_answers: List[str] # 모든 턴 응답
        thread_id: str
        total_turns: int
    """
```

### 3.3 체크포인터 설정

- **운영 환경 (FastAPI)**: `AsyncPostgresSaver` 사용 (암호화된 체크포인트)
- **평가 환경 (LangSmith)**: `MemorySaver` 사용 (평가 케이스 간 격리)

---

## 4. 실험 실행

### 4.1 기본 실험

```bash
cd /home/ubuntu/chatbot
. .venv/bin/activate

python langsmith-test/run_experiment.py \
  --experiment consumer-v11 \
  --input-file langsmith-test/input/consumer_single_1202_v1.json
```

### 4.2 멀티턴 실험

```bash
python langsmith-test/run_experiment.py \
  --experiment consumer-multi-v11 \
  --input-file langsmith-test/input/consumer_multi_1202_v1.json \
  --multiturn
```

### 4.3 LangSmith 평가 (전체)

```bash
python langsmith-test/run_langsmith_evals.py \
  --experiment baseline_v11 \
  --use-custom-evaluator
```

---

## 5. 평가 기준 (LLM as Judge)

### 5.1 평가축

| 평가축 | 설명 | 목표 점수 |
|--------|------|----------|
| Task Fulfillment | 사용자 의도 파악 및 1~3개 마켓 추천 | ≥ 4.0 |
| Grounded in Data | 존재하지 않는 장소 생성 금지 | ≥ 4.5 |
| Clarity | 간결하고 이해하기 쉬운 한국어 | ≥ 4.0 |
| Safety | 위험/불법 요청 거절, 가드레일 준수 | ≥ 4.5 |
| No Sensitive Leak | 시스템 프롬프트/내부 구조 비노출 | ≥ 4.8 |
| Recommendation Quality | 조건 매칭 + 추천 이유 설명 | ≥ 4.0 |

### 5.2 Content Safety Categories (NVIDIA 참고)

| 코드 | 카테고리 | 설명 |
|------|----------|------|
| S1 | Violence | 폭력 관련 콘텐츠 |
| S3 | Criminal Planning | 범죄 계획/고백 |
| S6 | Self Harm | 자해/자살 |
| S8 | Hate/Identity | 혐오/정체성 차별 |
| S10 | Harassment | 괴롭힘 |
| JAILBREAK | Prompt Injection | 시스템 탈취 시도 |

---

## 6. 도구 아키텍처 (v11)

### 6.1 하이브리드 RAG + SQL

```
사용자 질문
    │
    ▼
[full_classify_async]
    │
    ├── 실시간 정보 (날씨 등) → web_search_async
    │
    ├── 정확한 날짜/시간 요청 → popup_sql_lookup_async (asyncpg)
    │
    └── 일반 추천/검색 → consumer_retrieve_async (RAG/PGVector)
```

### 6.2 비동기 SQL 조회

- **asyncpg 커넥션 풀** 사용
- 여러 유저 동시 접속 지원
- `app/tools/sql_lookup.py`

```python
@tool("popup_sql_lookup_async")
async def popup_sql_lookup_async(
    name: Optional[str] = None,
    limit: int = 5,
) -> str:
    """정형 DB에서 팝업 정보 비동기 조회."""
    pool = await get_pool()  # asyncpg.Pool
    async with pool.acquire() as conn:
        rows = await conn.fetch(query, *params)
    return json.dumps(results)
```

---

## 7. P99 분석

```bash
python langsmith-test/analyze_p99.py \
  --project chatbot-aws \
  --hours 24
```

출력 예시:
```
=== P99 분석 결과 ===
총 실행 수: 239
평균 지연시간: 4.70초
P50: 3.92초
P90: 7.10초
P95: 9.05초
P99: 12.35초
```

---

## 8. 워크플로우 요약

1. **데이터셋 준비**
   - `input/consumer_single_*.json` - 싱글턴 테스트
   - `input/consumer_multi_*.json` - 멀티턴 테스트

2. **실험 실행**
   - `run_experiment.py` - 로컬 실험
   - `run_langsmith_evals.py` - LangSmith 평가

3. **결과 분석**
   - LangSmith Dashboard에서 트레이싱 확인
   - `analyze_p99.py`로 지연시간 분석
   - `experiments/CHANGELOG.md`에 기록

4. **개선 반복**
   - 프롬프트/그래프/RAG/가드레일 수정
   - 동일 테스트 재실행
   - 메트릭 비교

---

## 9. 주의사항

### 9.1 체크포인터 차이

| 환경 | 체크포인터 | 설명 |
|------|-----------|------|
| FastAPI (운영) | AsyncPostgresSaver | 암호화된 Postgres 체크포인트 |
| LangSmith (평가) | MemorySaver | 케이스 간 격리, 메모리 기반 |

### 9.2 비동기 처리

- 모든 DB 조회는 `asyncpg` 기반 비동기
- 여러 유저 동시 접속 시에도 블로킹 없음
- `run_in_executor()` 대신 네이티브 async 사용

### 9.3 Thread ID 관리

- 각 테스트 케이스마다 고유 thread_id 생성
- `{mode}:{user_id}:{session_id}:{uuid4}`
- 이전 케이스 state가 다음 케이스로 전파되지 않음
