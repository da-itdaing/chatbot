## compare_b4c.md

### 1. 개요
- 기준 노트북: `b4c_async_multi_psql_nosum.ipynb`
- 목적: 노트북에서 제안한 설계/패턴 중 현재 FastAPI 기반 챗봇 코드베이스에 반영된 항목과 미반영 항목을 명확히 정리

---

### 2. 노트북 주요 구성 요소
1. **LangGraph StateGraph**: `agent → (tools | delete_messages) → agent/END` 플로우
2. **도구 계층**: `retrieve`(PGVector retriever) + `web_search`(DuckDuckGo)
3. **AsyncPostgresSaver + AsyncConnectionPool** 기반 체크포인터
4. **메시지 정리(delete_messages) 노드**와 요약(summarizer) 옵션
5. **주피터 인터랙티브 루프**(`graph.astream` 기반 스트리밍)
6. **LangSmith 추적 설정**
7. **마켓 데이터 전처리 및 PGVector 적재 유틸**

---

### 3. 현재 챗봇에 이미 반영된 항목
| 노트북 요소 | 적용 위치 | 비고 |
| --- | --- | --- |
| LangGraph StateGraph 패턴 (agent ↔ tools ↔ cleanup) | `app/graphs/consumer_graph.py`, `app/graphs/seller_graph.py` | `build_*_graph_sync/async` 쌍으로 분리하여 동기·비동기 그래프 모두 동일한 상태머신 공유 |
| `retrieve` + `web_search` 도구 패턴 | `app/chains/rag_chain.py`, `app/utils/search.py` | RAG 체인 내부에서 PGVector retriever 사용, 웹검색은 DuckDuckGo 기반 `WebSearchClient`로 래핑 |
| AsyncPostgresSaver 체크포인트 | `app/db/postgres.py`, `app/main.py` | FastAPI `startup`에서 `AsyncPostgresSaver.from_conn_string` 초기화 후 앱 전역 상태로 공유 |
| 메시지 트리밍(delete_messages) | `app/graphs/*` | LangGraph 노드로 유지, 노트북과 동일하게 최근 N개만 보존 |
| LangSmith 추적 | `app/config.py`, `.env`, README | `LANGCHAIN_TRACING_V2`, `LANGCHAIN_PROJECT`, `LANGCHAIN_API_KEY` 세팅 및 사용법 문서화 |
| 비동기 스트리밍 실행 (`graph.astream`, `astream_events`) | `app/routers/chat_consumer.py`, `app/routers/chat_seller.py` | `/async`, `/async/stream` 엔드포인트로 Spring ↔ FastAPI 커넥터 대응 |
| PGVector 적재 흐름 | `app/data/markets_loader.py`, `app/data/zones_loader.py` | 노트북의 JSON→Document→Vectorstore 과정을 독립 스크립트로 일반화 |
| 시스템 프롬프트/행동 지침 | `app/graphs/*` | 노트북에서 정의한 페르소나·행동 규칙을 한국어 주석과 함께 유지 |

---

### 4. 아직 반영되지 않았거나 차이가 있는 항목
| 노트북 요소 | 현재 상태 | 향후 고려 사항 |
| --- | --- | --- |
| Summarizer 노드 | 성능·지연 문제로 비활성화 (노트북과 동일하게 주석 처리) | 필요 시 `small_llm` 기반 비동기 요약 노드 재활성화 검토 |
| 주피터 CLI 루프 | FastAPI HTTP 엔드포인트로 대체 | 로컬 디버깅용 CLI가 필요하면 `scripts/` 하위에 별도 도구 추가 가능 |
| AsyncConnectionPool 직접 제어 | 현재는 `AsyncPostgresSaver.from_conn_string`의 내부 풀 사용 | 연결 재사용이 충분하면 유지, 고급 튜닝 필요 시 커스텀 풀 주입 |
| LangSmith 프로젝트명/키 파일화 | 도커/EC2 환경에선 `.env` 및 SSM 스크립트 사용 | 노트북식 로컬 파일(`../key/.openai_api_key`) 방식은 도입하지 않음 |
| JSON→Markdown 변환/문서화 루틴 | PGVector 적재용 스크립트에서 핵심 로직만 사용 | 문서 출력이 필요하면 추가 CLI 작성 |
| `DuckDuckGoSearchRun` 직접 사용 | `WebSearchClient` 클래스로 추상화하여 설정 기반 토글 | 향후 Tavily/Serper 등 추가 시 추상화 계층만 확장 |
| ToolNode 단일 등록 | 그래프에선 동일하지만 HTTP 계층에서 `restart_thread`, `stream_mode` 등 확장 필드 추가 | LangGraph 자체 구조에는 영향 없음 |

---

### 5. 요약
- **핵심 대화 흐름, RAG, 도구 사용, 비동기 체크포인트, 스트리밍**은 노트북과 동일한 패턴으로 서비스 코드에 이식 완료.
- **운영 환경 차이**(FastAPI 서버, Spring 연동, AWS 배포)에 맞춰 **환경 변수 관리, thread_id 전략, 웹 검색 토글, README 가이드** 등을 확장함.
- **미도입 요소**(CLI 루프, summarizer, 파일 기반 키 관리 등)는 의도적으로 생략했으며, 필요 시 이 문서를 참고해 선택적으로 재도입할 수 있음.

---

### 6. 참고 링크
- 노트북 원본: `/home/ubuntu/b4c_async_multi_psql_nosum.ipynb`
- FastAPI 앱 진입점: `/home/ubuntu/chatbot/app/main.py`
- 그래프 구현: `/home/ubuntu/chatbot/app/graphs/consumer_graph.py`, `/home/ubuntu/chatbot/app/graphs/seller_graph.py`

