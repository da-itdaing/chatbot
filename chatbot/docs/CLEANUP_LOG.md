# 챗봇 폴더 정리 로그

> 최종 정리일: 2025-11-30

## 개요

이 문서는 `/home/ubuntu/chatbot` 폴더의 정리 과정과 제거된 파일들을 기록합니다.

---

## 정리된 파일 목록

### 1. 중복 파일 제거

| 파일 | 제거 이유 | 대체 파일 |
|------|----------|----------|
| `docs/test_prompt.md` | 중복 | `langsmith-test/input/test_prompt.md` |

**설명**: `docs/test_prompt.md`와 `langsmith-test/input/test_prompt.md`가 동일한 내용을 담고 있었으며, `langsmith-test/input/` 버전이 더 완전하고 최신 메타데이터 스키마를 반영하므로 docs 버전을 제거했습니다.

---

## 현재 폴더 구조

정리 후 최종 구조:

```
chatbot/
├── app/                          # FastAPI + LangGraph 애플리케이션
│   ├── chains/                   # LangChain 체인 정의
│   │   ├── consumer/            # 소비자용 RAG 체인
│   │   ├── seller/              # 판매자용 RAG 체인
│   │   └── shared/              # 공통 프롬프트
│   ├── config.py                # 환경 설정
│   ├── data/                    # 시드 데이터 로더
│   ├── db/                      # Postgres/PGVector 헬퍼
│   ├── graphs/                  # LangGraph 정의
│   │   ├── consumer/           # 소비자 그래프
│   │   ├── seller/             # 판매자 그래프
│   │   └── shared/             # 공통 노드/유틸
│   ├── main.py                  # FastAPI 엔트리포인트
│   ├── routers/                 # API 라우터
│   ├── tools/                   # LangGraph 도구 (retrieval, web_search)
│   └── utils/                   # 유틸리티
│
├── artifacts/                    # 생성된 아티팩트
│   └── graphs/                  # 그래프 시각화 (mermaid, png)
│
├── data/                         # 크롤링/외부 데이터
│   ├── images/                  # 크롤링된 이미지
│   ├── crawler_state.json       # 크롤러 상태
│   ├── events_with_s3_images.json
│   └── scraped_events.json
│
├── docs/                         # 문서
│   ├── INFRASTRUCTURE_CAPACITY.md  # 인프라 용량 분석
│   ├── QUALITY_TESTING.md          # 품질 검사 종합 가이드
│   ├── CHATBOT_EVALUATION.md       # 성능 평가 상세 문서
│   ├── EVALUATION_TRACKING.md      # 성능 평가 트래킹
│   └── CLEANUP_LOG.md              # 이 문서
│
├── langsmith-test/               # LangSmith 테스트 & 평가
│   ├── evaluators/              # 커스텀 평가기
│   ├── input/                   # 테스트 프롬프트 (기준 데이터셋)
│   │   ├── test_prompt.md       # 케이스 요약 문서
│   │   ├── test_prompts.json    # 전체 278개 케이스
│   │   ├── test_prompts_*.json  # 서브셋 (30_se, 100, hard 등)
│   │   └── ...
│   ├── deep_check_consumer.py   # Consumer 품질 체크
│   ├── label_dataset.py         # LLM 기반 라벨링
│   ├── run_langsmith_evals.py   # LangSmith 평가 러너
│   ├── run_consumer_evals.py    # Consumer 전용 평가
│   ├── target_function.py       # 평가 타겟 함수
│   ├── upload_dataset.py        # Dataset 업로드
│   ├── validate_test_prompts.py # 프롬프트 검증
│   └── README.md                # 테스트 가이드
│
├── logs/                         # 로그 파일
│
├── scripts/                      # 유틸리티 스크립트
│   ├── benchmark_suite.py       # 벤치마크 스위트
│   ├── daily_crawler.py         # 일일 크롤러
│   ├── render_graphs.py         # 그래프 렌더링
│   └── ...
│
├── chatbot.env                   # 환경 변수
├── requirements.txt              # Python 의존성
└── README.md                     # 프로젝트 README
```

---

## 유지되는 파일 설명

### 핵심 코드

| 경로 | 역할 |
|------|------|
| `app/` | FastAPI 애플리케이션 + LangGraph 챗봇 |
| `app/graphs/` | Consumer/Seller LangGraph 정의 |
| `app/chains/` | RAG 체인 및 프롬프트 |
| `app/tools/` | retrieval, web_search 도구 |

### 테스트 & 평가

| 경로 | 역할 |
|------|------|
| `langsmith-test/` | LangSmith 연동 테스트 전체 |
| `langsmith-test/input/` | 기준 테스트 프롬프트 (JSON) |
| `langsmith-test/run_*.py` | 평가 실행 스크립트 |
| `scripts/benchmark_suite.py` | 로컬 벤치마크 |

### 문서

| 경로 | 역할 |
|------|------|
| `README.md` | 프로젝트 개요 및 API 명세 |
| `docs/INFRASTRUCTURE_CAPACITY.md` | 인프라 용량 분석 |
| `docs/QUALITY_TESTING.md` | 품질 검사 종합 가이드 |
| `docs/CHATBOT_EVALUATION.md` | 성능 평가 상세 |
| `docs/EVALUATION_TRACKING.md` | 평가 트래킹 |

### 데이터

| 경로 | 역할 |
|------|------|
| `data/` | 크롤링된 이벤트/이미지 데이터 |
| `artifacts/graphs/` | 그래프 시각화 결과물 |

---

## 권장 .gitignore 항목

다음 항목들은 Git에 포함되지 않아야 합니다:

```gitignore
# Python 캐시
__pycache__/
*.pyc
*.pyo

# 가상환경
.venv/
venv/

# 환경 변수 (실제 값)
chatbot.env
!chatbot.env.template

# 로그
logs/*.log

# 벤치마크 결과 (선택적)
benchmark_results/

# IDE
.idea/
.vscode/
*.swp
```

---

## 정리 기준

향후 파일 정리 시 다음 기준을 적용합니다:

1. **중복 제거**: 동일한 내용의 파일이 여러 위치에 있으면 가장 적절한 위치에만 유지
2. **캐시 제거**: `__pycache__`, `.pyc` 등 자동 생성 파일은 gitignore로 관리
3. **레거시 정리**: 더 이상 사용되지 않는 스크립트/설정은 제거 또는 `deprecated/` 이동
4. **문서 통합**: 분산된 문서는 `docs/` 폴더로 통합

---

## 변경 이력

| 날짜 | 변경 내용 | 작업자 |
|------|----------|--------|
| 2025-11-30 | 초기 정리 - 중복 test_prompt.md 제거 | AI |
| 2025-11-30 | 문서 체계 정비 (QUALITY_TESTING, EVALUATION 등) | AI |










