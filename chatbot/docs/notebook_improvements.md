# b4c_async_multi_psql_nosum.ipynb 기반 그래프 개선 요약

이 문서는 `b4c_async_multi_psql_nosum.ipynb` 노트북과 기존 bot4c/bot4s 설계를
현재 FastAPI + LangGraph 기반 챗봇에 어떻게 이식·개선했는지 정리한 기록입니다.

## 1. 범위와 전제

- **프롬프트 보존 원칙**
  - 노트북과 기존 스크립트에 있던 **system / user / tool 지침 프롬프트는 의미와 톤을 유지**한다.
  - 변경은 아래 경우에만 허용한다.
    - 코드 구조에 맞게 변수 이름, placeholder, 줄바꿈을 정리하는 수준
    - 노트북에서만 유효한 설명(예: “아래 셀에서 실행…”)을 제거하는 수준
- **대상 컴포넌트**
  - 노트북: `b4c_async_multi_psql_nosum.ipynb`
  - 소비자 그래프:
    - `app/chains/consumer/rag.py`
    - `app/chains/shared/prompts.py`
    - `app/graphs/consumer/nodes.py`
    - `app/graphs/consumer/graph.py`
  - 판매자 그래프:
    - `app/chains/seller/rag.py`
    - `app/graphs/seller/nodes.py`
    - `app/graphs/seller/graph.py`

## 2. 프롬프트 보존 현황

### 2.1 소비자(Consumer) 쪽

노트북 / 옛 코드의 프롬프트가 어디로 갔는지 매핑합니다.

- **에이전트 페르소나 & 행동지침**
  - 원본: 노트북의 `agent_system_prompt` (광주 플리마켓 추천 전문가, 도구 사용 우선순위, 답변 스타일 등)
  - 현재:
    - `app/chains/shared/prompts.py` 의 `CONSUMER_AGENT_SYSTEM_PROMPT` 상수에 **원문을 그대로 옮겨** system 메시지로 사용
    - `app/chains/shared/prompts.py` 의 `CONSUMER_RAG_PROMPT` 가 위 system 메시지와 사용자 입력(요약/질문/문서)을 결합
    - `app/graphs/consumer/nodes.py` 의 `router_system_prompt`, `case_classification_system_prompt`,
      `basic_system_prompt`, `hallucination_prompt`, `rewrite_dictionary + rewrite_prompt`
      → 모두 잇다잉 페르소나/지침을 명시하도록 문구를 재정렬
  - 유지 내용:
    - “광주 플리마켓/팝업 추천”, “도구 사용 우선순위(retrieve → web_search)”,
      “친근하고 위트있는 한국어” 지침을 그대로 유지.

- **질문 분류 / case classification**
  - 원본: 노트북 내 질문 분류 로직 + case 라벨 (`region_keyword`, `date`, `market_info` 등)
  - 현재:
    - `app/graphs/consumer/nodes.py` 의 `CaseClassification` 모델 + `case_classification_system_prompt`
  - 차이:
    - 노트북에서는 체인 내부에서 바로 사용되던 프롬프트를,
      지금은 **`with_structured_output` + Pydantic 모델**로 구조화하여 LangGraph 노드에 연결.

- **할루시네이션 체크 & 재작성(rewrite)**
  - 원본: 노트북 내 hallucination 판정용 프롬프트 + 사전 기반 rewrite 규칙
  - 현재:
    - `hallucination_prompt`, `rewrite_dictionary`, `rewrite_prompt`가
      `app/chains/shared/prompts.py` 및 `app/graphs/consumer/nodes.py` 에 거의 동일한 텍스트로 존재.
  - 유지 내용:
    - “문서 기반인지/할루시네이션인지 판정”, “사전 기반 표현 개선”, “마켓/플리/팝업 단어 반드시 포함”
      등의 요구사항을 그대로 반영.

### 2.2 판매자(Seller) 쪽

- **존 추천용 router / case classification**
  - 원본: `bot4s` 설계(존 추천 라우팅, 셀러 관점 case 라벨)
  - 현재:
    - `app/graphs/seller/nodes.py` 의
      `router_system_prompt`, `CaseClassification`, `case_classification_system_prompt`
  - 유지 내용:
    - “광주 내에서 존 추천”, “셀러 입장에서의 적합도/트래픽/고객층” 등 도메인 문맥 유지.

- **rewrite / basic 응답**
  - 원본: 존 추천을 명시하는 한 문장 쿼리로 재작성하는 프롬프트, 기본 안내 챗봇 프롬프트
  - 현재:
    - `rewrite_dictionary`, `rewrite_prompt`, `basic_system_prompt`가
      `app/graphs/seller/nodes.py` 에 구현되며,
      `SELLER_AGENT_SYSTEM_PROMPT` 지침(존 추천/셀러 보조, retrieve 우선)을 반영.
  - 유지 내용:
    - “존 추천을 명확히 드러내는 한 문장”, “말머리/따옴표/리스트 금지” 등의 제약을 그대로 유지.

### 2.3 기준 agent_system_prompt 전문

아래 텍스트는 노트북의 `agent_system_prompt` 원문이며,
`app/chains/shared/prompts.py` 의 `CONSUMER_AGENT_SYSTEM_PROMPT` 로 그대로 보존됩니다.

```
당신은 광주광역시 플리마켓 및 팝업스토어 추천 전문가 '잇다잉(Itdaing)'입니다.

당신의 목표는 사용자에게 딱 맞는 마켓을 추천하거나 관련 정보를 제공하는 것입니다.

[행동 지침]

1. **도구 사용 우선순위**:

   - 마켓 추천, 장소, 시간 등 구체적인 정보는 반드시 `retrieve` 도구를 가장 먼저 사용해 DB에서 찾으세요.

   - DB에 정보가 없거나, 일반적인 지역, 날씨/뉴스 등 일반적인 정보가 필요할 때만 `web_search`를 사용하세요.

2. **판단 및 분류**:

   - 사용자가 "놀러 갈 곳 추천해줘"처럼 모호하게 말해도, 마켓 추천 의도로 파악하고 DB를 검색하세요.

   - 전혀 관련 없는 질문(예: 코딩 질문, 수학 문제)에는 정중히 거절하세요.

3. **답변 스타일**:

   - 친근하고 공손한 톤을 유지하세요.

   - 답변은 한국어로 작성하세요.

   - 유머러스하고 위트있게 응답해주세요.
```

판매자 전용 `SELLER_AGENT_SYSTEM_PROMPT` 는 위 지침을 그대로 따르되,
“판매자가 존을 고르는 상황”과 “retrieve → web_search” 우선순위를 강조하는 문장만 추가했습니다.

## 3. 현재 구조에 이미 반영된 주요 개선점

### 3.1 LangGraph + AsyncPostgresSaver 도입

- 노트북:
  - AsyncPostgresSaver를 사용해 `graph = graph_builder.compile(checkpointer=checkpointer)` 패턴을 보여주고,
    `thread_id` 단위로 상태를 저장하는 예제를 포함.
- 현재:
  - `app/db/postgres.py` 에서 `AsyncPostgresSaver`를 올바른 async 컨텍스트로 초기화.
  - `app/main.py` 에서 FastAPI lifespan(startup/shutdown) 안에서 checkpointer를 생성/정리.
  - `configurable.thread_id` 전략(`consumer|seller:{user_id}:{session_id}`)을 README와 코드에 통일.

**효과**  
- 멀티유저 환경에서 **대화 상태가 Postgres에 안전하게 축적**되고,
  Spring/프론트가 `thread_id`만 유지하면 언제든지 복구 가능.

### 3.2 ToolNode 기반 Retrieval + Web Search

- 노트북:
  - `@tool retrieve`, `@tool web_search`, `ToolNode(tools)` + `should_continue` 조건부 엣지 구조.
- 현재:
  - `app/tools/` 패키지
    - `retrieval.py`: `consumer_retrieve(_async)`, `seller_retrieve(_async)`  
      → PGVector + DuckDuckGoFallback을 하나의 도구로 캡슐화
    - `web_search.py`: `web_search(_async)` → DuckDuckGo 전용 웹검색 도구
  - 그래프 레벨:
    - `app/graphs/consumer/graph.py`, `app/graphs/seller/graph.py`:
      - `case_classification → schedule_tool → ToolNode → consume_tool → generate`
      - 문서가 비어 있고 `WEBSEARCH_ENABLED=true` 이면 `web_search` Tool을 자동 재호출.
  - 상태(State) 레벨:
    - `AgentState`에 `pending_tool_call_id`, `last_tool_payload`, `needs_web_search`, `web_search_attempted` 등을 추가해
      Tool 호출 상태를 명시적으로 관리.

**효과**  
- 노트북에서 하던 “도구 먼저 → 답변 나중” 패턴을  
  **동기/비동기 그래프 양쪽에서 일관성 있게 재현**.
- LangSmith trace 상에서 `tool_call → tool_output → rag_generate` 흐름이 그대로 남아
  디버깅과 관측이 훨씬 쉬워짐.

### 3.3 Async-first 그래프 + Streaming

- 노트북:
  - `graph.astream({...}, config, stream_mode="values")` 를 통해 콘솔에 토큰 단위(또는 단계 단위) 응답을 출력.
- 현재:
  - `app/graphs/{consumer,seller}/graph.py`:
    - 각 노드에 `*_async` 버전을 구현 (`router_async`, `generate_async`, `summarize_messages_async` 등).
  - FastAPI:
    - `/api/chat/(consumer|seller)/async`, `/async/stream` 엔드포인트에서
      LangGraph `astream` 결과를 **JSON 라인 diff**로 스트리밍.
  - README:
    - `curl -N` 예시로 실제 스트림 형태를 검증하는 방법 문서화.

**효과**  
- 노트북 수준의 “실시간 대화 경험”을 **Spring → FastAPI → 프론트** 체인 전체에서 사용할 수 있음.

### 3.4 thread_id / restart_thread 전략

- 노트북:
  - 단일 `thread_id` (예: `"jongha_async_03"`)를 사용해 여러 턴의 대화를 실험.
- 현재:
  - `thread_id` 규칙:
    - 소비자: `consumer:{user_id}:{session_id or default}`
    - 판매자: `seller:{user_id}:{session_id or default}`
  - `restart_thread` 플래그:
    - Request에 `restart_thread=true`를 주면 내부적으로 UUID suffix를 붙여 새 thread를 시작.
    - 에이전트 실패/네트워크 오류로 thread가 꼬인 경우, 클라이언트가 명시적으로 리셋 가능.

**효과**  
- 노트북에서는 수동으로 thread_id를 바꿔야 했던 작업을,
  실제 서비스에서는 **API 레벨에서 안전하게 제어**할 수 있음.

### 3.5 환경 변수 정리 및 Web Search 설정

- 노트북:
  - `.env`에서 직접 키를 로드하고, 코드 셀에서 `os.environ[...]` 을 수동 설정.
- 현재:
  - `chatbot.env` + `scripts/generate-chatbot-env.sh`:
    - `WEBSEARCH_ENABLED`, `WEBSEARCH_PROVIDER`, `WEBSEARCH_TOP_K` 를 포함.
  - `app/config.Settings`:
    - OpenAI, PG, LangSmith, WebSearch 관련 설정을 중앙에서 관리.
  - `app/utils/search.WebSearchClient`:
    - 설정 값에 따라 DuckDuckGoSearchRun을 활성/비활성화.

**효과**  
- 노트북의 실험용 설정을 **EC2/프로덕션 환경에서 재현 가능한 형태**로 정리.

### 3.6 그래프 시각화 아티팩트

- `scripts/render_graphs.py` 실행 시
  - `artifacts/graphs/consumer_graph.mmd|.png`
  - `artifacts/graphs/seller_graph.mmd|.png`
  가 생성되어 LangGraph 단계/엣지를 한눈에 볼 수 있다.
- README와 `docs/chain_graph_audit.md` 에서 해당 파일을 직접 링크하여
  소비자/판매자 그래프 구조를 빠르게 검토할 수 있게 했다.

## 4. 앞으로 적용하면 좋은 개선 아이디어

### 4.1 Tool 세분화 및 도메인별 도구 추가

- 현재:
  - 소비자: `consumer_retrieve` 하나로 모든 플리마켓 검색을 처리.
  - 판매자: `seller_retrieve` 하나로 모든 존/상권 검색을 처리.
- 아이디어:
  - 카테고리/상황별 도구 분리:
    - 예: `retrieve_night_market`, `retrieve_family_friendly`, `retrieve_indoor` 등.
  - 판매자용:
    - `retrieve_high_traffic_zone`, `retrieve_low_rent_zone` 등 셀러 KPI에 맞춘 도구.

### 4.2 할루시네이션/품질 관리 강화

- 노트북 & 현 코드 모두 “문서 기반 여부”만 1차로 판정.
- 아이디어:
  - Hallucination 라벨에 따라:
    - `rewrite` 뿐만 아니라 **대화형 clarification 질문**을 추가 (예: “정확한 날짜를 알려주실 수 있나요?”).
  - seller 쪽은 “존 데이터가 충분하지 않은 경우”를 사용자에게 명시하고,
    web search에 의존했다는 점을 설명하는 후처리 프롬프트 추가.

### 4.3 메모리/요약 전략 개선

- 노트북에서는 summarizer + delete_messages 조합을 보여줬으나,
  실제 서비스에서는 성능/비용 문제로 일부만 반영.
- 아이디어:
  - LangGraph의 Checkpoint를 활용해:
    - “요약 버전”과 “Raw 버전”을 모두 저장하고, 오래된 Raw는 정기적으로 정리하는 백그라운드 작업 추가.
  - 장기 기억용 테이블(예: 유저 선호도, 자주 묻는 질문)을 별도 Postgres 테이블/VectorStore로 분리.

### 4.4 공통 Agent 레이어 도입

- 현재:
  - consumer/seller 그래프가 각각의 프롬프트와 노드를 가지고 있지만,
    구조는 매우 유사.
- 아이디어:
  - `app/graphs/shared/agent.py` 등에 공통된 “Agent 스켈레톤”을 정의:
    - `extract_query → router → case_classification → schedule_tool → ...` 구조를 템플릿화.
  - consumer/seller는 프롬프트와 Tool 세트만 바꿔 끼우는 형태로 단순화.

## 5. 결론

- `b4c_async_multi_psql_nosum.ipynb`와 기존 설계에 담긴 **프롬프트/행동지침/도구 사용 철학은 그대로 유지**하면서,
  실제 서비스 환경에 맞게 LangGraph, AsyncPostgresSaver, ToolNode, FastAPI HTTP API로 확장했다.
- 이 문서는
  1) 노트북 대비 어떤 점이 동일하게 유지되었는지,  
  2) 어떤 점이 구조적으로 개선되었는지,  
  3) 앞으로 어디를 확장하면 좋은지  
  를 한 눈에 볼 수 있는 참고용 메모이다.