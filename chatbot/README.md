## Itdaing LangGraph Chatbot (FastAPI)

### 개요

- 이 서비스는 **Spring Boot 백엔드에서 호출하는 LangGraph 기반 챗봇**입니다.
- 주요 역할:
  - 소비자용 플리마켓 추천 챗봇 (`bot4c_v2_multiturn.py` 로직 이식)
  - 판매자용 존 추천 챗봇 (`bot4s.py` 로직 이식)
  - **PostgreSQL + AsyncPostgresSaver** 로 대화 상태(checkpoint) 영구 저장
  - **PGVector** 기반 RAG (마켓 / 존 정보)

### 잇다잉(Itdaing) 페르소나 요약

- **서비스명/역할**: 광주광역시 플리마켓·팝업스토어 추천 전문가 “잇다잉(Itdaing)”으로 소개한다.
- **목표**: 사용자(소비자/판매자)에게 가장 적절한 마켓 혹은 존 정보를 추천하거나 필요한 배경 정보를 제공한다.
- **도구 사용 우선순위**: 구체적 추천·스케줄·위치 정보는 반드시 `retrieve` 계열 도구(PGVector)로 먼저 찾고, 데이터가 없을 때만 `web_search` 로 보완한다.
- **판단 기준**: 모호한 질문도 플리마켓 추천 의도로 해석하며, 완전히 무관한 질문(코딩, 수학 등)은 정중하게 거절한다.
- **답변 스타일**: 한국어로 친근·공손한 톤을 유지하고, 가벼운 유머나 위트를 섞어 대화를 이어간다.

### 폴더 구조 (요약)

- `app/`
  - `config.py` – `.env(chatbot.env)` 로딩, OpenAI/PG/PGVector/LangSmith 설정
  - `db/postgres.py` – PGVector 헬퍼, (레거시) LangGraph 체크포인터 헬퍼
  - `tools/` – LangGraph ToolNode에서 사용하는 retrieval/web_search 도구 모듈
  - `graphs/consumer/` – 소비자용 LangGraph 노드 + 빌더 (bot4c_v2_multiturn 이식)
  - `graphs/seller/` – 판매자용 LangGraph 노드 + 빌더 (bot4s 이식)
  - `graphs/shared/` – 메시지 포맷터, 웹 검색 fallback 등 공통 유틸
  - `routers/chat_consumer.py` – `/api/chat/consumer`, `/api/chat/consumer/stream`
  - `routers/chat_seller.py` – `/api/chat/seller`, `/api/chat/seller/stream`
  - `data/markets_loader.py` – `markets_seed.json` → PGVector(`itdaing_popups`)
  - `data/zones_loader.py` – `zones_seed.json` → PGVector(`itdaing_zone`)
  - `main.py` – FastAPI 생성, LangGraph + AsyncPostgresSaver 초기화, 라우터 등록
- `chatbot.env` – 로컬/EC2 환경 변수 (AWS SSM/Secrets 에서 생성 가능)
- `scripts/generate-chatbot-env.sh` – EC2 부팅 시 `chatbot.env` 자동 생성
- `artifacts/graphs/*.mmd|.png` – LangGraph 구조 다이어그램 (consumer/seller 그래프)

### 환경 변수 (핵심)

- OpenAI / LangChain / LangSmith:
  - `OPENAI_API_KEY`, `OPENAI_MODEL`, `OPENAI_EMBEDDING_MODEL`
  - `LANGSMITH_API_KEY`, `LANGSMITH_PROJECT`, `LANGSMITH_TRACING`
  - `LANGCHAIN_API_KEY`, `LANGCHAIN_TRACING_V2`, `LANGCHAIN_ENDPOINT`
- PG / PGVector:
  - `PGVECTOR_CONNECTION` – `postgresql+psycopg://.../itdaing-db`
  - `VECTOR_COLLECTION=itdaing_popups`
  - `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB`, `POSTGRES_HOST`, `POSTGRES_PORT`
  - (옵션) `PGVECTOR_ZONE_URL`, `PGVECTOR_ZONE_COLLECTION=itdaing_zone`
- RAG / 메모리:
  - `RAG_TOP_K`, `ZONE_RAG_TOP_K`
  - `MAX_MESSAGE_HISTORY`, `ZONE_MAX_MESSAGE_HISTORY`
- 웹 검색 / 외부 정보:
  - `WEBSEARCH_ENABLED` (DuckDuckGo fallback on/off)
  - `WEBSEARCH_PROVIDER` (현재 `duckduckgo` 고정), `WEBSEARCH_TOP_K`
  - `TAVILY_API_KEY` 등 추후 확장용 키
- LangGraph checkpoint:
  - `CHECKPOINT_DB_URL` (없으면 POSTGRES_* 기반 DSN 사용)
  - `LANGGRAPH_AES_KEY` (16/24/32바이트 문자열 – 이미 샘플 값 세팅됨)
- 시드 데이터 경로:
  - `MARKETS_SEED_PATH=/home/ubuntu/markets_seed.json`
  - `ZONES_SEED_PATH=/home/ubuntu/zones_seed.json`

### LangGraph / thread_id 전략

- LangGraph는 `AsyncPostgresSaver`를 사용해 **대화 상태를 Postgres에 저장**합니다.
- 모든 호출에서 `config = {"configurable": {"thread_id": "<thread_id>"}}` 를 넘기며:
  - 소비자: `consumer:{user_id}:{session_id or 'default'}`
  - 판매자: `seller:{user_id}:{session_id or 'default'}`
- Spring 쪽에서는:
  - `user_id` → 회원 ID (또는 비회원 세션 키)
  - `session_id` → 프론트의 대화 세션/탭 ID
  - 같은 `(user_id, session_id)` 조합으로 요청하면 LangGraph가 **같은 대화 히스토리**를 이어 받습니다.
- 오류/타임아웃 후 재시작:
  - Request body에 `restart_thread=true`를 추가하면 서버가 `consumer|seller:{user}:{session}:{uuid}` 형태의 **새 thread_id**를 발급
  - Response에는 항상 `thread_id`가 포함되므로, 정상 케이스에서는 그대로 재사용하면 됩니다.

### LangGraph Tool 파이프라인 (consumer ↔ seller 공통)

- **Tool 정의**  
  - `app/tools/retrieval.py` → `consumer_retrieve`, `seller_retrieve` (sync/async 버전 포함)  
  - `app/tools/web_search.py` → `web_search` (DuckDuckGo)  
  - 모든 Tool은 JSON 문자열로 `documents`/`metadata`/`count` 를 반환하므로 LangSmith trace에서 근거를 그대로 확인할 수 있습니다.
- **그래프 내 흐름**  
  1. `case_classification` 이후 `schedule_tool` 노드가 LangGraph MessagesState에 `AIMessage(tool_calls=...)` 를 추가합니다.  
  2. `ToolNode`가 실제 Tool을 실행하고, `ToolMessage`를 messages에 추가합니다.  
  3. `consume_tool` 노드가 Tool 결과(JSON)를 파싱해 `state.context`를 `Document` 리스트로 복원합니다.  
  4. 결과 문서가 없고 `WEBSEARCH_ENABLED=true`이면 `web_search` Tool을 자동으로 재요청하여 DuckDuckGo 결과를 보강합니다.  
  5. `generate` 노드는 항상 Tool이 전달한 `context`만을 이용해 RAG 응답을 작성하므로, consumer/seller 그래프 모두 **동일한 Tool 파이프라인** 위에서 동작합니다.
- **비동기/스트림**  
  - Async 그래프에서는 `consumer_retrieve_async`, `web_search_async` (동일 시그니처) 를 사용하여 `graph.astream(...)` 상황에서도 Tool 결과가 자연스럽게 diff에 포함됩니다.
  - LangSmith에서 trace를 보면 `tool_call → tool_output → rag_generate` 순서가 consumer/seller 모두 동일하게 찍히며, 스트림 엔드포인트에서도 delta 로그를 통해 Tool 실행 시점이 그대로 노출됩니다.

### LangGraph 시각화 (artifacts/graphs)

- `scripts/render_graphs.py` 실행 시 아래 파일이 최신 그래프 정의를 기준으로 다시 생성됩니다.
  - `artifacts/graphs/consumer_graph.mmd` / `consumer_graph.png`
  - `artifacts/graphs/seller_graph.mmd` / `seller_graph.png`
- Mermaid 소스(`.mmd`)는 PR 리뷰에서 구조 diff를 텍스트로 추적할 수 있고, PNG는 QA/기획이 바로 열어볼 수 있는 정적인 다이어그램입니다.
- 두 그래프 모두 `__start__ → extract_query → case_classification → schedule_tool → ToolNode` 플로우를 공유하므로, 문서나 회의에서 빠르게 비교할 때 해당 이미지를 첨부하면 됩니다.

### RAG 시드 로딩 (1회 작업)

```bash
cd /home/ubuntu/chatbot
. .venv/bin/activate

python -m app.data.markets_loader --reset   # itdaing_popups 컬렉션 재구축
python -m app.data.zones_loader --reset     # itdaing_zone 컬렉션 재구축
```

### 서버 기동 (단독 실행)

```bash
cd /home/ubuntu/chatbot
. .venv/bin/activate

uvicorn app.main:app --host 0.0.0.0 --port 9000
```

헬스체크:

```bash
curl -s http://127.0.0.1:9000/health
```

### API 명세 (HTTP + Streaming)

> Nginx가 `/ai/` prefix를 FastAPI (`10.0.146.32:9000/`) 로 프록시하므로, 클라이언트는 `https://<nginx-host>/ai/...` 경로만 사용하면 됩니다.

#### 공통 규칙

- **Content-Type**: 모든 요청/응답은 `application/json`.
- **인증**: 현재 사내 VPC IP 화이트리스트로 보호, 추가 토큰은 추후 확장.
- **타임아웃 가이드**: 백엔드는 60초 이상 잡아도 되지만, 프론트는 30초 내 재시도 UX를 권장.
- **스레드 관리**: `thread_id`는 응답에 항상 포함. 다음 호출에서 그대로 전달하면 LangGraph가 Postgres 체크포인트 기반으로 대화를 이어준다.
- **공통 헤더**: `X-Trace-Id` (선택). 미전달 시 FastAPI에서 UUID를 생성하여 로그/trace에 기록.

#### 요청 본문 공통 스키마

| 필드 | 타입 | 필수 | 설명 |
|------|------|------|------|
| `user_id` | string | ✅ | Spring 사용자의 고유 ID. 비회원일 경우 프론트 세션 키 사용. |
| `session_id` | string | ✅ | 프론트 탭/대화 ID. null 이면 서버가 `"default"` 로 처리. |
| `message` | string | ✅ | 사용자 입력 문장. 빈 문자열은 400 처리. |
| `thread_id` | string \| null | ⛔️ | (선택) 이전 응답에서 받은 thread_id. null 이면 `"{bot}:{user_id}:{session_id}"` 생성. |
| `restart_thread` | boolean | ✅ | `true`면 기존 thread snapshot을 폐기하고 신규 UUID suffix를 붙인다. 주로 오류 복구용. |

> `consumer` 엔드포인트는 `thread_id` prefix가 항상 `consumer:`. `seller` 엔드포인트는 `seller:` 로 강제된다.

#### 기본 응답 스키마

| 필드 | 타입 | 설명 |
|------|------|------|
| `answer` | string | 동기·Async 단발 엔드포인트에서 최종 LLM 응답. |
| `thread_id` | string | LangGraph에서 실제 사용된 thread 식별자. 다음 요청에 그대로 사용. |

Streaming 엔드포인트에서는 위 스키마 대신 **줄 단위(JSON Lines)** 로 `{ "delta": "...", "thread_id": "..." }` 형식을 지속 전송한다.

#### 엔드포인트 요약

| 메서드 | 경로 | 설명 | 완료 시점 |
|--------|------|------|-----------|
| POST | `/ai/api/chat/consumer` | 소비자 챗봇 동기 응답 | LangGraph 완료 후 한 번에 결과 |
| POST | `/ai/api/chat/consumer/stream` | 소비자 챗봇 동기 스트림 | 한 줄 JSON으로 결과 스트림 |
| POST | `/ai/api/chat/consumer/async` | 소비자 챗봇 비동기(내부 async) 완료 응답 | LangGraph async 실행 종료 후 단발 |
| POST | `/ai/api/chat/consumer/async/stream` | 소비자 챗봇 비동기 diff 스트림 | LangGraph `astream` diff 전송 |
| POST | `/ai/api/chat/seller` | 판매자 챗봇 동기 응답 | 위와 동일 |
| POST | `/ai/api/chat/seller/stream` | 판매자 챗봇 동기 스트림 | 위와 동일 |
| POST | `/ai/api/chat/seller/async` | 판매자 챗봇 비동기 완료 응답 | 위와 동일 |
| POST | `/ai/api/chat/seller/async/stream` | 판매자 챗봇 비동기 diff 스트림 | 위와 동일 |

#### 1) 소비자 동기 완료 `POST /ai/api/chat/consumer`

- **설명**: LangGraph를 동기적으로 실행하고 `answer` 하나만 반환.
- **성공 상태코드**: `200 OK`.
- **Request 예시**

```json
{
  "user_id": "springUserId",
  "session_id": "conv-202411",
  "message": "광주 야경 예쁜 플리마켓 추천해줘",
  "thread_id": null,
  "restart_thread": false
}
```

- **Response 예시**

```json
{
  "answer": "야경이 아름다운 플리마켓을 ...",
  "thread_id": "consumer:springUserId:conv-202411"
}
```

- **구현 노트**
  - Spring은 응답의 `thread_id`를 저장 후, 다음 요청의 `thread_id` 필드에 그대로 실어야 멀티턴이 유지된다.
  - 판매자용 API는 동일 JSON 구조에서 prefix만 `seller:` 로 바뀐다.

#### 2) 소비자 동기 스트림 `POST /ai/api/chat/consumer/stream`

- **설명**: 내부 처리는 동기지만, chunked 전송으로 한 번만 JSON 라인을 내려준다. 긴 응답에도 연결을 유지하기 위함.
- **성공 상태코드**: `200 OK`, `Transfer-Encoding: chunked`.
- **Response 포맷**

```
{"delta":"야경 플리 추천을 정리 중입니다...","thread_id":"consumer:..."}
```

> `delta`는 최종 답변 전체 문자열. (동기 완료형과 동일)

#### 3) 소비자 Async 완료 `POST /ai/api/chat/consumer/async`

- **설명**: LangGraph 노드들을 `async` 실행으로 구성한 버전. 인터페이스는 동기 완료와 동일해 Spring 쪽 코드 재사용이 가능하다.
- **성공 상태코드**: `200 OK`.
- **입출력 스키마**: 1)과 동일.
- **사용 시점**: 추후 모든 그래프가 async로 전환되었을 때 기본 계약으로 사용할 예정.

#### 4) 소비자 Async diff 스트림 `POST /ai/api/chat/consumer/async/stream`

- **설명**: LangGraph `Graph.astream_events` 결과를 그대로 흘려 보내 diff 단위로 실시간 렌더링.
- **성공 상태코드**: `200 OK`, `Transfer-Encoding: chunked`.
- **Response 흐름**
  1. `delta` 첫 줄은 사용자 원문 에코.
  2. 이후 `consumer_retrieve_async` 등 Tool 스케줄 이벤트 메시지가 순서대로 등장.
  3. 마지막에 LLM이 작성한 답변이 여러 개의 delta로 쪼개져 도착.
- **프론트 처리 가이드**
  - `thread_id`가 동일한 delta를 순서대로 이어 붙여 최종 답을 구성.
  - 스트림 종료는 서버가 `0\r\n\r\n` (chunked 종료) 를 보낼 때.
  - 연결 중 에러는 HTTP 상태코드로 즉시 응답 (예: 401, 429).

#### 판매자 엔드포인트

- 위 4가지 패턴과 완전히 동일하며, 경로의 `consumer`만 `seller`로 치환하면 된다.
- 판매자 그래프는 도구(`seller_retrieve_async`)와 프롬프트만 다르므로, Spring/프론트에서 공통 SDK를 작성해도 무방하다.

#### 오류 응답 규칙

| 상태코드 | 예시 상황 | 응답 페이로드 |
|----------|-----------|----------------|
| 400 | 필수 필드 누락, message 길이 0 | `{"error":"BAD_REQUEST","detail":"message is required","code":"REQ_001"}` |
| 401 | 추후 토큰 미검증 | `{"error":"UNAUTHORIZED","detail":"invalid token","code":"AUTH_001"}` |
| 404 | 경로 오타 | FastAPI 기본 404 또는 Nginx 404 |
| 422 | 스키마 검증 실패(FastAPI pydantic) | 자동 생성. 프론트에서는 `detail[0].msg` 참고. |
| 429 | OpenAI RPM/TPM 제한, 내부 큐 초과 | `{"error":"RATE_LIMIT","detail":"OpenAI quota exceeded","code":"OPENAI_429"}` |
| 500 | LangGraph 예외, Tool 실패 미처리 | `{"error":"INTERNAL_ERROR","detail":"...", "code":"SRV_001"}` |

- FastAPI 예외 핸들러는 예제와 동일한 포맷으로 응답을 맞췄으며, 추가 코드(`code`) 는 클라이언트 로깅/알림에 사용.
- Retry 가능한 오류(429, 500)는 Spring 단에서 exponential backoff(예: 1s, 2s, 4s)를 권장.

#### 로깅 & 추적

- 모든 API는 `thread_id`, `user_id`, `session_id`, `X-Trace-Id` 를 FastAPI 로거에 남긴다.
- LangSmith 프로젝트(`chatbot-aws`)에서 동일한 `thread_id`를 키로 실행 trace를 조회할 수 있다.
- 필요 시 Spring에서 `X-Trace-Id`를 생성해 전달하면, FastAPI → LangGraph → LangSmith 로그를 동일 키로 그룹화 가능하다.

### 터미널에서 Async Streaming 테스트하기

백엔드(Spring)나 프론트엔드가 붙기 전이라도, FastAPI 서버만 띄워두면 `curl` 로 LangGraph 비동기·스트림 경로를 검증할 수 있습니다.

1. **FastAPI 서버 실행**

   ```bash
   cd /home/ubuntu/chatbot
   . .venv/bin/activate
   uvicorn app.main:app --host 0.0.0.0 --port 9000
   ```

2. **소비자 Async 스트림 호출**

   다른 터미널에서 아래 명령을 실행하면 JSON 라인이 실시간으로 흘러옵니다. `-N` 옵션으로 스트림을 끊지 않고 유지합니다.

   ```bash
   curl -N -H "Content-Type: application/json" \
     -X POST http://127.0.0.1:9000/api/chat/consumer/async/stream \
     -d '{
       "user_id": "demoUser",
       "session_id": "test-session",
       "message": "광주 야경 보기 좋은 플리마켓 추천해줘",
       "restart_thread": false
     }'
   ```

   출력 예시:

   ```
   {"delta":"안녕하세요! ...","thread_id":"consumer:demoUser:test-session"}
   {"delta":" (추가 답변)","thread_id":"consumer:demoUser:test-session"}
   ```

3. **판매자 Async 스트림 호출**

   동일한 형식으로 `/api/chat/seller/async/stream` 를 호출하면 존 추천 그래프 결과를 확인할 수 있습니다.

   ```bash
   curl -N -H "Content-Type: application/json" \
     -X POST http://127.0.0.1:9000/api/chat/seller/async/stream \
     -d '{
       "user_id": "demoSeller",
       "session_id": "zone-session",
       "message": "광주 북구에서 주말에 열기 좋은 존 추천해줘",
       "restart_thread": false
     }'
   ```

4. **비동기 단발 응답(`/async`) 확인**

   스트리밍이 아닌 단발 응답을 테스트하려면 `/async` 엔드포인트에 동일한 JSON을 전달하면 됩니다.

   ```bash
   curl -H "Content-Type: application/json" \
     -X POST http://127.0.0.1:9000/api/chat/consumer/async \
     -d '{
       "user_id": "demoUser",
       "session_id": "test-session",
       "message": "광주 야경 보기 좋은 플리마켓 추천해줘",
       "restart_thread": false
     }'
   ```

이 과정을 통해 Spring 없이도 LangGraph async 그래프와 diff 스트림을 독립적으로 점검할 수 있습니다.

### 멀티턴(thread_id) 유지 전략

- LangGraph는 Postgres 체크포인터를 통해 `configurable.thread_id` 단위로 상태를 복구합니다.
- API 응답에는 항상 `thread_id`가 포함되므로, 클라이언트(Spring)가 이 값을 저장/재사용하면 멀티턴 대화가 그대로 이어집니다.
- 기본 규칙:
  - 소비자: `consumer:{user_id}:{session_id or default}`
  - 판매자: `seller:{user_id}:{session_id or default}`
- `restart_thread=true` 를 주면 내부적으로 UUID suffix를 붙여 새 thread를 시작합니다 (실패한 대화를 리셋할 때 사용).
- Streaming/Async 엔드포인트도 동일한 thread_id를 요구하므로, 프론트/백엔드가 thread_id를 일관되게 넘기면 Sync ↔ Async 간 전환 시에도 대화가 끊기지 않습니다.

### EC2 / Nginx 연동 요약

- Nginx (`ops/nginx/chatbot.conf`) 에서:

```nginx
location /ai/ {
    allow 10.0.0.0/16;
    deny all;
    proxy_pass         http://....:9000/;
    proxy_set_header   Host               $host;
    proxy_set_header   X-Real-IP          $remote_addr;
    proxy_set_header   X-Forwarded-For    $proxy_add_x_forwarded_for;
    proxy_set_header   X-Forwarded-Proto  $scheme;
    proxy_http_version 1.1;
    proxy_set_header   Connection "";
}
```

- 따라서 **프론트/QA**는 `http(s)://<nginx-host>/ai/api/chat/...` 으로 호출하면 됩니다.

### LangSmith / 추적

- `chatbot.env`에 LangSmith / LangChain 관련 ENV가 모두 들어 있으며,
  `app.main`에서 `.env`를 로딩하므로:
  - OpenAI / LangGraph 호출은 자동으로 `LANGSMITH_PROJECT=chatbot-aws` 로 트레이싱됩니다.
- LangSmith 대시보드에서 프로젝트 이름으로 검색하면,  
  각 `/api/chat/...` 호출에 대응되는 LangGraph 실행 trace를 확인할 수 있습니다.

---

### 성능 최적화 현황 (2025-12-02 v9)

| 지표 | v1 Baseline | v9 현재 | 개선율 |
|------|-------------|---------|--------|
| **싱글턴 Latency** | 17.56초 | **3.92초** | **-78%** |
| **멀티턴 Latency** | N/A | **4.70초** | - |
| **P99 (싱글턴)** | N/A | **3.92초** | - |
| **Error Rate** | 0% | **0%** | - |
| **Quality Score** | N/A | **4.29/5.0** | - |

#### 주요 최적화 내역

1. **LLM 호출 최적화**: 6회 → 2회 (노드 병합)
2. **RAG 컨텍스트 축소**: 3000자 → 2000자
3. **응답 토큰 제한**: 256 토큰
4. **휴리스틱 빠른 경로**: 인사/범위외 질문 즉시 응답

자세한 내용은 `langsmith-test/experiments/CHANGELOG.md` 참조.

---

### 테스트 실행

```bash
cd /home/ubuntu/chatbot
. .venv/bin/activate

# 싱글턴 테스트 (203개 케이스)
python langsmith-test/run_experiment.py \
  --dataset consumer-single-1202-v1 \
  --experiment single-test \
  --repeats 1

# 멀티턴 테스트 (36개 케이스)
python langsmith-test/run_experiment.py \
  --dataset consumer-multi-1202-v1 \
  --experiment multi-test \
  --repeats 1 \
  --multiturn

# P99 분석
python langsmith-test/analyze_p99.py --experiment single-test
```

