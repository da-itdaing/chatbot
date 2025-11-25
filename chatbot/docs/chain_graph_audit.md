## Chain & Graph Dependency Audit

분리 리팩터링 전에 현재 구조에서 소비자/판매자 흐름이 어떻게 섞여 있는지 정리했습니다.  
프롬프트 내용과 호출 순서는 유지하고, 파일만 재배치할 예정입니다.  
※ 아래 표는 리팩터링 이전(`consumer_graph.py`, `seller_graph.py`) 기준입니다.

### 1. 체인 계층 (`app/chains`)

| 항목 | 소비자 | 판매자 | 비고 |
| --- | --- | --- | --- |
| RAG 체인 파일 | `app/chains/rag_chain.py` | 없음 | consumer 전용 빌더만 존재 |
| 프롬프트 정의 | 파일 내부 `ChatPromptTemplate` | `app/graphs/seller_graph.py`에 직접 포함 | 프롬프트 내용 자체는 동일하게 유지해야 함 |
| LLM 초기화 | `ChatOpenAI(model=settings.openai_model)` | 각 그래프 파일의 `_llm` | API 키 전달 방식 동일하게 유지 |
| Output parser | `StrOutputParser` | 그래프 내 PromptTemplate → `ChatOpenAI` | 공통 유틸 없음 |

### 2. 그래프 계층 (`app/graphs`)

| 항목 | 소비자 그래프 | 판매자 그래프 | 비고 |
| --- | --- | --- | --- |
| 상태 정의 | `consumer_graph.AgentState` | `seller_graph.AgentState` | 필드 구성이 유사하지만 별도 선언 |
| 노드 함수 | 라우팅/분류/RAG/재작성/요약이 단일 파일에 혼재 | 동일 | 중복 로직 많음 |
| Web search fallback | `_web_search_client` + 동기/비동기 함수 | 동일 | `app/utils/search.WebSearchClient` 사용 |
| Retriever | `get_markets_vectorstore`를 파일 시작에서 초기화 | `get_zones_vectorstore`를 파일 시작에서 초기화 | async 그래프에서 직접 사용 |
| 그래프 빌더 | `build_consumer_graph_sync/async` | `build_seller_graph_sync/async` | LangGraph 구성 자체는 유사 |

### 3. 공통 의존성

| 컴포넌트 | 사용처 |
| --- | --- |
| `app.config.get_settings()` | 두 그래프와 RAG 체인 모두 동일 |
| `app.utils.search.WebSearchClient` | 두 그래프에서 직접 생성 (singleton) |
| `app/db/postgres.py` 벡터 스토어 헬퍼 | `get_markets_vectorstore`, `get_zones_vectorstore` 각각 호출 |

### 4. 리팩터링 목표 정리

1. **체인**: `app/chains/consumer/rag.py`, `app/chains/seller/rag.py`로 나누고, 기존 프롬프트/LLM 구성을 그대로 옮긴다.
2. **그래프**: `app/graphs/consumer/` 및 `app/graphs/seller/` 서브패키지로 나누어, 노드·빌더·async 버전을 모듈 단위로 분리한다.
3. **공통 유틸**: 메시지 포맷터, web search fallback 등 중복 로직을 `app/graphs/shared/`로 추출한다.
4. **최종 목표**: 구조만 분리하여 이후 LangGraph ToolNode 기반 리팩터링(노트북 스타일)을 쉽게 진행할 수 있게 한다.

### 5. 리팩터링 결과 (요약)

- 체인: `app/chains/{consumer,seller}/rag.py` + `shared/prompts.py` 로 정리하여  
  노트북과 동일한 프롬프트/LLM 흐름을 유지한 채 모듈화 완료.
- 그래프: `app/graphs/{consumer,seller}/` 에 `nodes.py`(상태/노드)와 `graph.py`(빌더)를 분리하고,  
  `shared/utils.py`에서 메시지 포맷/웹검색 Fallback을 재사용하도록 통합.
- HTTP 레벨: `app/routers/chat_{consumer,seller}.py` 에서 동기/스트리밍/async/async-stream 엔드포인트를  
  동일한 스키마로 제공하며, `thread_id` prefix(`consumer:` ↔ `seller:`)만 구분한다.
- 문서: README에 새 폴더 구조와 판매자 Async/Streaming 사용법을 추가하고,  
  Spring ↔ FastAPI 계약 섹션에 두 챗봇의 parity를 명시했다.

본 문서를 기반으로 리팩터링 진행 후 README/문서에 새 구조를 반영할 예정입니다.


