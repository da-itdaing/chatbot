## Itdaing LangGraph Chatbot (FastAPI)

### 개요

- 이 서비스는 **Spring Boot 백엔드에서 호출하는 LangGraph 기반 챗봇**입니다.
- 주요 역할:
  - 소비자용 플리마켓 추천 챗봇 (`bot4c_v2_multiturn.py` 로직 이식)
  - 판매자용 존 추천 챗봇 (`bot4s.py` 로직 이식)
  - **PostgreSQL + AsyncPostgresSaver** 로 대화 상태(checkpoint) 영구 저장
  - **PGVector** 기반 RAG (마켓 / 존 정보)

### 폴더 구조 (요약)

- `app/`
  - `config.py` – `.env(chatbot.env)` 로딩, OpenAI/PG/PGVector/LangSmith 설정
  - `db/postgres.py` – PGVector 헬퍼, (레거시) LangGraph 체크포인터 헬퍼
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

### HTTP API 설계 (엔드포인트별 예시)

> Nginx가 `/ai/` prefix를 FastAPI (`10.0.146.32:9000/`) 로 프록시하므로, Spring/프론트는 아래 URI를 그대로 호출하면 됩니다.

#### 1) 동기 완료 응답: `POST /ai/api/chat/(consumer|seller)`

- 사용 시점: 기존 v1 계약. LangGraph 실행이 끝나면 최종 답만 전달.
- Request (소비자 예시):

  ```json
  {
    "user_id": "springUserId",
    "session_id": "conversationId-or-null",
    "message": "광주 야경 예쁜 플리마켓 추천해줘",
    "thread_id": null,
    "restart_thread": false
  }
  ```

- Response:

  ```json
  {
    "answer": "LLM이 생성한 한국어 답변",
    "thread_id": "consumer:springUserId:conversationId-or-default"
  }
  ```

- 판매자 요청도 동일하며 `thread_id` prefix만 `seller:` 로 다릅니다.
- **멀티턴**: 응답의 `thread_id`를 다음 호출의 `thread_id` 필드에 그대로 넣으면 이전 대화가 이어집니다.

#### 2) 동기 스트리밍: `POST /ai/api/chat/(consumer|seller)/stream`

- 사용 시점: LangGraph 실행이 끝난 후 한 번에 결과를 내려주지만, HTTP 응답을 스트림으로 받아 중간에 끊기지 않도록 할 때.
- Response는 JSON 라인 하나 (`{"delta":"...","thread_id":"consumer:..."}`) 이며, 내용 자체는 동기 완료 응답과 동일합니다.

#### 3) Async 단발 응답: `POST /ai/api/chat/(consumer|seller)/async`

- 사용 시점: LangGraph 내부에서 LLM/RAG/WebSearch를 모두 async로 처리하되, 최종 응답만 한 번에 받고 싶을 때.
- Request/Response 스키마는 동기 버전과 완전히 동일하며, thread_id 재사용 규칙도 같습니다.

#### 4) Async diff 스트림: `POST /ai/api/chat/(consumer|seller)/async/stream`

- 사용 시점: LangGraph `astream` 결과를 diff 단위로 받아 Spring → 프론트로 실시간 전달할 때.
- Request 본문은 `/async`와 동일합니다.
- Response: 여러 개의 JSON 라인이 순차적으로 흘러오며, 각 라인은 아래와 같습니다.

  ```json
  {"delta":"안녕하세요! ...","thread_id":"consumer:demoUser:test-session"}
  {"delta":" (추가 문장)","thread_id":"consumer:demoUser:test-session"}
  ```

- diff 누적 방식이므로, 프론트에서는 같은 thread_id로 들어오는 delta를 이어 붙이면 전체 답변을 복원할 수 있습니다.
- 판매자 엔드포인트도 동일하게 동작하므로 URI에서 `consumer`만 `seller`로 바꾸면 됩니다.

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


