"""
Consumer 챗봇 LangGraph 노드 함수 정의.

이 모듈은 그래프의 각 노드(함수)만 포함합니다.
스키마, 프롬프트, 체인은 별도 모듈로 분리되어 있습니다.
"""
from __future__ import annotations

import json
import uuid
from typing import Any, Dict, List, Literal, Optional, Sequence, cast

from langchain_core.documents import Document
from langchain_core.messages import AIMessage, BaseMessage, RemoveMessage, ToolMessage

from app.config import get_settings
from app.graphs.shared import (
    clean_answer_text,
    detect_policy_violation,
    format_messages,
    latest_user_message,
    render_fallback_message,
)
from app.graphs.shared.utils import classify_query_type

from .chains import (
    basic_chain,
    case_with_plan_chain,
    full_classification_chain,
    hallucination_chain,
    rag_chain,
    rewrite_chain,
    summary_llm,
    unified_classify_chain,
    )
from .schemas import CaseWithPlan, FullClassification, HallucinationLabel, UnifiedIntentFeasibility
from .state import AgentState


settings = get_settings()

# RAG 컨텍스트 길이 상한 (v9: 토큰 절감을 위해 축소)
CONTEXT_DOC_MAX_CHARS = 500  # 800 → 500
CONTEXT_TOTAL_MAX_CHARS = 2000  # 3000 → 2000


# ---------------------------------------------------------------------------
# Helper utilities
# ---------------------------------------------------------------------------


def _is_realtime_info_query(query: str) -> bool:
    """
    날씨, 시간, 교통 등 실시간 정보가 필요한 질문인지 판단.
    이런 질문은 웹 검색으로 처리한다.
    """
    if not query:
        return False
    lowered = query.lower()
    realtime_keywords = (
        "날씨", "기온", "온도", "비 올", "눈 올", "비올", "눈올",
        "우산", "미세먼지", "일기예보",
        "현재 시간", "몇 시",
    )
    return any(kw in lowered for kw in realtime_keywords)


def _is_gwangju_general_query(query: str) -> bool:
    """
    광주 관련 일반 질문인지 판단 (관광지, 명소, 위치 등).
    플리마켓과 직접 관련은 없지만, 근처 플리마켓으로 연결할 수 있다.
    """
    if not query:
        return False
    
    # 광주 + 관광/명소 키워드
    gwangju_landmarks = (
        "무등산", "양림동", "충장로", "국립아시아문화전당", "5.18",
        "광주비엔날레", "광주호", "사직공원", "중외공원",
    )
    location_queries = ("어디", "위치", "가는 법", "어떻게 가")
    
    has_landmark = any(lm in query for lm in gwangju_landmarks)
    has_location_q = any(lq in query for lq in location_queries)
    
    return has_landmark and has_location_q


def _trim_text(text: str, max_chars: int) -> str:
    """Safely trim long document text to avoid huge prompts."""
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip() + "..."


def _format_context(docs: List[Document]) -> str:
    if not docs:
        return "(no documents)"

    parts: List[str] = []
    total_chars = 0

    for idx, doc in enumerate(docs, start=1):
        title = doc.metadata.get("market_name", f"Document {idx}")
        raw_content = str(doc.page_content or "")
        body = _trim_text(raw_content, CONTEXT_DOC_MAX_CHARS)

        chunk = f"## {title}\n{body}"
        if total_chars + len(chunk) > CONTEXT_TOTAL_MAX_CHARS:
            remaining = max(CONTEXT_TOTAL_MAX_CHARS - total_chars, 0)
            if remaining > 0:
                chunk = _trim_text(chunk, remaining)
                parts.append(chunk)
            break

        parts.append(chunk)
        total_chars += len(chunk)

    return "\n\n".join(parts)


def _get_query_for_reasoning(state: AgentState) -> str:
    query = state.get("query")
    if isinstance(query, str) and query.strip():
        return query.strip()
    raise ValueError("질문을 찾을 수 없습니다.")


def _get_query_for_search(state: AgentState) -> str:
    candidate = state.get("paraphrased_query")
    if isinstance(candidate, str) and candidate.strip():
        return candidate.strip()
    return _get_query_for_reasoning(state)


def _get_answer_text(state: AgentState) -> str:
    answer = state.get("answer")
    if isinstance(answer, str) and answer.strip():
        return answer
    raise ValueError("응답이 비어있습니다.")


def _build_tool_call_message(
    tool_name: str,
    query: str,
    tool_args: Optional[Dict[str, Any]] = None,
) -> tuple[str, AIMessage]:
    call_id = f"{tool_name}-{uuid.uuid4().hex}"
    args: Dict[str, Any] = {"query": query}
    if tool_args:
        args.update(tool_args)
    tool_call = {
        "id": call_id,
        "type": "tool_call",
        "name": tool_name,
        "args": args,
    }
    ai_message = AIMessage(
        content="",
        tool_calls=[tool_call],
    )
    return call_id, ai_message


def _schedule_tool(
    state: AgentState,
    *,
    tool_name: str,
    query: str,
    tool_args: Optional[Dict[str, Any]] = None,
) -> AgentState:
    call_id, ai_message = _build_tool_call_message(tool_name, query, tool_args=tool_args)
    next_state: AgentState = {
        **state,
        "messages": [ai_message],
        "pending_tool_call_id": call_id,
        "pending_tool_name": tool_name,
        "pending_tool_query": query,
    }
    if tool_name.startswith("web_search"):
        next_state["web_search_attempted"] = True
        next_state.pop("needs_web_search", None)
    else:
        next_state.pop("web_search_attempted", None)
    return next_state


def _resolve_tool_query(state: AgentState, *, web_search: bool) -> str:
    if web_search:
        return _get_query_for_reasoning(state)
    return _get_query_for_search(state)


def _structured_plan_args(state: AgentState) -> Dict[str, Any]:
    plan = state.get("structured_plan")
    if isinstance(plan, dict):
        return {"structured_plan": plan}
    return {}


def _find_tool_message(
    messages: Sequence[BaseMessage],
    call_id: str | None,
) -> ToolMessage | None:
    for message in reversed(messages):
        if isinstance(message, ToolMessage):
            if call_id is None or message.tool_call_id == call_id:
                return message
    return None


def _parse_tool_payload(content: Any) -> Dict[str, Any]:
    if isinstance(content, dict):
        return content
    if isinstance(content, str):
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            return {"type": "unknown", "raw": content}
    return {"type": "unknown", "raw": content}


def _documents_from_payload(payload: Dict[str, Any]) -> List[Document]:
    """
    도구 결과에서 문서 목록 추출.
    
    v13: SQL 도구(results) + RAG 도구(documents) 모두 지원
    """
    # RAG 도구: documents 필드
    docs_data = payload.get("documents") or []
    
    # SQL 도구: results 필드 (v13)
    if not docs_data:
        sql_results = payload.get("results") or []
        for row in sql_results:
            if not isinstance(row, dict):
                continue
            # SQL 결과를 문서 형식으로 변환
            name = row.get("name", "")
            start_date = row.get("start_date", "")
            end_date = row.get("end_date", "")
            operating_time = row.get("operating_time", "")
            zone_area_name = row.get("zone_area_name", "")
            
            content = f"마켓명: {name}"
            if start_date and end_date:
                content += f"\n기간: {start_date} ~ {end_date}"
            if operating_time:
                content += f"\n운영시간: {operating_time}"
            if zone_area_name:
                content += f"\n위치: {zone_area_name}"
            
            docs_data.append({
                "page_content": content,
                "metadata": {
                    "name": name,
                    "market_name": name,
                    "start_date": start_date,
                    "end_date": end_date,
                    "operating_time": operating_time,
                    "zone_area_name": zone_area_name,
                    "source": "sql_lookup",
                }
            })
    
    documents: List[Document] = []
    for row in docs_data:
        if not isinstance(row, dict):
            continue
        page_content = row.get("page_content") or ""
        metadata = row.get("metadata") or {}
        documents.append(
            Document(
                page_content=str(page_content),
                metadata=dict(metadata),
            )
        )
    return documents


def _build_market_recommendations(documents: List[Document], limit: int = 3) -> List[Dict[str, Any]]:
    recommendations: List[Dict[str, Any]] = []
    for doc in documents:
        metadata = doc.metadata or {}
        market_id = metadata.get("market_id")
        name = metadata.get("market_name") or metadata.get("name")
        if not (market_id or name):
            continue
        recommendations.append(
            {
                "type": "market",
                "market_id": market_id,
                "name": name,
                "address": metadata.get("address"),
                "lat": metadata.get("lat"),
                "lon": metadata.get("lon"),
                "distance_km": metadata.get("distance_km"),
                "rating": metadata.get("market_rating"),
                "category": metadata.get("market_category"),
                "attributes": metadata.get("market_attribute"),
                "amenities": metadata.get("market_ameni"),
                "metadata": metadata,
            }
        )
        if len(recommendations) >= limit:
            break
    return recommendations


# ---------------------------------------------------------------------------
# Node functions (Sync)
# ---------------------------------------------------------------------------


def extract_user_query(state: AgentState) -> AgentState:
    """사용자 메시지에서 쿼리 추출."""
    messages = state.get("messages", [])
    latest = latest_user_message(messages)
    latest_text = latest.content if isinstance(latest.content, str) else str(latest.content)
    return {
        **state,
        "query": latest_text.strip(),
    }


def _extract_market_keyword(query: str) -> str:
    """
    쿼리에서 마켓 이름/지역 키워드를 추출 (v13).
    
    예:
    - "양림동 플리마켓 언제 열어?" → "양림"
    - "상무지구 플리마켓 시간" → "상무"
    - "동명동 예술골목 마켓" → "동명동"
    """
    import re
    
    # 지역 키워드 패턴 (동/구 이름)
    district_patterns = [
        r"(양림동|양림)", r"(동명동|동명)", r"(충장로|충장)",
        r"(상무지구|상무)", r"(첨단|첨단지구)", r"(금남로|금남)",
        r"(봉선동|봉선)", r"(치평동|치평)", r"(운남동|운남)",
        r"(송정)", r"(문화전당)", r"(아시아문화)",
    ]
    
    for pattern in district_patterns:
        match = re.search(pattern, query, re.IGNORECASE)
        if match:
            return match.group(1)
    
    # 마켓 유형 키워드
    market_types = ["플리마켓", "야시장", "축제", "팝업", "전시", "마켓"]
    for mt in market_types:
        if mt in query:
            # 마켓 유형 앞의 단어를 추출
            idx = query.find(mt)
            if idx > 0:
                prefix = query[:idx].strip().split()
                if prefix:
                    return prefix[-1]
    
    # 추출 실패 시 첫 2-3단어 반환
    words = query.split()[:3]
    return " ".join(words) if words else query


def _should_use_sql_lookup(query: str, state: AgentState) -> bool:
    """
    SQL 직접 조회가 필요한지 판단.
    
    정확한 날짜/시간/상태 정보가 필요할 때 SQL 사용:
    - "언제 열어?", "몇 시에", "정확한 날짜"
    - 특정 마켓 이름을 언급하고 상세 정보 요청
    """
    if not query:
        return False
    
    lowered = query.lower()
    
    # 정확한 정보 요청 키워드
    exact_info_keywords = (
        "언제 열", "몇 시", "정확한 날짜", "정확한 시간",
        "운영시간", "운영 시간", "영업시간", "영업 시간", "오픈 시간",
        "시작일", "종료일", "기간이", "일정",
    )
    
    # 상세 정보 요청 + 이전 대화에서 마켓 추천이 있었을 때
    detail_keywords = ("자세히", "상세", "더 알려", "정보")
    has_recommendations = bool(state.get("recommendations"))
    
    if any(kw in lowered for kw in exact_info_keywords):
        return True
    
    if has_recommendations and any(kw in lowered for kw in detail_keywords):
        return True
    
    return False


def schedule_consumer_tool_async(state: AgentState) -> AgentState:
    """
    도구 호출 스케줄링 (v11: 하이브리드 RAG + SQL).
    
    도구 선택 우선순위:
    1. 실시간 정보 질문 (날씨 등) → web_search_async
    2. 정확한 날짜/시간 정보 요청 → popup_sql_lookup_async
    3. 일반 추천/검색 → consumer_retrieve_async (RAG)
    """
    query_text = _get_query_for_search(state)
    
    # 실시간 정보 질문(날씨 등)이나 광주 일반 질문은 웹 검색 사용
    use_web_search = (
        state.get("needs_web_search")
        or _is_realtime_info_query(query_text)
        or _is_gwangju_general_query(query_text)
    )
    
    # v11: 정확한 정보 요청 시 SQL 직접 조회
    use_sql_lookup = not use_web_search and _should_use_sql_lookup(query_text, state)
    
    if use_web_search:
        tool_name = "web_search_async"
        query = _resolve_tool_query(state, web_search=True)
        tool_args = None
    elif use_sql_lookup:
        tool_name = "popup_sql_lookup_async"
        # SQL 조회 시 마켓 이름/키워드 추출 (v13 수정)
        query = _resolve_tool_query(state, web_search=False)
        market_keyword = _extract_market_keyword(query_text)
        tool_args = {"name": market_keyword, "limit": 5}
    else:
        tool_name = "consumer_retrieve_async"
        query = _resolve_tool_query(state, web_search=False)
        tool_args = _structured_plan_args(state)
    
    return _schedule_tool(
        state, tool_name=tool_name, query=query, tool_args=tool_args or None
    )


def consumer_tool_router(state: AgentState) -> Literal["tools", "resume"]:
    """도구 호출 필요 여부 라우팅."""
    return "tools" if state.get("pending_tool_name") else "resume"


def consume_consumer_tool_result(state: AgentState) -> AgentState:
    """도구 실행 결과 소비."""
    call_id = state.get("pending_tool_call_id")
    messages = state.get("messages", [])
    tool_message = _find_tool_message(messages, call_id)
    if tool_message is None:
        return state

    payload = _parse_tool_payload(tool_message.content)
    documents = _documents_from_payload(payload)
    next_state: AgentState = {
        **state,
        "context": documents,
        "last_tool_payload": payload,
    }

    plan_result = payload.get("structured_plan_result")
    if plan_result:
        next_state["structured_plan_result"] = plan_result

    recs = _build_market_recommendations(documents)
    if recs:
        next_state["recommendations"] = recs
    else:
        next_state.pop("recommendations", None)

    next_state.pop("pending_tool_call_id", None)
    next_state.pop("pending_tool_name", None)
    next_state.pop("pending_tool_query", None)

    should_retry_with_web = (
        payload.get("type") == "consumer_retrieve"
        and not documents
        and bool(settings.websearch_enabled)
        and not state.get("web_search_attempted")
    )
    if should_retry_with_web:
        next_state["needs_web_search"] = True
    else:
        next_state.pop("needs_web_search", None)

    if payload.get("type") == "web_search":
        next_state["web_search_attempted"] = True

    return next_state


def consumer_tool_followup_router(state: AgentState) -> Literal["more_tools", "continue"]:
    """추가 도구 호출 필요 여부 라우팅."""
    return "more_tools" if state.get("needs_web_search") else "continue"


def format_answer_message(state: AgentState) -> AgentState:
    """최종 답변 메시지 포맷팅."""
    answer = state.get("answer", "")
    answer_text = answer if isinstance(answer, str) else str(answer)
    answer_text = clean_answer_text(answer_text)
    ai_message = AIMessage(content=answer_text)
    return {
        **state,
        "messages": [ai_message],
    }


def truncate_messages(state: AgentState) -> AgentState:
    """오래된 메시지 삭제."""
    messages = state.get("messages", [])
    max_keep = settings.max_message_history
    if len(messages) <= max_keep:
        return state

    to_remove = messages[:-max_keep]
    remove_msgs = [RemoveMessage(id=m.id) for m in to_remove if m.id]
    return {
        **state,
        "messages": remove_msgs,
    }


def hallucination_router(state: AgentState) -> Literal["not hallucinated", "hallucinated"]:
    """할루시네이션 검사 결과 라우팅."""
    label = state.get("hallucination_label", "")
    if label == "hallucinated":
        rewrite_count = state.get("rewrite_count", 0)
        if rewrite_count >= 2:
            return "not hallucinated"
        return "hallucinated"
    return "not hallucinated"


def router_async(state: AgentState) -> Literal["rag_answer", "general_answer"]:
    """
    classify_and_assess_async 결과를 기반으로 라우팅.
    
    이미 classify_and_assess_async에서 intent가 설정되어 있으므로
    별도 LLM 호출 없이 바로 라우팅한다.
    """
    # fallback_code가 설정되어 있으면 general_answer로 라우팅
    fallback_code = state.get("fallback_code")
    if fallback_code:
        return "general_answer"
    
    # intent 기반 라우팅
    intent = state.get("intent", "")
    
    # consumer_query만 RAG 경로로
    if intent == "consumer_query":
        return "rag_answer"
    
    # 나머지는 모두 general_answer
    return "general_answer"


# ---------------------------------------------------------------------------
# Node functions (Async)
# ---------------------------------------------------------------------------


async def classify_and_assess_async(state: AgentState) -> AgentState:
    """
    통합 Intent + Feasibility + Safety 노드.
    휴리스틱으로 greeting/noise/bot_about/out_of_scope를 빠르게 처리.
    """
    summary = state.get("summary", "").strip() or "요약 없음"
    query = state.get("query", "")
    query_text = query if isinstance(query, str) else ""

    next_state: AgentState = {**state}

    # 1) 휴리스틱으로 명확한 케이스는 LLM 호출을 건너뛴다.
    qtype = classify_query_type(query_text)
    if qtype == "greeting":
        next_state["intent"] = "greeting"
        next_state["risk_level"] = "low"
        next_state.pop("fallback_code", None)
        next_state.pop("fallback_detail", None)
        return next_state
    elif qtype == "noise":
        next_state["intent"] = "noise"
        next_state["risk_level"] = "low"
        next_state.pop("fallback_code", None)
        next_state.pop("fallback_detail", None)
        return next_state
    elif qtype == "bot_about":
        next_state["intent"] = "bot_about"
        next_state["risk_level"] = "low"
        next_state.pop("fallback_code", None)
        next_state.pop("fallback_detail", None)
        return next_state
    elif qtype == "out_of_scope_region":
        next_state["intent"] = "out_of_scope"
        next_state["risk_level"] = "low"
        next_state["fallback_code"] = "OUT_OF_SCOPE_REGION"
        next_state["fallback_detail"] = "광주광역시 외 지역 요청"
        return next_state
    elif qtype == "out_of_scope_topic":
        next_state["intent"] = "out_of_scope"
        next_state["risk_level"] = "low"
        next_state["fallback_code"] = "OUT_OF_SCOPE_TOPIC"
        next_state["fallback_detail"] = "플리마켓과 무관한 주제"
        return next_state

    # 2) 휴리스틱 정책 위반 체크 (키워드 기반 - 빠른 차단)
    violated, code, detail = detect_policy_violation(query_text)
    if violated and code:
        next_state["intent"] = "safety_violation"
        next_state["fallback_code"] = code
        next_state["fallback_detail"] = detail
        next_state["risk_level"] = "high"
        return next_state

    # 3) LLM 기반 통합 분류
    decision = cast(
        UnifiedIntentFeasibility,
        await unified_classify_chain.ainvoke({"summary": summary, "query": query_text}),
    )

    next_state["intent"] = decision.intent
    next_state["risk_level"] = decision.risk_level

    # Safety violation 처리
    if decision.safety_category != "SAFE":
        next_state["intent"] = "safety_violation"
        next_state["fallback_code"] = decision.safety_category
        next_state["fallback_detail"] = decision.detail
        next_state["risk_level"] = "high"
        return next_state

    # Feasibility 처리
    if decision.feasibility_code != "OK":
        next_state["fallback_code"] = decision.feasibility_code
        next_state["fallback_detail"] = decision.detail
    else:
        next_state.pop("fallback_code", None)
        next_state.pop("fallback_detail", None)

    # normalized_query를 paraphrased_query로 저장
    if decision.normalized_query:
        next_state["paraphrased_query"] = decision.normalized_query

    return next_state


async def classify_case_and_plan_async(state: AgentState) -> AgentState:
    """
    병합된 노드: case_classification + plan_structured_search를 단일 LLM 호출로 처리.
    """
    summary = state.get("summary", "").strip() or "요약 없음"
    query = _get_query_for_reasoning(state)

    result = cast(
        CaseWithPlan,
        await case_with_plan_chain.ainvoke({"summary": summary, "query": query}),
    )

    # StructuredRetrievalPlan 형식으로 변환
    structured_plan = {
        "target_entity": result.target_entity,
        "keyword_filters": [f.model_dump() for f in result.keyword_filters],
        "numeric_filters": [f.model_dump() for f in result.numeric_filters],
        "exclude_districts": result.exclude_districts,
        "sort": [s.model_dump() for s in result.sort],
        "allow_broadening": result.allow_broadening,
        "strict_filters": False,
        "rationale": result.rationale,
        "risk_level": result.risk_level,
    }

    next_state: AgentState = {
        **state,
        "case": result.case,
        "paraphrased_query": result.rewritten_query,
        "structured_plan": structured_plan,
        "entity_target": result.target_entity,
    }

    if result.risk_level == "high":
        next_state["risk_level"] = "high"
        next_state["force_hallucination_check"] = True

    return next_state


async def full_classify_async(state: AgentState) -> AgentState:
    """
    완전 통합 노드: classify_and_assess + classify_case_and_plan을 단일 LLM 호출로 처리.
    
    LLM 호출 2회 → 1회로 감소 (~5초 → ~3초 예상)
    """
    summary = state.get("summary", "").strip() or "요약 없음"
    query = state.get("query", "")
    query_text = query if isinstance(query, str) else ""

    next_state: AgentState = {**state}

    # 0) 날씨/실시간 정보 또는 광주 일반 질문 체크 (웹 검색으로 처리)
    if _is_realtime_info_query(query_text):
        next_state["intent"] = "realtime_info"
        next_state["risk_level"] = "low"
        next_state["is_realtime_query"] = True
        return next_state
    
    if _is_gwangju_general_query(query_text):
        next_state["intent"] = "gwangju_general"
        next_state["risk_level"] = "low"
        next_state["is_gwangju_general"] = True
        return next_state

    # 1) 휴리스틱으로 명확한 케이스는 LLM 호출을 건너뛴다
    qtype = classify_query_type(query_text)
    if qtype == "greeting":
        next_state["intent"] = "greeting"
        next_state["risk_level"] = "low"
        return next_state
    elif qtype == "noise":
        next_state["intent"] = "noise"
        next_state["risk_level"] = "low"
        return next_state
    elif qtype == "bot_about":
        next_state["intent"] = "bot_about"
        next_state["risk_level"] = "low"
        return next_state
    elif qtype == "out_of_scope_region":
        next_state["intent"] = "out_of_scope"
        next_state["fallback_code"] = "OUT_OF_SCOPE_REGION"
        next_state["fallback_detail"] = "광주광역시 외 지역 요청"
        return next_state
    elif qtype == "out_of_scope_topic":
        next_state["intent"] = "out_of_scope"
        next_state["fallback_code"] = "OUT_OF_SCOPE_TOPIC"
        next_state["fallback_detail"] = "플리마켓과 무관한 주제"
        return next_state

    # 2) 휴리스틱 정책 위반 체크
    violated, code, detail = detect_policy_violation(query_text)
    if violated and code:
        next_state["intent"] = "safety_violation"
        next_state["fallback_code"] = code
        next_state["fallback_detail"] = detail
        next_state["risk_level"] = "high"
        return next_state

    # 3) LLM 기반 완전 통합 분류 (단일 호출)
    result = cast(
        FullClassification,
        await full_classification_chain.ainvoke({"summary": summary, "query": query_text}),
    )

    next_state["intent"] = result.intent
    next_state["risk_level"] = result.risk_level

    # Safety violation 처리
    if result.safety_category != "SAFE":
        next_state["intent"] = "safety_violation"
        next_state["fallback_code"] = result.safety_category
        next_state["fallback_detail"] = result.detail
        next_state["risk_level"] = "high"
        return next_state

    # Feasibility 처리
    if result.feasibility_code != "OK":
        next_state["fallback_code"] = result.feasibility_code
        next_state["fallback_detail"] = result.detail

    # consumer_query인 경우 Case/Plan 정보도 설정
    if result.intent == "consumer_query" and result.feasibility_code == "OK":
        next_state["case"] = result.case
        next_state["paraphrased_query"] = result.rewritten_query
        
        # StructuredRetrievalPlan 형식으로 변환
        structured_plan = {
            "target_entity": result.target_entity,
            "keyword_filters": [f.model_dump() for f in result.keyword_filters],
            "numeric_filters": [],
            "exclude_districts": result.exclude_districts,
            "sort": [],
            "allow_broadening": result.allow_broadening,
            "strict_filters": False,
            "rationale": result.detail,
            "risk_level": result.risk_level,
        }
        next_state["structured_plan"] = structured_plan
        next_state["entity_target"] = result.target_entity

    return next_state


def full_router(state: AgentState) -> Literal["rag_answer", "general_answer"]:
    """full_classify_async 결과를 기반으로 라우팅."""
    fallback_code = state.get("fallback_code")
    if fallback_code:
        return "general_answer"
    
    # 날씨/실시간 정보, 광주 일반 질문은 basic_generate로 (웹 검색 활용)
    if state.get("is_realtime_query") or state.get("is_gwangju_general"):
        return "general_answer"
    
    intent = state.get("intent", "")
    if intent == "consumer_query":
        return "rag_answer"
    
    return "general_answer"


async def generate_async(state: AgentState) -> AgentState:
    """RAG 기반 답변 생성."""
    from app.utils.timezone import format_time_context_for_prompt
    
    context_docs = state.get("context", []) or []
    summary = state.get("summary", "").strip()
    query = _get_query_for_search(state)
    guidance_parts: List[str] = []
    plan_label = state.get("structured_plan_result")
    if plan_label == "no_match":
        guidance_parts.append("조건과 완전히 일치하는 장소가 없어도 솔직히 알리고, 가장 근접한 옵션 1~2곳만 제안하세요.")
    elif plan_label == "broadened":
        guidance_parts.append("정확 일치는 아니므로 유사한 추천임을 명시하세요.")
    policy_notes = state.get("policy_notes")
    if policy_notes:
        guidance_parts.append(f"운영/이용 주의사항: {policy_notes}")
    if guidance_parts:
        query = f"{query}\n\n[추가 지시]\n" + "\n".join(guidance_parts)
    
    # 한국시간(KST) 컨텍스트 생성
    time_context = format_time_context_for_prompt()
    
    response = await rag_chain.ainvoke(
        {
            "question": query,
            "context": _format_context(context_docs),
            "summary": summary or "요약 없음",
            "time_context": time_context,
        }
    )
    answer_text = response.content if isinstance(response.content, str) else str(response.content)
    answer_text = clean_answer_text(answer_text)
    return {
        **state,
        "answer": answer_text,
    }


async def check_hallucination_async(state: AgentState) -> AgentState:
    """할루시네이션 검사."""
    docs = state.get("context", []) or []
    answer = _get_answer_text(state)
    force_check = bool(state.get("force_hallucination_check"))
    if not force_check and (len(docs) == 0 or len(answer) < 200):
        return {
            **state,
            "hallucination_label": "not hallucinated",
            "hallucination_reason": "skipped_check_short_or_no_docs",
        }

    documents = _format_context(docs)
    result = cast(
        HallucinationLabel,
        await hallucination_chain.ainvoke(
            {"documents": documents, "student_answer": answer}
        ),
    )
    return {
        **state,
        "hallucination_label": result.label,
        "hallucination_reason": result.reason,
    }


async def rewrite_async(state: AgentState) -> AgentState:
    """쿼리 재작성."""
    summary = state.get("summary", "").strip() or "요약 없음"
    query = _get_query_for_reasoning(state)
    hallucination_label = state.get("hallucination_label", "")
    hallucination_reason = state.get("hallucination_reason", "")

    rewritten = await rewrite_chain.ainvoke(
        {
            "query": query,
            "summary": summary,
            "hallucination_label": hallucination_label,
            "hallucination_reason": hallucination_reason,
        }
    )
    rewritten_text = rewritten if isinstance(rewritten, str) else str(rewritten)

    # rewrite 횟수 증가
    rewrite_count = state.get("rewrite_count", 0) + 1

    return {
        **state,
        "paraphrased_query": rewritten_text.strip(),
        "rewrite_count": rewrite_count,
    }


async def basic_generate_async(state: AgentState) -> AgentState:
    """기본 응답 생성 (non-RAG). 날씨/광주 일반 질문은 웹 검색 활용."""
    from app.utils.search import WebSearchClient
    
    fallback_code = state.get("fallback_code")
    if fallback_code:
        detail = state.get("fallback_detail")
        answer_text = render_fallback_message(fallback_code, detail)
        return {**state, "answer": answer_text}
    
    summary = state.get("summary", "").strip() or "요약 없음"
    query = state.get("query", "")
    query_text = query if isinstance(query, str) else str(query)
    
    # 날씨/광주 일반 질문은 웹 검색 결과 활용
    is_realtime = state.get("is_realtime_query", False)
    is_gwangju_general = state.get("is_gwangju_general", False)
    
    web_context = ""
    if (is_realtime or is_gwangju_general) and settings.websearch_enabled:
        try:
            web_client = WebSearchClient(settings)
            search_query = f"광주 {query_text}" if "광주" not in query_text else query_text
            web_docs = await web_client.search_async(search_query)
            if web_docs:
                web_context = "\n".join([
                    f"- {doc.page_content[:200]}" for doc in web_docs[:3]
                ])
        except Exception:
            pass  # 웹 검색 실패 시 무시
    
    # 웹 검색 결과가 있으면 컨텍스트 포함
    if web_context:
        enhanced_query = f"""질문: {query_text}

참고 정보:
{web_context}

위 정보를 바탕으로 간단히 답변하고, 자연스럽게 광주 플리마켓/팝업으로 연결해주세요."""
    else:
        enhanced_query = query_text
    
    answer = await basic_chain.ainvoke(
        {
            "query": enhanced_query,
            "summary": summary,
        }
    )
    if isinstance(answer, str):
        answer_text = answer
    elif hasattr(answer, "content"):
        content = getattr(answer, "content")
        answer_text = content if isinstance(content, str) else str(content)
    else:
        answer_text = str(answer)
    answer_text = clean_answer_text(answer_text)
    return {
        **state,
        "answer": answer_text,
    }


async def summarize_messages_async(state: AgentState) -> AgentState:
    """
    대화 요약 (v10 최적화).
    
    - summary_min_messages 이하면 요약 스킵 (짧은 대화에서 LLM 호출 방지)
    - 이전 요약이 있으면 증분 요약 (새 메시지만 추가)
    - 프롬프트 간소화로 토큰 절감
    """
    messages = state.get("messages", [])
    summary = state.get("summary", "")
    
    if not messages:
        return state

    if not settings.summary_enabled:
        return state

    # v10: summary_min_messages 조건 강화 (기본값 6)
    # 3턴 이하 대화(6개 메시지 이하)에서는 요약하지 않음
    if len(messages) < settings.summary_min_messages:
        return state

    if len(messages) <= settings.max_message_history:
        return state

    if settings.summary_min_answer_chars > 0:
        answer = state.get("answer", "")
        answer_text = answer if isinstance(answer, str) else str(answer)
        if len(answer_text) < settings.summary_min_answer_chars:
            return state

    # v10: 증분 요약 - 이전 요약이 있으면 새 메시지만 요약에 추가
    if summary:
        # 이전 요약이 있으면 최근 2개 메시지만 추가 요약
        new_messages = messages[-2:] if len(messages) >= 2 else messages
        prompt = f"이전 요약: {summary}\n\n새 대화:\n{format_messages(new_messages)}\n\n위 내용을 50자 이내로 통합 요약:"
    else:
        # 첫 요약: 최근 메시지만 요약
        recent_messages = messages[-settings.max_message_history:]
        prompt = f"대화 내용:\n{format_messages(recent_messages)}\n\n위 대화를 50자 이내로 요약:"
    
    summary_text = await summary_llm.ainvoke(prompt)
    new_summary = summary_text.content if isinstance(summary_text.content, str) else str(summary_text.content)
    
    return {
        **state,
        "summary": new_summary,
    }


__all__ = [
    # Sync nodes/routers
    "extract_user_query",
    "schedule_consumer_tool_async",
    "consumer_tool_router",
    "consume_consumer_tool_result",
    "consumer_tool_followup_router",
    "format_answer_message",
    "truncate_messages",
    "hallucination_router",
    "router_async",
    "full_router",
    # Async nodes
    "classify_and_assess_async",
    "classify_case_and_plan_async",
    "full_classify_async",
    "generate_async",
    "check_hallucination_async",
    "rewrite_async",
    "basic_generate_async",
    "summarize_messages_async",
]
