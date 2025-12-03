from __future__ import annotations

"""
Seller-facing LangGraph nodes and helpers.

`seller_graph.py`에서 정의하던 상태/노드/프롬프트를 모듈화했고,
실제 로직은 기존 설계와 동일하게 유지된다.
"""

import json
import uuid
from typing import Any, Dict, List, Literal, Optional, Sequence, cast

from langchain_core.documents import Document
from langchain_core.messages import AIMessage, BaseMessage, RemoveMessage, ToolMessage
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate, PromptTemplate
from langgraph.graph import MessagesState
from pydantic import BaseModel, Field
from typing_extensions import NotRequired

from langchain_openai import ChatOpenAI

from app.chains.seller import build_seller_rag_chain
from app.config import get_settings
from app.graphs.shared import (
    IntentDecision,
    StructuredRetrievalPlan,
    clean_answer_text,
    detect_policy_violation,
    format_messages,
    latest_user_message,
    render_fallback_message,
)
from app.graphs.shared.utils import classify_query_type


settings = get_settings()

# RAG 컨텍스트 길이 상한: 셀러 존 설명도 과도하게 길어지지 않도록 제한한다.
ZONE_CONTEXT_DOC_MAX_CHARS = 800
ZONE_CONTEXT_TOTAL_MAX_CHARS = 3000

def _llm(temperature: float = 0.0) -> ChatOpenAI:
    """LLM 인스턴스 생성 헬퍼 (키 로테이션 지원)."""
    from app.utils.key_rotation import get_key_manager

    def _api_key_provider() -> str:
        """키 매니저에서 현재 활성 키 반환 (로테이션)"""
        key_manager = get_key_manager()
        return key_manager.get_current_key()

    # Runtime 시그니처는 model / temperature / api_key를 지원하지만,
    # 타입 스텁이 오래된 경우 call-arg 오류가 날 수 있어 무시한다.
    return ChatOpenAI(  # type: ignore[call-arg]
        model=settings.openai_model,
        temperature=temperature,
        api_key=_api_key_provider,
    )


router_llm = _llm(temperature=0)
intent_llm = _llm(temperature=0)
case_classification_llm = _llm(temperature=0)
hallucination_llm = _llm(temperature=0)
rewrite_llm = _llm(temperature=0)
basic_llm = _llm(temperature=0.3)
summary_llm = _llm(temperature=0)
feasibility_llm = _llm(temperature=0)
structured_plan_llm = _llm(temperature=0)
policy_llm = _llm(temperature=0)
rag_chain = build_seller_rag_chain(settings)

# Intent classification prompt & chain
INTENT_SYSTEM_PROMPT = """
당신은 광주 플리마켓 셀러 챗봇의 의도 분류기입니다.
사용자의 질문을 다음 카테고리 중 하나로 분류하세요:

- greeting: 인사 (안녕, 반가워, 고마워 등)
- bot_about: 챗봇/서비스 소개 질문
- chitchat: 일상적인 대화
- seller_query: 셀러 관련 질문 (존 추천, 상권 정보, 운영 팁 등)
- out_of_scope: 서비스 범위 밖 질문 (광주 외 지역, 관련 없는 주제)
- safety_violation: 위험하거나 불법적인 요청
- noise: 의미 없는 입력

질문을 RAG/검색에 적합하도록 명확하게 다시 작성해서 normalized_query에 출력하세요.
""".strip()

intent_prompt = ChatPromptTemplate.from_messages([
    ("system", INTENT_SYSTEM_PROMPT),
    ("user", "이전 대화 요약:\n{summary}\n\n사용자 질문:\n{query}"),
])
intent_chain = intent_prompt | intent_llm.with_structured_output(IntentDecision)


# ---------------------------------------------------------------------------
# Conversation-aware state and helpers (ported from bot4s.AgentState)
# ---------------------------------------------------------------------------


class AgentState(MessagesState):
    """Seller assistant state augmented with retrieval metadata."""

    summary: NotRequired[str]
    query: NotRequired[str]
    case: NotRequired[str]
    paraphrased_query: NotRequired[str]
    context: NotRequired[List[Document]]
    answer: NotRequired[str]
    hallucination_label: NotRequired[str]
    hallucination_reason: NotRequired[str]
    pending_tool_call_id: NotRequired[str]
    pending_tool_name: NotRequired[str]
    pending_tool_query: NotRequired[str]
    needs_web_search: NotRequired[bool]
    web_search_attempted: NotRequired[bool]
    last_tool_payload: NotRequired[Dict[str, Any]]
    recommendations: NotRequired[List[Dict[str, Any]]]
    structured_plan: NotRequired[Dict[str, Any]]
    structured_plan_result: NotRequired[str]
    entity_target: NotRequired[str | None]
    intent: NotRequired[str]
    # Lightweight intent label (예: greeting, chitchat, seller_query 등)
    fallback_code: NotRequired[str | None]
    fallback_detail: NotRequired[str | None]
    risk_level: NotRequired[str | None]
    force_hallucination_check: NotRequired[bool]
    policy_notes: NotRequired[str]
    analysis_notes: NotRequired[str]


# ---------------------------------------------------------------------------
# FullSellerClassification: 통합 분류 스키마 (LLM 호출 4회 → 1회)
# ---------------------------------------------------------------------------

class KeywordFilterSchema(BaseModel):
    """키워드 필터 스키마."""
    field: str = Field(default="district", description="필터 대상 필드")
    include: List[str] = Field(default_factory=list, description="포함할 값")
    exclude: List[str] = Field(default_factory=list, description="제외할 값")


class FullSellerClassification(BaseModel):
    """
    Intent + Feasibility + Case + Plan을 단일 LLM 호출로 통합한 스키마.
    
    classify_intent + assess_feasibility + case_classification + plan_structured_search를 병합.
    LLM 호출 4회 → 1회로 감소 (~15초 → ~5초 예상)
    """
    
    # === Intent/Feasibility (모든 경로에서 사용) ===
    intent: Literal[
        "greeting",
        "bot_about",
        "chitchat",
        "seller_query",
        "out_of_scope",
        "safety_violation",
        "noise",
    ] = Field(default="seller_query", description="의도 분류")
    
    feasibility_code: Literal[
        "OK",
        "OUT_OF_SCOPE_REGION",
        "NOT_IMPLEMENTED",
        "INSUFFICIENT_DATA",
        "POLICY_RESTRICTED",
    ] = Field(default="OK", description="실행 가능성 코드")
    
    risk_level: Literal["low", "medium", "high"] = Field(
        default="low",
        description="위험 수준 (법규/안전 관련 high)",
    )
    
    detail: str = Field(
        default="",
        description="짧은 한국어 이유/설명",
    )
    
    # === Case/Plan (seller_query일 때만 유효) ===
    case: Literal[
        "zone_recommendation",
        "commercial_info",
        "operation_guide",
        "seasonal_guide",
        "target_customer",
        "general",
    ] = Field(
        default="zone_recommendation",
        description="질문 유형 분류",
    )
    
    rewritten_query: str = Field(
        default="",
        description="검색에 최적화된 한국어 쿼리",
    )
    
    target_entity: Literal["zone", "store", "either"] = Field(
        default="zone",
        description="판매자용이므로 기본 zone",
    )
    
    keyword_filters: List[KeywordFilterSchema] = Field(
        default_factory=list,
        description="키워드 필터 (district, neighborhood 등)",
    )
    
    exclude_districts: List[str] = Field(
        default_factory=list,
        description="제외할 구 (동구, 서구, 남구, 북구, 광산구)",
    )
    
    allow_broadening: bool = Field(
        default=True,
        description="조건 완화 허용",
    )


# ---------------------------------------------------------------------------
# FullSellerClassification 프롬프트 & 체인
# ---------------------------------------------------------------------------

FULL_SELLER_CLASSIFICATION_SYSTEM_PROMPT = """
당신은 광주광역시 플리마켓 셀러 챗봇 '잇다잉'의 통합 분류기입니다.

사용자 질문을 분석하여 **한 번에** 모든 판단을 수행하세요:

## 1. Intent 분류
- greeting: 인사 ("안녕", "고마워")
- bot_about: 챗봇/서비스 소개 질문
- chitchat: 가벼운 잡담
- seller_query: 셀러 관련 질문 (존 추천, 상권 정보, 운영 팁, 임대료, 유동인구 등)
- out_of_scope: 광주 외 지역 (서울, 부산 등)
- safety_violation: 안전 위반 (불법, 위조품 등)
- noise: 의미 없는 입력

## 2. Feasibility 코드
- OK: 셀러 관련 질문 (존 추천, 상권, 임대료, 유동인구, 운영 팁 등)
- OUT_OF_SCOPE_REGION: 광주광역시 외 지역 명시적 요청
- NOT_IMPLEMENTED: 복잡한 데이터 계산 요청
- INSUFFICIENT_DATA: 특정 수치 요청
- POLICY_RESTRICTED: 불법/위험 요청

## 3. Case 분류 (seller_query일 때)
- zone_recommendation: 존/장소 추천 (동구에서 추천, 임대료 저렴한 곳, 유동인구 많은 곳)
- commercial_info: 상권/유동인구/임대료 정보 조회
- operation_guide: 운영 팁/가이드
- seasonal_guide: 계절/시즌 가이드
- target_customer: 타겟 고객 관련
- general: 기타

## 4. 검색 계획 (seller_query일 때)
- rewritten_query: "광주" 포함하여 검색에 적합하게 재작성
- target_entity: 판매자용이므로 항상 "zone"
- keyword_filters: 지역 필터
  - "동구에서" → keyword_filters에 district include: ["동구"]
  - "북구 말고" → exclude_districts: ["북구"]
- allow_broadening: 항상 True

## 사용 가능한 필터 필드
- district: 광주광역시 구 (동구, 서구, 남구, 북구, 광산구)
- neighborhood: 동네/지역명
- commercial_grade: 상권 등급 (S, A, B+, B, C)

## 핵심 규칙
- **셀러 관련 모든 질문 → seller_query + OK**:
  - "동구 존 추천" → (seller_query, OK, zone_recommendation)
  - "임대료 저렴한 곳" → (seller_query, OK, zone_recommendation)
  - "20대 많은 곳" → (seller_query, OK, zone_recommendation)
  - "주말에 장사 좋은 곳" → (seller_query, OK, zone_recommendation)
  - "충장로 유동인구" → (seller_query, OK, commercial_info)
- "동구/서구/남구/북구/광산구" → 광주 구로 해석
- "서울에서 플리마켓" → (out_of_scope, OUT_OF_SCOPE_REGION)
""".strip()

full_seller_classification_prompt = ChatPromptTemplate.from_messages([
    ("system", FULL_SELLER_CLASSIFICATION_SYSTEM_PROMPT),
    ("user", "이전 대화 요약:\n{summary}\n\n사용자 질문:\n{query}"),
])

full_seller_classification_llm = _llm(temperature=0)
full_seller_classification_chain = (
    full_seller_classification_prompt
    | full_seller_classification_llm.with_structured_output(FullSellerClassification)
)


def _trim_text(text: str, max_chars: int) -> str:
    """Safely trim long zone descriptions to avoid huge prompts."""

    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip() + "..."


def _format_context(docs: Sequence[Document]) -> str:
    if not docs:
        return "(no documents)"
    parts: List[str] = []
    total_chars = 0
    for idx, doc in enumerate(docs, start=1):
        title = doc.metadata.get("zone_name", f"Zone {idx}")
        raw_content = str(doc.page_content or "")
        body = _trim_text(raw_content, ZONE_CONTEXT_DOC_MAX_CHARS)
        chunk = f"## {title}\n{body}"
        if total_chars + len(chunk) > ZONE_CONTEXT_TOTAL_MAX_CHARS:
            remaining = max(ZONE_CONTEXT_TOTAL_MAX_CHARS - total_chars, 0)
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
        return answer.strip()
    raise ValueError("응답이 비어있습니다.")


def _build_tool_call_message(
    tool_name: str,
    query: str,
    tool_args: Optional[Dict[str, Any]] = None,
) -> tuple[str, AIMessage]:
    call_id = f"{tool_name}-{uuid.uuid4().hex}"
    # LangChain v1 ToolCall 스키마(name/args)를 따른다.
    # 참고: https://docs.langchain.com/oss/python/langchain/overview
    args: Dict[str, Any] = {"query": query}
    if tool_args:
        args.update(tool_args)
    tool_call = {
        "id": call_id,
        "type": "tool_call",
        "name": tool_name,
        "args": args,
    }
    message = AIMessage(
        content="",
        tool_calls=[tool_call],
    )
    return call_id, message


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
    # NOTE: web_search_attempted는 유지 (리셋하지 않음) - 무한 루프 방지
    
    # 도구 호출 횟수 추적 (무한 루프 방지)
    tool_call_count = state.get("tool_call_count", 0) + 1
    next_state["tool_call_count"] = tool_call_count
    
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


def _matches_category(allowed: Sequence[str], item: str) -> bool:
    lowered_item = item.lower()
    for category in allowed:
        token = str(category).lower()
        if not token:
            continue
        if lowered_item in token or token in lowered_item:
            return True
    return False


def schedule_seller_tool(state: AgentState) -> AgentState:
    tool_name = "web_search" if state.get("needs_web_search") else "seller_retrieve"
    query = _resolve_tool_query(state, web_search=tool_name.startswith("web_search"))
    tool_args = _structured_plan_args(state)
    return _schedule_tool(
        state, tool_name=tool_name, query=query, tool_args=tool_args or None
    )


def schedule_seller_tool_async(state: AgentState) -> AgentState:
    # 도구 호출 횟수 제한 (무한 루프 방지)
    tool_call_count = state.get("tool_call_count", 0)
    MAX_TOOL_CALLS = 5
    if tool_call_count >= MAX_TOOL_CALLS:
        # 도구 호출 제한 초과 시 스킵 (generate로 바로 이동)
        return {**state, "pending_tool_name": None}
    
    tool_name = "web_search_async" if state.get("needs_web_search") else "seller_retrieve_async"
    query = _resolve_tool_query(state, web_search=tool_name.startswith("web_search"))
    tool_args = _structured_plan_args(state)
    return _schedule_tool(
        state, tool_name=tool_name, query=query, tool_args=tool_args or None
    )


def seller_tool_router(state: AgentState) -> Literal["tools", "resume"]:
    return "tools" if state.get("pending_tool_name") else "resume"


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
    documents: List[Document] = []
    for row in payload.get("documents") or []:
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


def _build_zone_recommendations(documents: List[Document], limit: int = 3) -> List[Dict[str, Any]]:
    """
    검색된 존 문서에서 프론트엔드용 추천 목록을 생성합니다.
    
    상권 정보, 유동인구, 위도/경도 등 판매자에게 유용한 정보를 포함합니다.
    """
    recommendations: List[Dict[str, Any]] = []
    for doc in documents:
        metadata = doc.metadata or {}
        zone_id = metadata.get("zone_id")
        name = metadata.get("zone_name") or metadata.get("name") or metadata.get("zone_id")
        if not (zone_id or name):
            continue
        
        rec = {
            "type": "zone",
            "zone_id": zone_id,
            "name": name,
            "address": metadata.get("address") or metadata.get("detailed_address"),
            "lat": metadata.get("lat"),
            "lng": metadata.get("lng") or metadata.get("lon"),
            "district": metadata.get("district"),
            "neighborhood": metadata.get("neighborhood"),
            # 상권 정보
            "commercial_grade": metadata.get("commercial_grade"),
            "traffic_score": metadata.get("traffic_score"),
            "competition_score": metadata.get("competition_score"),
            "potential_score": metadata.get("potential_score"),
            "weekday_traffic": metadata.get("weekday_traffic"),
            "weekend_traffic": metadata.get("weekend_traffic"),
            "avg_sales": metadata.get("avg_sales"),
            "rent_per_day": metadata.get("rent_per_day"),
            "best_products": metadata.get("best_products"),
            # 기존 필드
            "category": metadata.get("zone_type") or metadata.get("type"),
            "style_tags": metadata.get("zone_style_tags"),
            "allowed_categories": metadata.get("allowed_categories"),
            "metadata": metadata,
        }
        
        # None 값 제거
        rec = {k: v for k, v in rec.items() if v is not None}
        recommendations.append(rec)
        
        if len(recommendations) >= limit:
            break
    return recommendations


def _apply_district_filter(documents: List[Document], state: AgentState) -> List[Document]:
    """
    structured_plan의 district 필터를 적용하여 문서를 필터링합니다.
    
    - keyword_filters에 district include가 있으면 해당 구만 반환
    - exclude_districts가 있으면 해당 구 제외
    """
    plan = state.get("structured_plan")
    if not isinstance(plan, dict):
        return documents
    
    # 1) keyword_filters에서 district include 추출
    include_districts: List[str] = []
    keyword_filters = plan.get("keyword_filters", [])
    for kf in keyword_filters:
        if isinstance(kf, dict) and kf.get("field") == "district":
            include_districts.extend(kf.get("include", []))
    
    # 2) exclude_districts 추출
    exclude_districts: List[str] = plan.get("exclude_districts", [])
    
    # 필터가 없으면 원본 반환
    if not include_districts and not exclude_districts:
        return documents
    
    filtered: List[Document] = []
    for doc in documents:
        metadata = doc.metadata or {}
        district = metadata.get("district", "")
        
        # include 필터: 지정된 구만 포함
        if include_districts and district not in include_districts:
            continue
        
        # exclude 필터: 지정된 구 제외
        if exclude_districts and district in exclude_districts:
            continue
        
        filtered.append(doc)
    
    # 필터링 후 결과가 없으면 원본 반환 (allow_broadening)
    if not filtered and plan.get("allow_broadening", True):
        return documents
    
    return filtered


def consume_seller_tool_result(state: AgentState) -> AgentState:
    call_id = state.get("pending_tool_call_id")
    messages = state.get("messages", [])
    tool_message = _find_tool_message(messages, call_id)
    if tool_message is None:
        return state

    payload = _parse_tool_payload(tool_message.content)
    documents = _documents_from_payload(payload)
    
    # district 후처리 필터 적용
    documents = _apply_district_filter(documents, state)
    
    next_state: AgentState = {
        **state,
        "context": documents,
        "last_tool_payload": payload,
    }

    next_state.pop("pending_tool_call_id", None)
    next_state.pop("pending_tool_name", None)
    next_state.pop("pending_tool_query", None)

    # 도구 호출 횟수가 너무 많으면 추가 호출 차단 (무한 루프 방지)
    tool_call_count = state.get("tool_call_count", 0)
    MAX_TOOL_CALLS = 5  # 최대 도구 호출 횟수
    
    should_retry_with_web = (
        payload.get("type") == "seller_retrieve"
        and not documents
        and bool(settings.websearch_enabled)
        and not state.get("web_search_attempted")
        and tool_call_count < MAX_TOOL_CALLS  # 도구 호출 제한
    )
    if should_retry_with_web:
        next_state["needs_web_search"] = True
    else:
        next_state.pop("needs_web_search", None)

    if payload.get("type") == "web_search":
        next_state["web_search_attempted"] = True

    plan_result = payload.get("structured_plan_result")
    if plan_result:
        next_state["structured_plan_result"] = plan_result

    recs = _build_zone_recommendations(documents)
    if recs:
        next_state["recommendations"] = recs
    else:
        next_state.pop("recommendations", None)

    return next_state


def analyze_zone_performance(state: AgentState) -> AgentState:
    docs = state.get("context", []) or []
    if not docs:
        return state
    insights: List[str] = []
    for doc in docs:
        metadata = doc.metadata or {}
        zone_name = metadata.get("zone_name") or metadata.get("zone_id") or "해당 존"
        age_40 = metadata.get("age_ratio_40s_plus")
        evening = metadata.get("evening_peak_score")
        night = metadata.get("night_peak_score")
        tag_count = metadata.get("tag_count")
        if isinstance(age_40, (int, float)) and age_40 >= 0.5:
            insights.append(f"{zone_name}: 40대 이상 비중 {int(age_40 * 100)}%")
        if isinstance(evening, (int, float)) and evening >= 0.5:
            insights.append(f"{zone_name}: 18시 이후 피크 타임 강함")
        if isinstance(night, (int, float)) and night >= 0.5:
            insights.append(f"{zone_name}: 야간 판매 수요가 높음")
        if isinstance(tag_count, (int, float)) and tag_count <= 3:
            insights.append(f"{zone_name}: 콘셉트 태그 {int(tag_count)}개로 집중도 높음")
    if insights:
        return {**state, "analysis_notes": "; ".join(insights)}
    next_state: AgentState = dict(state)
    next_state.pop("analysis_notes", None)
    return next_state


def check_allowed_categories(state: AgentState) -> AgentState:
    docs = state.get("context", []) or []
    if not docs:
        return state
    query = _get_query_for_reasoning(state)
    assessment = cast(
        AllowedCategoryRequest,
        allowed_category_chain.invoke({"query": query}),
    )
    requested = [item for item in assessment.requested_items if item]
    warnings: List[str] = []
    for doc in docs:
        metadata = doc.metadata or {}
        allowed = [str(x) for x in metadata.get("allowed_categories") or []]
        if requested and allowed:
            conflicts = [
                item for item in requested if not _matches_category(allowed, item)
            ]
            if conflicts:
                zone_name = metadata.get("zone_name", "해당 존")
                warnings.append(
                    f"{zone_name}: {', '.join(conflicts)} 판매는 허용 범위인지 주최 측 확인 필요"
                )
        elif requested and not allowed:
            zone_name = metadata.get("zone_name", "해당 존")
            warnings.append(f"{zone_name}: 허용 업종 정보가 없어 주최 측 확인이 필요합니다.")
    if assessment.risky_items:
        warnings.append(f"추가 확인 필요 품목: {', '.join(assessment.risky_items)}")
    if warnings:
        return {**state, "policy_notes": " / ".join(warnings)}
    next_state: AgentState = dict(state)
    next_state.pop("policy_notes", None)
    return next_state


def seller_tool_followup_router(state: AgentState) -> Literal["more_tools", "continue"]:
    # NOTE: 웹 검색 루프 비활성화 (무한 루프 방지)
    # 웹 검색이 필요하더라도 일단 continue로 진행
    # return "more_tools" if state.get("needs_web_search") else "continue"
    return "continue"


# ---------------------------------------------------------------------------
# Router & prompts (seller tone preserved from bot4s)
# ---------------------------------------------------------------------------


class FeasibilityDecision(BaseModel):  # type: ignore[misc]
    code: Literal[
        "OK",
        "OUT_OF_SCOPE_REGION",
        "NOT_IMPLEMENTED",
        "INSUFFICIENT_DATA",
        "POLICY_RESTRICTED",
    ] = "OK"
    detail: str = ""
    risk_level: Literal["low", "medium", "high"] = "low"


class AllowedCategoryRequest(BaseModel):  # type: ignore[misc]
    requested_items: List[str] = Field(default_factory=list)
    risky_items: List[str] = Field(default_factory=list)
    needs_warning: bool = False


class Route(BaseModel):  # type: ignore[misc]
    target: Literal["rag_answer", "general_answer"]


router_system_prompt = """
You are the routing assistant for '잇다잉(Itdaing)', 광주광역시 플리마켓/팝업 셀러 전용 존 추천 서비스.
Decide whether the user's question should be answered via rag_answer (tool/DB 기반) or general_answer.

The vector store contains detailed information about zone recommendations for sellers,
including descriptions, locations, categories, visitor patterns, and atmosphere tags.

Choose ``rag_answer`` unless the question is unrelated to picking a zone in Gwangju,
requests other cities, or clearly aims to overload the system.
""".strip()

router_prompt = ChatPromptTemplate.from_messages(
    [
        ("system", router_system_prompt),
        ("user", "이전 대화 요약:\n{summary}\n\n사용자 질문:\n{query}"),
    ]
)

structured_router_llm = router_llm.with_structured_output(Route)
router_chain = router_prompt | structured_router_llm


feasibility_system_prompt = """
당신은 광주 셀러 챗봇의 정책/기능 가드레일 평가자입니다.
다음 코드 중 하나를 선택하세요.

- OK: 정상 처리 가능 (존 추천, 상권 정보, 임대료, 유동인구, 운영 팁 등 셀러 관련 질문)
- OUT_OF_SCOPE_REGION: 광주광역시 외 지역 명시적 요청 (서울, 부산, 대구 등)
- NOT_IMPLEMENTED: 복잡한 데이터 계산/정렬/비교 분석 요청
- INSUFFICIENT_DATA: 특정 날짜/시간대 정확한 수치 요청 등 데이터 없음
- POLICY_RESTRICTED: 불법/편법/법규 위반 가능성 (위조품, 무허가 음식 등)

중요: 다음은 모두 OK로 분류해야 합니다:
- 존 추천, 상권 추천, 장사 좋은 곳
- 임대료, 비용, 저렴한 곳
- 유동인구, 연령대, 타겟 고객
- 주말/평일/야간 장사
- 특정 구(동구, 서구, 남구, 북구, 광산구) 관련 질문

detail에는 짧은 이유를 한국어로 적고, risk_level은 low/medium/high 중 하나를 선택하세요.
""".strip()

feasibility_prompt = ChatPromptTemplate.from_messages(
    [
        ("system", feasibility_system_prompt),
        ("user", "이전 대화 요약:\n{summary}\n\n사용자 질문:\n{query}"),
    ]
)

feasibility_chain = feasibility_prompt | feasibility_llm.with_structured_output(
    FeasibilityDecision
)


SELLER_STRUCTURED_PLAN_SYSTEM_PROMPT = """
당신은 광주 존 추천을 위한 structured planner입니다.

사용 가능한 필드:

Keyword fields (keyword_filters 사용):
- district: 광주광역시 구 (동구, 서구, 남구, 북구, 광산구)
- neighborhood: 동네/지역명
- best_products: 추천 판매 상품
- commercial_grade: 상권 등급 (S, A, B+, B, C)

Numeric fields (numeric_filters 사용):
- traffic_score: 유동인구 점수 (0-100)
- competition_score: 경쟁도 점수 (0-100, 낮을수록 경쟁 적음)
- potential_score: 성장 잠재력 점수 (0-100)
- rent_per_day: 일일 임대료 (원)
- weekday_traffic: 평일 유동인구
- weekend_traffic: 주말 유동인구

정렬 예시:
- 임대료 저렴: rent_per_day 오름차순
- 유동인구 많음: traffic_score 내림차순
- 경쟁 적음: competition_score 오름차순

지역 필터 사용법:
- "동구에서 추천해줘" → keyword_filters에 district: include: ["동구"]
- "광산구 제외" → exclude_districts에 ["광산구"]

target_entity는 기본적으로 "zone"입니다.
allow_broadening은 true로 유지하세요 (검색 결과가 없을 때 확장 허용).
""".strip()

seller_structured_plan_prompt = ChatPromptTemplate.from_messages(
    [
        ("system", SELLER_STRUCTURED_PLAN_SYSTEM_PROMPT),
        ("user", "이전 대화 요약:\n{summary}\n\n사용자 질문:\n{query}"),
    ]
)

seller_structured_plan_chain = (
    seller_structured_plan_prompt
    | structured_plan_llm.with_structured_output(StructuredRetrievalPlan)
)


allowed_category_prompt = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            "사용자가 판매하려는 품목/서비스를 추출해 requested_items에 적고, "
            "위험하거나 허용 여부가 불확실한 품목은 risky_items에 추가하세요.",
        ),
        ("user", "{query}"),
    ]
)

allowed_category_chain = allowed_category_prompt | policy_llm.with_structured_output(
    AllowedCategoryRequest
)


class CaseClassification(BaseModel):  # type: ignore[misc]
    case: Literal[
        "zone_info",
        "category_fit",
        "seller_recommend",
        "traffic_peak",
        "demographics",
    ]
    rewritten_query: str = Field(description="벡터 검색에 적합한 한국어 문장")


case_classification_system_prompt = """
당신은 광주광역시 존 추천 전문가 '잇다잉(Itdaing)'의 질문분류기이자 문장 작성기입니다.

질문을 보고:
1) 아래 라벨 중 하나를 case로 출력하고
2) 벡터 검색에 적합하도록 한국어로 명확히 다시 써서 rewritten_query로 반환하세요.
""".strip()

case_classification_prompt = ChatPromptTemplate.from_messages(
    [
        ("system", case_classification_system_prompt),
        ("user", "이전 대화 요약:\n{summary}\n\n사용자 질문:\n{query}"),
    ]
)

case_classification_chain = case_classification_prompt | case_classification_llm.with_structured_output(
    CaseClassification
)


hallucination_prompt = PromptTemplate.from_template(
    """
You are a teacher checking if the assistant's answer is grounded in the zone documents.
Respond with a label ("hallucinated" or "not hallucinated") and a short reason.

Documents:
{documents}

Student answer:
{student_answer}
""".strip()
)


rewrite_dictionary = """
열만한 데, 차릴만한 곳 -> 플리마켓을 열만한 존
곳, 존, 장소, 지역 -> 플리마켓을 열만한 존
""".strip()

rewrite_prompt = PromptTemplate.from_template(
    f"""
당신은 광주광역시 플리마켓 존 추천 챗봇의 쿼리 재작성 도우미입니다.
사전과 질문과 할루시네이션 정보를 참고해 검색용 한국어 문장을 한 줄로 출력하세요.

사전:
{rewrite_dictionary}

이전 대화 요약:
{{summary}}

질문:
{{query}}

할루시네이션 라벨: {{hallucination_label}}
할루시네이션 이유: {{hallucination_reason}}

출력 형식:
- 마켓존 추천을 명확히 드러내는 한 문장만 출력합니다.
- 말머리, 따옴표, 리스트, 번역은 금지입니다.
""".strip()
)


basic_system_prompt = """
당신은 광주광역시 플리마켓 및 팝업스토어 셀러를 돕는 '잇다잉(Itdaing)'의 간단 응답용 챗봇입니다.

질문 유형별 응답 방향:

1) **셀러/존 관련 일반 질문**:
   → 가능한 범위에서 실무 팁과 체크리스트 중심으로 2~3문장 안에서 답하세요.

2) **광주 외 지역/플랫폼 범위 밖 요청**:
   → "잇다잉은 광주 플리마켓 셀러를 위한 서비스라 다른 지역은 자세히 안내하기 어려워요. 광주에서의 셀러 활동에 대해 물어봐 주시면 더 잘 도와드릴게요."

3) **챗봇/서비스 소개 질문 (bot_about)**:
   → "저는 광주광역시 플리마켓·팝업스토어 셀러분들을 위한 존 추천과 운영 가이드를 도와주는 잇다잉 챗봇이에요. 판매 품목이나 목표를 알려주시면 어울리는 존과 전략을 함께 고민해 드릴게요."

4) **간단한 인사**:
   → 친근하고 짧게 인사한 뒤, 어떤 셀러 고민을 돕고 있는지 한 문장으로 소개하세요.
""".strip()

basic_prompt = ChatPromptTemplate.from_messages(
    [
        ("system", basic_system_prompt),
        ("user", "이전 대화 요약:\n{summary}\n\n사용자 질문:\n{query}"),
    ]
)


class Hallucination(BaseModel):  # type: ignore[misc]
    label: Literal["hallucinated", "not hallucinated"]
    reason: str


structured_hallucination_llm = hallucination_llm.with_structured_output(Hallucination)
hallucination_chain = hallucination_prompt | structured_hallucination_llm
rewrite_chain = rewrite_prompt | rewrite_llm | StrOutputParser()
basic_chain = basic_prompt | basic_llm | StrOutputParser()


# ---------------------------------------------------------------------------
# Graph nodes
# ---------------------------------------------------------------------------


def extract_user_query(state: AgentState) -> AgentState:
    latest = latest_user_message(state.get("messages", []))
    latest_text = latest.content if isinstance(latest.content, str) else str(latest.content)
    return {**state, "query": latest_text.strip()}


def classify_intent_node(state: AgentState) -> AgentState:
    """
    High-level intent classifier for seller chatbot.

    - greeting/noise 는 휴리스틱으로 빠르게 처리
    - 그 외에는 LLM 기반 IntentDecision 을 사용
    """

    summary = state.get("summary", "").strip() or "요약 없음"
    query = state.get("query", "")
    query_text = query if isinstance(query, str) else ""

    qtype = classify_query_type(query_text)
    if qtype == "greeting":
        intent = "greeting"
        normalized = query_text.strip()
    elif qtype == "noise":
        intent = "noise"
        normalized = query_text.strip()
    else:
        # ChatPromptTemplate + 체인으로 LLM 호출 (버그 수정)
        decision = cast(
            IntentDecision,
            intent_chain.invoke({"summary": summary, "query": query_text}),
        )
        intent = decision.intent
        normalized = decision.normalized_query.strip() or query_text.strip()

    next_state: AgentState = {**state, "intent": intent}
    if normalized and normalized != query_text:
        next_state["query"] = normalized
    return next_state

def router(state: AgentState) -> Literal["rag_answer", "general_answer"]:
    intent = state.get("intent")
    if intent in {"greeting", "bot_about", "chitchat", "noise", "safety_violation"}:
        return "general_answer"

    if state.get("fallback_code"):
        return "general_answer"
    summary = state.get("summary", "").strip() or "요약 없음"
    query = state.get("query", "")
    result = cast(
        Route,
        router_chain.invoke(
            {
                "summary": summary,
                "query": query if isinstance(query, str) else str(query),
            }
        ),
    )
    return result.target


def assess_feasibility(state: AgentState) -> AgentState:
    summary = state.get("summary", "").strip() or "요약 없음"
    query = state.get("query", "")
    query_text = query if isinstance(query, str) else str(query)

    next_state: AgentState = {**state}

    # 1) 초경량 휴리스틱: 인사/노이즈는 별도 처리
    query_type = classify_query_type(query_text)
    if query_type == "greeting":
        next_state["intent"] = "greeting"
        next_state.pop("fallback_code", None)
        next_state.pop("fallback_detail", None)
        next_state["risk_level"] = "low"
        next_state.pop("force_hallucination_check", None)
        return next_state

    if query_type == "noise":
        next_state["intent"] = "noise"
        next_state["fallback_code"] = "INSUFFICIENT_DATA"
        next_state["fallback_detail"] = "질문이 조금 모호해요. 플리마켓 셀러 관련해서 더 구체적으로 알려주세요."
        next_state["risk_level"] = "low"
        next_state.pop("force_hallucination_check", None)
        return next_state

    # 2) 정책 키워드 기반 가드레일
    violation, code, detail = detect_policy_violation(query_text)
    if violation and code:
        next_state["intent"] = "safety_violation"
        next_state["fallback_code"] = code
        next_state["fallback_detail"] = detail
        next_state["risk_level"] = "high"
        next_state["force_hallucination_check"] = True
        return next_state

    # 3) LLM 기반 feasibility 평가
    decision = cast(
        FeasibilityDecision,
        feasibility_chain.invoke({"summary": summary, "query": query_text}),
    )
    next_state["risk_level"] = decision.risk_level
    if decision.risk_level == "high":
        next_state["force_hallucination_check"] = True
    if decision.code != "OK":
        next_state["fallback_code"] = decision.code
        next_state["fallback_detail"] = decision.detail
    else:
        next_state.pop("fallback_code", None)
        next_state.pop("fallback_detail", None)
    return next_state


def case_classification(state: AgentState) -> AgentState:
    summary = state.get("summary", "").strip() or "요약 없음"
    query = _get_query_for_reasoning(state)
    result = cast(
        CaseClassification,
        case_classification_chain.invoke({"summary": summary, "query": query}),
    )
    return {**state, "case": result.case, "paraphrased_query": result.rewritten_query}


def plan_structured_search(state: AgentState) -> AgentState:
    summary = state.get("summary", "").strip() or "요약 없음"
    query = _get_query_for_reasoning(state)
    plan = cast(
        StructuredRetrievalPlan,
        seller_structured_plan_chain.invoke({"summary": summary, "query": query}),
    )
    next_state: AgentState = {
        **state,
        "structured_plan": plan.model_dump(),
        "entity_target": plan.target_entity,
    }
    if plan.risk_level == "high":
        next_state["risk_level"] = "high"
        next_state["force_hallucination_check"] = True
    return next_state


def generate(state: AgentState) -> AgentState:
    context_docs = state.get("context", []) or []
    summary = state.get("summary", "").strip() or "요약 없음"
    question = _get_query_for_search(state)
    guidance_parts: List[str] = []
    plan_label = state.get("structured_plan_result")
    if plan_label == "no_match":
        guidance_parts.append("조건에 완전히 맞는 존이 없으면 그 사실을 밝히고 유사 존만 제안하세요.")
    elif plan_label == "broadened":
        guidance_parts.append("정확 일치가 아니므로 근접 존임을 명시하세요.")
    analysis_notes = state.get("analysis_notes")
    if analysis_notes:
        guidance_parts.append(f"데이터 인사이트: {analysis_notes}")
    policy_notes = state.get("policy_notes")
    if policy_notes:
        guidance_parts.append(f"운영 주의사항: {policy_notes}")
    if guidance_parts:
        question = f"{question}\n\n[추가 지시]\n" + "\n".join(guidance_parts)
    response = rag_chain.invoke(
        {
            "summary": summary,
            "question": question,
            "context": _format_context(context_docs),
        }
    )
    answer_text = response.content if isinstance(response.content, str) else str(response.content)
    answer_text = clean_answer_text(answer_text)
    return {**state, "answer": answer_text}


def check_hallucination(state: AgentState) -> AgentState:
    docs = state.get("context", []) or []
    answer_text = _get_answer_text(state)
    force_check = bool(state.get("force_hallucination_check"))
    if not force_check and (len(docs) == 0 or len(answer_text) < 200):
        return {**state, "hallucination_label": "not hallucinated", "hallucination_reason": "skipped_check_short_or_no_docs"}

    formatted_docs = _format_context(docs)
    result = cast(
        Hallucination,
        hallucination_chain.invoke({"student_answer": answer_text, "documents": formatted_docs}),
    )
    return {**state, "hallucination_label": result.label, "hallucination_reason": result.reason}


def hallucination_router(state: AgentState) -> Literal["hallucinated", "not hallucinated"]:
    """할루시네이션 검사 결과 라우팅 (무한 루프 방지: 최대 2회 재작성)."""
    label = state.get("hallucination_label", "not hallucinated")
    if label == "hallucinated":
        rewrite_count = state.get("rewrite_count", 0)
        if rewrite_count >= 2:
            # 무한 루프 방지: 2회 이상 재작성 시도 시 강제 종료
            return "not hallucinated"
        return "hallucinated"
    return "not hallucinated"  # type: ignore[return-value]


def rewrite(state: AgentState) -> AgentState:
    summary = state.get("summary", "").strip() or "요약 없음"
    query = _get_query_for_reasoning(state)
    new_query = rewrite_chain.invoke(
        {
            "summary": summary,
            "query": query,
            "hallucination_label": state.get("hallucination_label", "not hallucinated"),
            "hallucination_reason": state.get("hallucination_reason", ""),
        }
    )
    # rewrite 횟수 증가 (무한 루프 방지)
    rewrite_count = state.get("rewrite_count", 0) + 1
    return {**state, "query": new_query, "paraphrased_query": new_query, "rewrite_count": rewrite_count}


def basic_generate(state: AgentState) -> AgentState:
    """
    셀러용 기본 응답 생성기.

    - OUT_OF_SCOPE_REGION / POLICY_RESTRICTED: 정책/범위 안내만 반환
    - INSUFFICIENT_DATA / NOT_IMPLEMENTED: 안내 문구 + LLM 기반 후속 제안 결합
    - 나머지: LLM 기반 간단 응답
    """

    fallback_code = state.get("fallback_code")
    detail = state.get("fallback_detail")

    # 강한 거절: 범위 밖/정책 위반
    if fallback_code in {"OUT_OF_SCOPE_REGION", "POLICY_RESTRICTED"}:
        answer_text = render_fallback_message(fallback_code, detail)
        return {**state, "answer": answer_text}

    summary = state.get("summary", "").strip() or "요약 없음"
    query = state.get("query", "")
    prefix = ""
    if fallback_code in {"INSUFFICIENT_DATA", "NOT_IMPLEMENTED"}:
        prefix = render_fallback_message(fallback_code, detail)

    answer = basic_chain.invoke({"summary": summary, "query": query})
    if isinstance(answer, str):
        answer_text = answer
    elif hasattr(answer, "content"):
        content = getattr(answer, "content")
        answer_text = content if isinstance(content, str) else str(content)
    else:
        answer_text = str(answer)
    answer_text = clean_answer_text(answer_text)

    if prefix:
        answer_text = f"{prefix}\n\n{answer_text}"

    return {**state, "answer": answer_text}


def format_answer_message(state: AgentState) -> AgentState:
    answer = state.get("answer")
    if not answer:
        return state
    answer_text = answer if isinstance(answer, str) else str(answer)
    return {**state, "messages": [AIMessage(content=answer_text)]}


def summarize_messages(state: AgentState) -> AgentState:
    messages = state.get("messages", [])
    if not messages:
        return state

    if not settings.summary_enabled:
        return state

    # Only summarize once the conversation exceeds the truncation window.
    if len(messages) <= settings.zone_max_message_history:
        return state

    if settings.summary_min_answer_chars > 0:
        answer = state.get("answer", "")
        answer_text = answer if isinstance(answer, str) else str(answer)
        if len(answer_text) < settings.summary_min_answer_chars:
            return state

    summary = state.get("summary", "")
    recent_messages = messages[-settings.zone_max_message_history :]
    prompt = (
        "summarize this chat history below"
        if not summary
        else "summarize this chat history while incorporating the previous summary"
    )
    summary_text = summary_llm.invoke(
        f"{prompt}\n\nchat_history:\n{format_messages(recent_messages)}\n\nsummary:{summary}"
    )
    new_summary = summary_text.content if isinstance(summary_text.content, str) else str(summary_text.content)
    return {**state, "summary": new_summary}


def truncate_messages(state: AgentState) -> dict:
    messages = state.get("messages", [])
    if len(messages) <= settings.zone_max_message_history:
        return {}
    delete_targets: List[RemoveMessage] = []
    for message in messages[:-settings.zone_max_message_history]:
        message_id = getattr(message, "id", None)
        if message_id:
            delete_targets.append(RemoveMessage(id=message_id))
    if not delete_targets:
        return {}
    return {"messages": delete_targets}


# ---------------------------------------------------------------------------
# Async graph nodes (seller flow)
# ---------------------------------------------------------------------------


async def router_async(state: AgentState) -> Literal["rag_answer", "general_answer"]:
    intent = state.get("intent")
    if intent in {"greeting", "bot_about", "chitchat", "noise", "safety_violation"}:
        return "general_answer"

    if state.get("fallback_code"):
        return "general_answer"
    summary = state.get("summary", "").strip() or "요약 없음"
    query = state.get("query", "")
    query_text = query if isinstance(query, str) else str(query)
    result = cast(
        Route,
        await router_chain.ainvoke(
            {
                "summary": summary,
                "query": query_text,
            }
        ),
    )
    return result.target


async def assess_feasibility_async(state: AgentState) -> AgentState:
    summary = state.get("summary", "").strip() or "요약 없음"
    query = state.get("query", "")
    query_text = query if isinstance(query, str) else str(query)

    next_state: AgentState = {**state}

    # 1) 인사/노이즈 휴리스틱
    query_type = classify_query_type(query_text)
    if query_type == "greeting":
        next_state["intent"] = "greeting"
        next_state.pop("fallback_code", None)
        next_state.pop("fallback_detail", None)
        next_state["risk_level"] = "low"
        next_state.pop("force_hallucination_check", None)
        return next_state

    if query_type == "noise":
        next_state["intent"] = "noise"
        next_state["fallback_code"] = "INSUFFICIENT_DATA"
        next_state["fallback_detail"] = "질문이 조금 모호해요. 플리마켓 셀러 관련해서 더 구체적으로 알려주세요."
        next_state["risk_level"] = "low"
        next_state.pop("force_hallucination_check", None)
        return next_state

    # 2) 정책 키워드 기반 가드레일
    violation, code, detail = detect_policy_violation(query_text)
    if violation and code:
        next_state["intent"] = "safety_violation"
        next_state["fallback_code"] = code
        next_state["fallback_detail"] = detail
        next_state["risk_level"] = "high"
        next_state["force_hallucination_check"] = True
        return next_state

    # 3) LLM 기반 feasibility 평가
    decision = cast(
        FeasibilityDecision,
        await feasibility_chain.ainvoke({"summary": summary, "query": query_text}),
    )
    next_state["risk_level"] = decision.risk_level
    if decision.risk_level == "high":
        next_state["force_hallucination_check"] = True
    if decision.code != "OK":
        next_state["fallback_code"] = decision.code
        next_state["fallback_detail"] = decision.detail
    else:
        next_state.pop("fallback_code", None)
        next_state.pop("fallback_detail", None)
    return next_state


async def case_classification_async(state: AgentState) -> AgentState:
    summary = state.get("summary", "").strip() or "요약 없음"
    query = _get_query_for_reasoning(state)
    result = cast(
        CaseClassification,
        await case_classification_chain.ainvoke({"summary": summary, "query": query}),
    )
    return {**state, "case": result.case, "paraphrased_query": result.rewritten_query}


async def plan_structured_search_async(state: AgentState) -> AgentState:
    summary = state.get("summary", "").strip() or "요약 없음"
    query = _get_query_for_reasoning(state)
    plan = cast(
        StructuredRetrievalPlan,
        await seller_structured_plan_chain.ainvoke({"summary": summary, "query": query}),
    )
    next_state: AgentState = {
        **state,
        "structured_plan": plan.model_dump(),
        "entity_target": plan.target_entity,
    }
    if plan.risk_level == "high":
        next_state["risk_level"] = "high"
        next_state["force_hallucination_check"] = True
    return next_state


async def check_allowed_categories_async(state: AgentState) -> AgentState:
    docs = state.get("context", []) or []
    if not docs:
        return state
    query = _get_query_for_reasoning(state)
    assessment = cast(
        AllowedCategoryRequest,
        await allowed_category_chain.ainvoke({"query": query}),
    )
    requested = [item for item in assessment.requested_items if item]
    warnings: List[str] = []
    for doc in docs:
        metadata = doc.metadata or {}
        allowed = [str(x) for x in metadata.get("allowed_categories") or []]
        if requested and allowed:
            conflicts = [
                item for item in requested if not _matches_category(allowed, item)
            ]
            if conflicts:
                zone_name = metadata.get("zone_name", "해당 존")
                warnings.append(
                    f"{zone_name}: {', '.join(conflicts)} 판매는 허용 범위인지 주최 측 확인 필요"
                )
        elif requested and not allowed:
            zone_name = metadata.get("zone_name", "해당 존")
            warnings.append(f"{zone_name}: 허용 업종 정보가 없어 주최 측 확인이 필요합니다.")
    if assessment.risky_items:
        warnings.append(f"추가 확인 필요 품목: {', '.join(assessment.risky_items)}")
    if warnings:
        return {**state, "policy_notes": " / ".join(warnings)}
    next_state: AgentState = dict(state)
    next_state.pop("policy_notes", None)
    return next_state


async def generate_async(state: AgentState) -> AgentState:
    context_docs = state.get("context", []) or []
    summary = state.get("summary", "").strip() or "요약 없음"
    question = _get_query_for_search(state)
    guidance_parts: List[str] = []
    plan_label = state.get("structured_plan_result")
    if plan_label == "no_match":
        guidance_parts.append("조건에 완전히 맞는 존이 없으면 그 사실을 밝히고 유사 존만 제안하세요.")
    elif plan_label == "broadened":
        guidance_parts.append("정확 일치가 아니므로 근접 존임을 명시하세요.")
    analysis_notes = state.get("analysis_notes")
    if analysis_notes:
        guidance_parts.append(f"데이터 인사이트: {analysis_notes}")
    policy_notes = state.get("policy_notes")
    if policy_notes:
        guidance_parts.append(f"운영 주의사항: {policy_notes}")
    if guidance_parts:
        question = f"{question}\n\n[추가 지시]\n" + "\n".join(guidance_parts)
    response = await rag_chain.ainvoke(
        {
            "summary": summary,
            "question": question,
            "context": _format_context(context_docs),
        }
    )
    answer_text = response.content if isinstance(response.content, str) else str(response.content)
    answer_text = clean_answer_text(answer_text)
    return {**state, "answer": answer_text}


async def check_hallucination_async(state: AgentState) -> AgentState:
    docs = state.get("context", []) or []
    answer_text = _get_answer_text(state)
    force_check = bool(state.get("force_hallucination_check"))
    if not force_check and (len(docs) == 0 or len(answer_text) < 200):
        return {**state, "hallucination_label": "not hallucinated", "hallucination_reason": "skipped_check_short_or_no_docs"}

    formatted_docs = _format_context(docs)
    result = cast(
        Hallucination,
        await hallucination_chain.ainvoke(
            {"student_answer": answer_text, "documents": formatted_docs}
        ),
    )
    return {**state, "hallucination_label": result.label, "hallucination_reason": result.reason}


async def rewrite_async(state: AgentState) -> AgentState:
    summary = state.get("summary", "").strip() or "요약 없음"
    query = _get_query_for_reasoning(state)
    new_query = await rewrite_chain.ainvoke(
        {
            "summary": summary,
            "query": query,
            "hallucination_label": state.get("hallucination_label", "not hallucinated"),
            "hallucination_reason": state.get("hallucination_reason", ""),
        }
    )
    # rewrite 횟수 증가 (무한 루프 방지)
    rewrite_count = state.get("rewrite_count", 0) + 1
    return {**state, "query": new_query, "paraphrased_query": new_query, "rewrite_count": rewrite_count}


async def basic_generate_async(state: AgentState) -> AgentState:
    """
    basic_generate의 Async 버전.

    동기 버전과 동일한 정책을 따르되 LLM 호출만 await 한다.
    """

    fallback_code = state.get("fallback_code")
    detail = state.get("fallback_detail")

    if fallback_code in {"OUT_OF_SCOPE_REGION", "POLICY_RESTRICTED"}:
        answer_text = render_fallback_message(fallback_code, detail)
        return {**state, "answer": answer_text}

    summary = state.get("summary", "").strip() or "요약 없음"
    query = state.get("query", "")
    prefix = ""
    if fallback_code in {"INSUFFICIENT_DATA", "NOT_IMPLEMENTED"}:
        prefix = render_fallback_message(fallback_code, detail)

    answer = await basic_chain.ainvoke({"summary": summary, "query": query})
    if isinstance(answer, str):
        answer_text = answer
    elif hasattr(answer, "content"):
        content = getattr(answer, "content")
        answer_text = content if isinstance(content, str) else str(content)
    else:
        answer_text = str(answer)
    answer_text = clean_answer_text(answer_text)

    if prefix:
        answer_text = f"{prefix}\n\n{answer_text}"

    return {**state, "answer": answer_text}


async def summarize_messages_async(state: AgentState) -> AgentState:
    messages = state.get("messages", [])
    if not messages:
        return state

    if not settings.summary_enabled:
        return state

    if len(messages) <= settings.zone_max_message_history:
        return state

    if settings.summary_min_answer_chars > 0:
        answer = state.get("answer", "")
        answer_text = answer if isinstance(answer, str) else str(answer)
        if len(answer_text) < settings.summary_min_answer_chars:
            return state

    summary = state.get("summary", "")
    recent_messages = messages[-settings.zone_max_message_history :]
    prompt = (
        "summarize this chat history below"
        if not summary
        else "summarize this chat history while incorporating the previous summary"
    )
    summary_text = await summary_llm.ainvoke(
        f"{prompt}\n\nchat_history:\n{format_messages(recent_messages)}\n\nsummary:{summary}"
    )
    new_summary = summary_text.content if isinstance(summary_text.content, str) else str(summary_text.content)
    return {**state, "summary": new_summary}


# ---------------------------------------------------------------------------
# 통합 분류 노드: full_classify_seller_async
# ---------------------------------------------------------------------------

async def full_classify_seller_async(state: AgentState) -> AgentState:
    """
    완전 통합 노드: classify_intent + assess_feasibility + case_classification + plan_structured_search를 단일 LLM 호출로 처리.
    
    LLM 호출 4회 → 1회로 감소 (~15초 → ~5초 예상)
    """
    summary = state.get("summary", "").strip() or "요약 없음"
    query = state.get("query", "")
    query_text = query if isinstance(query, str) else ""

    next_state: AgentState = {**state}

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
        FullSellerClassification,
        await full_seller_classification_chain.ainvoke({"summary": summary, "query": query_text}),
    )

    next_state["intent"] = result.intent
    next_state["risk_level"] = result.risk_level

    # Feasibility 처리
    if result.feasibility_code != "OK":
        next_state["fallback_code"] = result.feasibility_code
        next_state["fallback_detail"] = result.detail

    # seller_query인 경우 Case/Plan 정보도 설정
    if result.intent == "seller_query" and result.feasibility_code == "OK":
        next_state["case"] = result.case
        next_state["paraphrased_query"] = result.rewritten_query or query_text
        
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


def full_seller_router(state: AgentState) -> Literal["rag_answer", "general_answer"]:
    """full_classify_seller_async 결과를 기반으로 라우팅."""
    fallback_code = state.get("fallback_code")
    if fallback_code:
        return "general_answer"
    
    intent = state.get("intent", "")
    if intent == "seller_query":
        return "rag_answer"
    
    return "general_answer"


__all__ = [
    "AgentState",
    "extract_user_query",
    "assess_feasibility",
    "assess_feasibility_async",
    "router",
    "router_async",
    "case_classification",
    "case_classification_async",
    "plan_structured_search",
    "plan_structured_search_async",
    "schedule_seller_tool",
    "schedule_seller_tool_async",
    "seller_tool_router",
    "seller_tool_followup_router",
    "consume_seller_tool_result",
    "analyze_zone_performance",
    "check_allowed_categories",
    "check_allowed_categories_async",
    "generate",
    "generate_async",
    "check_hallucination",
    "check_hallucination_async",
    "rewrite",
    "rewrite_async",
    "basic_generate",
    "basic_generate_async",
    "format_answer_message",
    "summarize_messages",
    "summarize_messages_async",
    "truncate_messages",
    "classify_intent_node",
    "hallucination_router",
    # 통합 노드
    "full_classify_seller_async",
    "full_seller_router",
    "FullSellerClassification",
]


