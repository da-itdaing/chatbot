## Chain & Graph Dependency Audit

이 문서는 LangGraph 기반 소비자/판매자 챗봇이 어떤 파일로 분리되어 있는지,
그리고 잇다잉(Itdaing) 페르소나 프롬프트가 어떻게 공유되는지를 추적하기 위한 최신 구조 요약입니다.

### 1. 체인 계층 (`app/chains`)

| 항목 | 소비자 | 판매자 | 비고 |
| --- | --- | --- | --- |
| Agent system prompt | `app/chains/shared/prompts.py::CONSUMER_AGENT_SYSTEM_PROMPT` | `app/chains/shared/prompts.py::SELLER_AGENT_SYSTEM_PROMPT` | 노트북 `agent_system_prompt` 원문을 그대로 사용 |
| RAG 프롬프트 템플릿 | `CONSUMER_RAG_PROMPT` | `SELLER_RAG_PROMPT` | `summary/question/context` 입력을 동일한 구조로 바인딩 |
| RAG 체인 빌더 | `app/chains/consumer/rag.py::build_consumer_rag_chain` | `app/chains/seller/rag.py::build_seller_rag_chain` | `ChatOpenAI`(temperature 0) + PromptTemplate |
| LLM 초기화 방식 | `_api_key_provider()` 콜백으로 OpenAI 키 주입 | 동일 | LangSmith/LangChain tracing을 자동으로 따라감 |
| Output 파서 | LangChain Message 객체 그대로 반환 | 동일 | `generate` 노드에서 `response.content`를 문자열로 변환 |

### 2. 그래프 계층 (`app/graphs`)

| 항목 | 소비자 | 판매자 | 비고 |
| --- | --- | --- | --- |
| 상태/노드 모듈 | `app/graphs/consumer/nodes.py` | `app/graphs/seller/nodes.py` | `AgentState` 구조와 Tool 라우팅 로직만 다르게 구성 |
| 그래프 빌더 | `app/graphs/consumer/graph.py::build_consumer_graph(_async)` | `app/graphs/seller/graph.py::build_seller_graph(_async)` | `schedule_tool → ToolNode → consume_tool → generate` 플로우 동일 |
| Router / Case prompts | `router_system_prompt`, `case_classification_system_prompt` | 동일 명칭으로 seller 전용 문맥 | 모두 잇다잉 페르소나/도구 우선순위를 명시 |
| Basic 응답 노드 | `basic_generate(_async)` | `basic_generate(_async)` | 광주 외 질문/악의적 요청 시 "지원되지 않는 서비스" 규칙을 공유 |
| 요약/트렁케이션 | `summarize_messages(_async) → truncate_messages` | 동일 | LangGraph Checkpoint에 저장되는 히스토리를 제어 |

### 3. 공통 유틸과 Tool 통합

- `app/graphs/shared/utils.py`:  
  - `latest_user_message`, `format_messages`, `extend_with_web_results(_async)` 로 소비자/판매자 노드에서 동일한 메시지/문서 처리를 재사용.
- `app/tools/retrieval.py`:  
  - `consumer_retrieve(_async)`, `seller_retrieve(_async)` 가 PGVector + DuckDuckGo fallback을 하나의 LangGraph Tool 인터페이스로 노출.
- `app/tools/web_search.py`:  
  - `web_search(_async)` Tool이 DuckDuckGo 결과를 `Document` 리스트에 맞는 JSON으로 반환.
- Tool 호출 흐름:  
  `case_classification → schedule_tool → ToolNode → consume_tool → generate`  
  (필요 시 `consume_tool` 이 `needs_web_search` 플래그를 세팅해 `web_search` Tool을 추가 라운드로 실행).

### 4. LangGraph 시각화 아티팩트

- `scripts/render_graphs.py` 실행 시 아래 파일이 갱신된다.
  - `artifacts/graphs/consumer_graph.mmd`
  - `artifacts/graphs/consumer_graph.png`
  - `artifacts/graphs/seller_graph.mmd`
  - `artifacts/graphs/seller_graph.png`
- `.mmd` 파일은 Mermaid 소스이므로 Git 리뷰에서 구조 차이를 텍스트로 확인할 수 있다.
- `.png` 파일은 README/문서에서 바로 열람할 수 있도록 900px 폭으로 렌더링되어 있다.

### 5. LangSmith & thread_id 맥락

- 그래프 빌더는 FastAPI `app.state` 에 저장되며, `/api/chat/...` 라우터가
  `config={"configurable": {"thread_id": thread_id}}` 로 실행한다.
- Thread 규칙:  
  소비자 `consumer:{user_id}:{session_id}`, 판매자 `seller:{user_id}:{session_id}`.  
  `restart_thread=true` 요청 시 UUID suffix를 붙여 새 체크포인트를 생성.
- LangSmith trace에서는 `tool_call → tool_output → rag_generate → summarize` 단계가
  두 그래프 모두 동일하게 찍히므로, 소비자/판매자 흐름을 비교 디버깅하기 쉽다.

본 문서는 구조가 추가로 변할 때마다 (예: 도구 확장, 새로운 노드 추가) 업데이트하여,
Spring ↔ FastAPI ↔ LangGraph 사이 의존성을 추적하는 기준으로 삼는다.


