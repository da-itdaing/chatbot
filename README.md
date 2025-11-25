# Itdaing LangGraph Chatbot 사용 가이드

이 문서는 `/home/ubuntu/chatbot` 프로젝트를 EC2에서 배포/운영할 때 필요한 **설치·환경 구성·실행·API 사용법**을 정리합니다. Spring Boot 백엔드가 FastAPI 챗봇을 호출하는 시나리오를 전제로 작성되었습니다.

---

## 1. 환경 준비

### 1.1 필수 요구 사항
- Python 3.11+
- PostgreSQL + pgvector 확장 (RDS `itdaing-db`)
- OpenAI API Key (LangChain/LangGraph)
- AWS CLI (SSM & Secrets Manager 접근)

### 1.2 저장소 & 가상환경
```bash
git clone https://github.com/da-itdaing/chatbot.git
cd chatbot
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
```

### 1.3 환경 변수 생성
1. AWS 권한이 있는 EC2에서 아래 스크립트를 실행하면 `/home/ubuntu/chatbot/chatbot.env`가 자동 생성됩니다.
   ```bash
   cd /home/ubuntu
   ./scripts/generate-chatbot-env.sh
   ```
2. 핵심 변수
   - `OPENAI_*`, `TAVILY_API_KEY`
   - `PGVECTOR_CONNECTION`, `POSTGRES_*`
   - `LANGSMITH_*`, `LANGCHAIN_*`
   - `WEBSEARCH_ENABLED`, `WEBSEARCH_PROVIDER`, `WEBSEARCH_TOP_K`
   - `LANGGRAPH_AES_KEY` (32바이트)

> 로컬 개발 시에는 `chatbot/chatbot.env`를 직접 수정하고 `uvicorn` 실행 전에 `load_dotenv`가 불리도록 되어 있습니다.

---

## 2. 데이터 시드 & LangGraph 체크포인트

| 작업 | 명령 |
| --- | --- |
| 소비자 RAG 컬렉션 초기화 | `python -m app.data.markets_loader --reset` |
| 판매자 존 RAG 컬렉션 초기화 | `python -m app.data.zones_loader --reset` |

LangGraph 상태는 `AsyncPostgresSaver`가 RDS에 만드는 `checkpoint_*` 테이블에 저장됩니다. **백업/복구 시 이 테이블들을 포함**해야 멀티턴 대화가 유지됩니다.

---

## 3. 서버 실행

```bash
cd /home/ubuntu/chatbot
. .venv/bin/activate
uvicorn app.main:app --host 0.0.0.0 --port 9000
```

- 헬스체크: `curl -s http://127.0.0.1:9000/health`
- 운영 시 Nginx → `http://10.0.146.32:9000/` 로 프록시 (예: `/ai/api/chat/...`)

---

## 4. API 사용 방법

### 4.1 요청 모델 (공통)
```json
{
  "user_id": "springUserId",
  "session_id": "conversationId-or-null",
  "message": "사용자 질문",
  "thread_id": null,
  "restart_thread": false
}
```
- `thread_id`를 비우면 서버가 `consumer|seller:{user_id}:{session_id}` 형태로 생성
- 오류/타임아웃 후 깨끗한 상태로 다시 시작하려면 **`restart_thread = true`**로 재전송

### 4.2 엔드포인트 요약

| 구분 | 동기 응답 | 단일 청크 스트림 | Async 그래프 | Async diff 스트림 |
| --- | --- | --- | --- | --- |
| 소비자 | `POST /api/chat/consumer` | `POST /api/chat/consumer/stream` | `POST /api/chat/consumer/async` | `POST /api/chat/consumer/async/stream` |
| 판매자 | `POST /api/chat/seller` | `POST /api/chat/seller/stream` | `POST /api/chat/seller/async` | `POST /api/chat/seller/async/stream` |

- `/stream`: LangGraph 실행 완료 후 한 번의 JSON 라인으로 답변 전달
- `/async/stream`: LangGraph `astream(stream_mode="values")`을 사용해 **동일 메시지의 diff**를 지속적으로 스트리밍 (토큰 단위 SSE 확장 예정)

### 4.3 Response 예시
```json
{
  "answer": "광주 동구에서는 ...",
  "thread_id": "consumer:springUserId:conversation:uuid"
}
```

---

## 5. 주요 기능 요약

- **LangGraph 멀티턴**: `AsyncPostgresSaver` + `EncryptedSerializer`로 대화 상태를 Postgres에 영구 저장
- **웹 검색 fallback**: 벡터스토어 결과가 비어 있을 때 DuckDuckGo 결과를 `Document` 형태로 추가 (`WEBSEARCH_*` ENV로 제어)
- **스트리밍**: 단일 청크/async diff 두 가지 모드 지원 → Spring SSE, WebFlux 등에서 소비 가능
- **오류 복구**: `restart_thread` 플래그로 thread_id를 안전하게 재생성, 이전 손상된 히스토리와 분리

---

## 6. 운영 체크리스트

1. `chatbot.env` 최신 여부 확인 (Secrets Manager 필드 누락 없는지)
   - 빈 값 검출: `cd /home/ubuntu/chatbot && grep -nE '=[[:space:]]*$' chatbot.env`
   - 출력이 `PGVECTOR_ZONE_URL=`만 뜬다면 정상이며, 그 외 결과는 즉시 채운다.
2. RDS `checkpoint_*` 테이블 모니터링 (디스크 용량, VACUUM 주기)
3. LangSmith 대시보드에서 trace 모니터링 (`LANGSMITH_PROJECT=chatbot-aws`)
4. Nginx `/ai/` 프록시 대상 IP 변경 시 `ops/nginx/chatbot.conf` 업데이트
5. 배포 시
   ```bash
   git pull origin dev/ai
   . .venv/bin/activate
   pip install -r requirements.txt
   sudo systemctl restart chatbot.service  # 서비스 스크립트 사용 시
   ```

---

## 7. 자주 묻는 질문

- **Q. thread_id를 지정하지 않았는데 대화가 이어지지 않아요.**  
  A. `session_id`가 매번 신규 값이면 새 thread로 인식합니다. 동일 세션에서는 동일 `session_id`를 넘기세요.

- **Q. OpenAI 키 없이 테스트할 수 있나요?**  
  A. RAG/웹검색 결과만으로 답변하기 어렵기 때문에, 최소한 `OPENAI_API_KEY`는 필요합니다. 개발 환경에서는 `gpt-4o-mini` 대신 작은 모델로 바꿀 수 있습니다.

- **Q. DuckDuckGo 검색을 끄고 싶어요.**  
  A. `chatbot.env`에서 `WEBSEARCH_ENABLED=false`로 설정하면 벡터스토어 결과만 사용합니다.

