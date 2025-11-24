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
  - `graphs/consumer_graph.py` – 소비자용 LangGraph (bot4c_v2_multiturn 이식)
  - `graphs/seller_graph.py` – 판매자용 LangGraph (bot4s 이식)
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

### Spring ↔ FastAPI HTTP 계약

- **소비자 동기 요청**

  - `POST /ai/api/chat/consumer` (Nginx가 `/ai/` → `10.0.146.32:9000/` 프록시)
  - Request:

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

- **판매자 동기 요청**

  - `POST /ai/api/chat/seller`
  - Request/Response 스키마는 소비자와 동일하며 `thread_id` prefix만 `seller:`로 구분됩니다.

- **소비자 Async / Streaming 전용**

  - `POST /ai/api/chat/consumer/async`
  - `POST /ai/api/chat/consumer/async/stream`
  - Request 스키마 동일 (`restart_thread` 지원), `/async/stream` 은 LangGraph `astream`을 통해 메시지 diff를 지속 전송

- **판매자 Async / Streaming 전용**

  - `POST /ai/api/chat/seller/async`
  - `POST /ai/api/chat/seller/async/stream`
  - 소비자 async 엔드포인트와 동일한 패턴

### 스트리밍 엔드포인트

- 1회 청크 (기존 버전): `POST /ai/api/chat/(consumer|seller)/stream`
- Async diff 스트림: `POST /ai/api/chat/(consumer|seller)/async/stream`
- Request 스키마는 동기 버전과 동일.
- Response는 기본적으로 **JSON 라인** (chunked):

```json
{"delta": "LLM이 생성한 최종 답변", "thread_id": "consumer:..."}
```

- `/async/stream` 은 LangGraph `stream_mode="values"` 이벤트를 그대로 전달해,  
  동일 메시지에 변경이 발생할 때마다 diff를 흘려보냅니다. (토큰 단위 SSE는 이후 `astream_events`로 확장 예정)

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


