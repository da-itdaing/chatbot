# 챗봇 스트리밍 출력 문제 해결

**작성일**: 2025-11-29  
**버전**: v1.2 (critical fix)

---

## 🚨 Console 로그 분석

```javascript
[useChatSession] First delta: 안녕
[useChatSession] Skipping question-only delta
[useChatSession] First delta: 하세요! 오늘도...
[useChatSession] First delta: 안녕하세요! 오늘도...
```

**문제**: 답변이 3번 중복 전송됨
- "안녕" (첫 조각)
- "하세요!..." (두 번째 조각)
- "안녕하세요!..." (전체 답변)

---

## 🔍 근본 원인

### 백엔드 (FastAPI)
**파일**: [`chatbot/app/routers/chat_consumer.py:330-337`](chatbot/app/routers/chat_consumer.py)

**문제**:
```python
if previous and full_text.startswith(previous):
    new_text = full_text[len(previous):]
else:
    new_text = full_text  # 완전히 새 텍스트면 전체 전송
previous = full_text
```

**이슈**: 
- Graph가 "안녕" → "안녕하세요!" → "안녕하세요! 오늘도..."처럼 점진적으로 answer를 업데이트
- 첫 번째: previous="", full_text="안녕" → new_text="안녕" (전체)
- 두 번째: previous="안녕", full_text="안녕하세요" → startswith 실패 → new_text="안녕하세요" (전체 다시 전송!)

### 프론트엔드 (React)
**파일**: [`backend/itdaing-app/src/chatbot/hooks/useChatSession.js:144-164`](backend/itdaing-app/src/chatbot/hooks/useChatSession.js)

**문제**:
```js
if (!hasFirstDelta) {
  // echo 제거 로직
  if (질문만 있으면) {
    return;  // hasFirstDelta = true 설정 안 됨!
  }
}
hasFirstDelta = true;
```

**이슈**: 첫 delta를 스킵하면 플래그가 설정되지 않아, 두 번째 delta도 echo 제거 시도

---

## ✅ 적용된 수정

### 1. 프론트엔드 플래그 관리 개선
**파일**: [`backend/itdaing-app/src/chatbot/hooks/useChatSession.js`](backend/itdaing-app/src/chatbot/hooks/useChatSession.js)

**변경**:
```js
if (!hasFirstDelta) {
  // echo 제거 로직
  if (질문만 있으면) {
    hasFirstDelta = true;  // 플래그 설정 후 스킵
    return;
  }
  hasFirstDelta = true;  // 여기도 설정
}
```

**효과**: 첫 delta를 스킵해도, 두 번째 delta부터는 echo 제거 안 함

### 2. 백엔드 Diff 계산 개선
**파일**: [`chatbot/app/routers/chat_consumer.py`](chatbot/app/routers/chat_consumer.py)

**변경**:
```python
if full_text:
    if previous and full_text.startswith(previous):
        new_text = full_text[len(previous):]
    else:
        new_text = full_text
    
    # previous 업데이트는 실제로 전송한 후에만
    if new_text.strip():
        previous = full_text
```

**효과**: 빈 텍스트 전송 시 previous가 업데이트되지 않아 diff 계산 오류 방지

### 3. 답변 중복 전송 방지
**파일**: [`chatbot/app/routers/chat_consumer.py`](chatbot/app/routers/chat_consumer.py)

**추가**:
```python
answer_sent = False

# answer가 이미 전송되었으면 중복 방지
if full_text and answer_sent and full_text == previous:
    if not has_recommendations:
        continue
```

---

## 🎯 예상 결과

### 수정 후
```
[useChatSession] First delta: 안녕하세요! 오늘도...
```

**단 1번만 전송**, 중복 없음

---

## 📦 수정된 파일

```
chatbot/app/routers/chat_consumer.py                  ✅ Diff 계산 + 중복 방지
backend/itdaing-app/src/chatbot/hooks/useChatSession.js  ✅ 플래그 관리 개선
backend/itdaing-app/src/chatbot/components/RecommendationPanel.jsx ✅ M001→1 변환
```

---

## 🧪 테스트 가이드

### 재시작 필요
```bash
# FastAPI 재시작
cd /home/ubuntu/chatbot
pkill -f "uvicorn app.main:app"
uvicorn app.main:app --host 0.0.0.0 --port 9000 --reload &

# React (이미 자동 반영되었을 것)
```

### 확인사항
1. 브라우저 Console 로그 확인
2. "안녕" 입력 → 첫 delta가 1번만 출력되는지
3. "동구에 갈만한 곳" → Recommendations 로그 + 카드 표시
4. 카드 클릭 → `/popup/1` 이동 확인

