# 챗봇 답변 누적 문제 최종 해결

**작성일**: 2025-11-29  
**버전**: v1.3 (critical state management fix)

---

## 🚨 문제 재현

### Console 로그
```
질문 1: "안녕"
→ Bot: "안녕하세요! 오늘도 좋은 하루 되세요!"

질문 2: "서구에 갈만한 곳"
→ Bot: "안녕하세요! 오늘도... 서구에서 추천할 만한 플리마켓..."
       ^^^^^^^^^^^^^^^^^^^^^^^^^ 이전 답변 누적!
```

### UI 증상
- 질문할 때마다 **이전 모든 답변이 앞에 붙음**
- 3번째 질문 시 1+2+3번 답변 모두 출력

---

## 🎯 근본 원인: LangGraph State 누적

### 백엔드 State 관리
**파일**: [`chatbot/app/graphs/consumer/nodes.py:854-862`](chatbot/app/graphs/consumer/nodes.py)

**기존 코드**:
```python
def format_answer_message(state: AgentState) -> AgentState:
    answer = state.get("answer", "")
    return {
        **state,  # 이전 state 모두 유지
        "messages": [AIMessage(content=answer_text)],
    }
```

**문제**:
- `**state`: 이전 턴의 `answer`, `context`, `structured_plan` 등 모두 유지
- Thread ID 재사용 시 state가 checkpoint에서 복원됨
- 새 답변 생성 시 이전 `answer` 필드가 남아있음
- LLM이 summary + 이전 answer를 기반으로 새 답변 생성 가능성

### LangGraph Checkpoint 동작
```
질문 1: "안녕"
  → State: {answer: "안녕하세요!", messages: [...]}
  → Checkpoint 저장

질문 2: "서구"  
  → State 복원: {answer: "안녕하세요!", ...}  ← 이전 answer 남아있음!
  → 새 answer 생성: "서구에서..."
  → **summary에 이전 대화 포함 가능**
```

---

## ✅ 적용된 수정

### 1. State 일회성 필드 명시적 제거
**파일**: [`chatbot/app/graphs/consumer/nodes.py`](chatbot/app/graphs/consumer/nodes.py)

```python
def format_answer_message(state: AgentState) -> AgentState:
    answer = state.get("answer", "")
    if not answer:
        return state
    answer_text = answer if isinstance(answer, str) else str(answer)
    
    # answer를 messages로 변환 후 state에서 제거
    next_state: AgentState = {
        **state,
        "messages": [AIMessage(content=answer_text)],
    }
    
    # 일회성 필드들을 명시적으로 제거 (다음 턴 누적 방지)
    next_state.pop("answer", None)
    next_state.pop("context", None)
    next_state.pop("hallucination_label", None)
    next_state.pop("hallucination_reason", None)
    next_state.pop("paraphrased_query", None)
    next_state.pop("case", None)
    next_state.pop("structured_plan", None)
    next_state.pop("structured_plan_result", None)
    next_state.pop("entity_target", None)
    next_state.pop("last_tool_payload", None)
    # recommendations는 유지 (프론트엔드 전달용)
    
    return next_state
```

**효과**:
- 다음 턴에서 이전 `answer`, `context` 등이 state에 남지 않음
- Checkpoint 복원 시에도 깔끔한 state

---

## 📊 State Lifecycle

### Before (문제)
```
Turn 1: {answer: "안녕하세요!", context: [...], ...} → Checkpoint 저장
Turn 2: State 복원 → {answer: "안녕하세요!", ...}  ← 이전 answer 남음!
        새 답변 생성 → answer: "서구... (summary 포함)"
```

### After (수정)
```
Turn 1: {answer: "안녕하세요!", ...}
        format_answer → {messages: [...]} + answer 제거
        → Checkpoint 저장

Turn 2: State 복원 → {messages: [...]}  ← answer 없음! ✅
        새 답변 생성 → answer: "서구..."
```

---

## 🧪 검증 방법

### 1. FastAPI 재시작 (필수!)
```bash
cd /home/ubuntu/chatbot
pkill -f "uvicorn app.main:app"
uvicorn app.main:app --host 0.0.0.0 --port 9000 --reload &
```

### 2. 브라우저 테스트
```
질문 1: "안녕"
기대: "안녕하세요! 오늘도..."

질문 2: "서구에 갈만한 곳"
기대: "광주 서구의 XX 마켓... 아래 카드에서 확인하세요."  
      (이전 답변 없음!)

Console:
[useChatSession] First delta: 안녕하세요!...  (1번만)
[useChatSession] Recommendations received: [{...}]
```

### 3. Console 확인
- ❌ "First delta"가 여러 번 나오면 여전히 문제
- ✅ "First delta"가 1번만 나오고, 이전 답변 미포함

---

## 🔧 문제 분류

| 증상 | 원인 | 위치 | 수정 |
|------|------|------|------|
| 질문 반복 | LLM이 질문 echo | 백엔드 프롬프트 | ✅ System 프롬프트 명시 |
| 답변 누적 | State answer 필드 누적 | 백엔드 State 관리 | ✅ format_answer_message에서 제거 |
| 스트리밍 중복 | Diff 계산 오류 | 백엔드 + 프론트엔드 | ✅ hasFirstDelta 플래그 개선 |
| 상세 링크 안 됨 | M001 vs 숫자 ID | 프론트엔드 | ✅ M001→1 변환 |

---

## 📝 수정 파일 최종 목록

### 백엔드 (chatbot)
```
app/graphs/consumer/nodes.py
  - format_answer_message: State 일회성 필드 제거
  - Router/Feasibility: 광주광역시 전용 명시
  - Structured Plan: 동구 제외 vs 포함 구분
  - Case Classification: 제외 의도 명시

app/chains/shared/prompts.py
  - CONSUMER_AGENT_SYSTEM_PROMPT: 질문 반복 금지
  - CONSUMER_RAG_PROMPT: 3-4문장 + 질문 반복 금지

app/graphs/shared/structured_query.py
  - StructuredRetrievalPlan: exclude_districts 필드
  - apply_structured_plan: 지역 제외 필터 적용

app/routers/chat_consumer.py
  - event_stream: Diff 계산 + 중복 방지
```

### 프론트엔드 (itdaing-app)
```
src/chatbot/hooks/useChatSession.js
  - hasFirstDelta 플래그 관리 개선
  - Echo 제거 로직 강화

src/chatbot/components/RecommendationPanel.jsx
  - M001→1 ID 변환
  - metadata fallback
  - 디버깅 로그
```

---

## ⚠️ 재시작 필수!

**FastAPI 재시작 없이는 수정 사항이 반영되지 않습니다!**

```bash
cd /home/ubuntu/chatbot
pkill -f "uvicorn"
uvicorn app.main:app --host 0.0.0.0 --port 9000 --reload &
```

---

**상태**: ✅ 코드 수정 완료  
**필수 조치**: FastAPI 재시작 후 재테스트

