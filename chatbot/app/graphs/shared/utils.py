from __future__ import annotations

import re
from typing import Any, Iterable, List, Literal, Sequence, Tuple

from langchain_core.documents import Document
from langchain_core.messages import BaseMessage

from app.utils.search import WebSearchClient


def latest_user_message(messages: Sequence[Any]) -> Any:
    """
    Return the most recent user/human message.

    LangGraph MessagesState may contain LangChain BaseMessage objects or
    plain dicts. We iterate in reverse and fall back to the last message
    if no explicit user message exists.
    """

    if not messages:
        raise ValueError("messages list is empty")

    for message in reversed(messages):
        if isinstance(message, BaseMessage):
            role = getattr(message, "type", None) or getattr(message, "role", None)
            if role in {"human", "user"}:
                return message
        elif isinstance(message, dict) and message.get("role") == "user":
            return message
    return messages[-1]


def format_messages(messages: Sequence[Any]) -> str:
    """
    Convert LangGraph messages into a compact textual transcript for prompts.
    """

    formatted: List[str] = []
    for idx, message in enumerate(messages, start=1):
        role = "unknown"
        content: Any
        if isinstance(message, BaseMessage):
            role = getattr(message, "type", None) or getattr(message, "role", None) or message.__class__.__name__
            content = message.content
        elif isinstance(message, dict):
            role = message.get("role", "unknown")
            content = message.get("content", "")
        else:
            content = message
        text = content if isinstance(content, str) else str(content)
        formatted.append(f"[{idx}:{role}] {text.strip()}")
    return "\n".join(formatted)


def _normalize_web_documents(documents: Iterable[Document], *, max_docs: int) -> List[Document]:
    normalized: List[Document] = []
    for doc in documents:
        metadata = dict(doc.metadata or {})
        metadata.setdefault("source", "web_search")
        normalized.append(Document(page_content=doc.page_content, metadata=metadata))
        if len(normalized) >= max_docs:
            break
    return normalized


def extend_with_web_results(
    docs: Sequence[Document],
    query: str,
    web_client: WebSearchClient,
    *,
    max_web_docs: int = 2,
) -> List[Document]:
    """
    Append DuckDuckGo (or configured provider) results to the PGVector hits.

    The LangChain PGVector retriever is synchronous today, so this helper
    keeps the function blocking and relies on WebSearchClient for network I/O.
    """

    combined = list(docs)
    if not query.strip() or not web_client.enabled:
        return combined
    web_docs = web_client.search_sync(query.strip())
    combined.extend(_normalize_web_documents(web_docs, max_docs=max_web_docs))
    return combined


async def extend_with_web_results_async(
    docs: Sequence[Document],
    query: str,
    web_client: WebSearchClient,
    *,
    max_web_docs: int = 2,
) -> List[Document]:
    """
    Async counterpart for LangGraph async graphs.
    """

    combined = list(docs)
    if not query.strip() or not web_client.enabled:
        return combined
    web_docs = await web_client.search_async(query.strip())
    combined.extend(_normalize_web_documents(web_docs, max_docs=max_web_docs))
    return combined


def render_fallback_message(code: str, detail: str | None = None) -> str:
    """
    Map feasibility codes to user-facing Korean guidance.
    
    Note: detail은 내부 로깅용으로만 사용하며, 사용자에게는 노출하지 않는다.
    """

    # detail은 내부 분류 정보이므로 사용자에게 노출하지 않음
    # 톤: 친근하고 공감하며, 자연스럽게 서비스 범위로 유도
    templates = {
        # 서비스 범위
        "OUT_OF_SCOPE_REGION": "아, 저는 광주광역시 전문이라 다른 지역은 잘 몰라요 😅 혹시 광주광역시 쪽에서 찾으시는 마켓이 있으면 도와드릴게요!",
        "OUT_OF_SCOPE_TOPIC": "음, 그건 제 전문 분야가 아니라서요 🤔 대신 광주 플리마켓이나 팝업 정보가 필요하시면 언제든 물어봐 주세요!",
        "NOT_IMPLEMENTED": "앗, 아직 그 기능은 준비 중이에요! 조금 더 간단하게 '북구 플리마켓 추천해줘' 이런 식으로 물어봐 주시면 잘 도와드릴 수 있어요 😊",
        "INSUFFICIENT_DATA": "음, 조건이 좀 까다로워서 딱 맞는 곳을 찾기 어렵네요. 조건을 조금 바꿔보시거나, 제가 비슷한 분위기의 마켓을 추천해드릴까요?",
        "POLICY_RESTRICTED": "그 부분은 정확한 답변 드리기 어려워서, 전문가분께 상담받으시는 게 좋을 것 같아요. 플리마켓 관련해서는 편하게 물어봐 주세요!",
        # Content Safety (S1-S12) - 공감하되 단호하게
        "S1_VIOLENCE": "그런 내용은 답변드리기 어려워요. 혹시 스트레스 푸실 곳이 필요하시면, 분위기 좋은 플리마켓 추천해드릴까요? 🌿",
        "S3_CRIMINAL": "그건 제가 도와드릴 수 없는 부분이에요. 대신 재밌는 플리마켓 구경은 어떠세요?",
        "S4_WEAPONS": "그런 정보는 제공하지 않아요. 핸드메이드 소품이나 빈티지 아이템 구경은 어떠세요?",
        "S5_SUBSTANCES": "그 부분은 답변드리기 어려워요. 맛있는 먹거리가 있는 야시장 정보는 관심 있으신가요?",
        "S6_SELF_HARM": "많이 힘드시죠 💙 혼자 감당하지 마시고, 자살예방상담전화 1393으로 연락해보세요. 24시간 상담 가능해요. 언제든 이야기 들어줄 사람이 있어요.",
        "S8_HATE": "모두가 편하게 이용할 수 있도록, 그런 내용은 답변드리지 않아요. 다른 질문 있으시면 편하게 말씀해주세요!",
        "S9_PII": "다른 분의 개인정보는 알려드리기 어려워요. 대신 마켓 위치나 운영시간 같은 건 바로 안내해드릴게요!",
        "S10_HARASSMENT": "그런 내용은 도와드리기 어려워요. 다른 질문 있으시면 편하게 물어봐 주세요.",
        "S11_THREAT": "그런 내용은 답변드릴 수 없어요. 안전하고 즐거운 마켓 정보가 필요하시면 말씀해주세요!",
        # Jailbreak - 가볍게 거절
        "JAILBREAK": "에이, 그런 건 안 돼요~ 😄 저는 광주 플리마켓 전문 챗봇이에요. 이번 주말 가볼 만한 마켓 추천해드릴까요?",
    }
    return templates.get(code, "일시적인 오류가 발생했어요. 잠시 후 다시 시도해 주세요.")


def classify_query_type(
    query: str,
) -> Literal["greeting", "bot_about", "out_of_scope_region", "out_of_scope_topic", "noise", "normal"]:
    """
    매우 가벼운 휴리스틱으로 질문 유형을 분류한다.

    - greeting: 짧은 인사/감사/헤어짐 인사
    - bot_about: 챗봇/서비스 소개 질문
    - out_of_scope_region: 명확히 서비스 범위 외 지역 (경기도 광주, 서울 등)
    - out_of_scope_topic: 플리마켓과 무관한 주제 (GPU, 주식 등)
    - noise: 의미 없는 한 글자/이모지/기호 위주
    - normal: 그 외 일반 질문 (광주 = 광주광역시로 해석)

    LangGraph/LangChain 호출 전에 빠르게 필터링하기 위한 용도이며,
    LLM 기반 intent 분류기의 보조 신호로 사용한다.
    """

    if not query:
        return "noise"

    text = query.strip()
    if not text:
        return "noise"

    # 아주 짧은 한/두 글자 + 문장부호만 있는 경우는 noise로 본다.
    if len(text) <= 2:
        if all(not ch.isalnum() for ch in text):
            return "noise"

    lowered = text.lower()

    # 대표적인 인사/감사/헤어짐 패턴
    greeting_keywords = (
        "안녕",
        "안녕하세요",
        "하이",
        "hello",
        "hi",
        "헬로",
        "반가워",
        "반가워요",
        "고마워",
        "고마워요",
        "감사",
        "땡큐",
        "수고",
        "수고했어",
        "수고하세요",
        "잘가",
        "bye",
        "goodbye",
    )

    # 이모지/기호 위주의 문자열은 greeting/응답 유도용으로 취급
    emoji_like = ("ㅎㅎ", "ㅋㅋ", "^^", ":)", ":D", "😂", "🤣", "😁", "😊")

    for kw in greeting_keywords + emoji_like:
        if kw in lowered or kw in text:
            return "greeting"

    # 서비스 소개 질문 패턴 (bot_about)
    bot_about_patterns = (
        "뭐하는 봇",
        "뭐하는 챗봇",
        "무슨 봇",
        "무슨 챗봇",
        "뭘 할 수 있",
        "뭘 해줄 수 있",
        "어떤 서비스",
        "잇다잉이 뭐",
        "서비스 소개",
        "기능이 뭐",
        "뭘 도와줄 수",
        "어떤 도움",
    )
    for pattern in bot_about_patterns:
        if pattern in text:
            return "bot_about"

    # === 경기도 광주 명확히 구분 ===
    # "경기도 광주", "경기 광주", "경기광주" 등 명확한 경우만 out_of_scope
    gyeonggi_gwangju_patterns = (
        "경기도 광주", "경기 광주", "경기광주",
        "광주시 경기", "광주시 성남", "광주시 이천",
        "광주시 하남", "광주시 용인",  # 경기도 광주시 인접 지역
    )
    for pattern in gyeonggi_gwangju_patterns:
        if pattern in text:
            return "out_of_scope_region"
    
    # 경기도 언급 + 광주 → 경기도 광주시로 해석
    if "경기" in text and "광주" in text:
        return "out_of_scope_region"

    # 타 지역 (광주가 아닌 도시명이 포함되고 플리마켓 관련 키워드가 있을 때)
    other_cities = ("서울", "부산", "대구", "인천", "대전", "울산", "제주", "수원", "성남", "용인")
    market_keywords = ("플리마켓", "마켓", "팝업", "축제", "야시장", "추천")
    
    has_other_city = any(city in text for city in other_cities)
    has_market_kw = any(kw in text for kw in market_keywords)
    has_gwangju = "광주" in text
    
    # 타 지역 + 마켓 키워드 + 광주 없음 → out_of_scope_region
    if has_other_city and has_market_kw and not has_gwangju:
        return "out_of_scope_region"

    # 명확한 범위 외 주제 (기술, 금융 등 - 날씨는 플리마켓 방문에 유용하므로 제외)
    out_of_scope_topics = (
        "rtx", "gpu", "그래픽카드", "cpu", "컴퓨터 가격",
        "주식", "코인", "비트코인", "환율",
        "뉴스", "정치",
    )
    for topic in out_of_scope_topics:
        if topic in lowered:
            return "out_of_scope_topic"
    
    # "광주", "광주시" 등은 모두 광주광역시로 해석 → normal로 처리
    return "normal"


# ============================================================================
# 휴리스틱 가드레일 (빠른 필터링용, 명백한 케이스만)
# 복잡한 판단은 LLM (UnifiedIntentFeasibility)에 위임
# ============================================================================

# Jailbreak 패턴만 휴리스틱으로 처리 (명백한 시스템 공격 시도)
# Content Safety (S1-S12)는 맥락 고려가 필요하므로 LLM이 판단
_JAILBREAK_PATTERNS: Tuple[Tuple[str, str], ...] = (
    # 시스템 프롬프트 노출 시도
    ("시스템프롬프트", "JAILBREAK"),
    ("systemprompt", "JAILBREAK"),
    ("프롬프트출력", "JAILBREAK"),
    ("프롬프트보여", "JAILBREAK"),
    # 규칙/역할 변경 시도
    ("ignoreallprevious", "JAILBREAK"),
    ("ignorepreviousinstructions", "JAILBREAK"),
    ("모든규칙무시", "JAILBREAK"),
    ("규칙무시", "JAILBREAK"),
    ("지시무시", "JAILBREAK"),
    ("역할바꿔", "JAILBREAK"),
    # 디버그/개발자 모드
    ("개발자모드", "JAILBREAK"),
    ("developermode", "JAILBREAK"),
    ("danmode", "JAILBREAK"),
    ("debugmode", "JAILBREAK"),
    ("디버그모드", "JAILBREAK"),
    # 내부 정보 요청
    ("내부구조", "JAILBREAK"),
    ("노드구조", "JAILBREAK"),
    ("db경로", "JAILBREAK"),
    ("환경변수", "JAILBREAK"),
    ("apikey", "JAILBREAK"),
    ("api키", "JAILBREAK"),
)

# 전문가 상담 권고 키워드 (POLICY_RESTRICTED)
_POLICY_KEYWORDS: Tuple[Tuple[str, str], ...] = (
    ("의료", "건강 관련은 의사 상담을 권해드려요."),
    ("처방", "약 처방은 의사 상담을 권해드려요."),
    ("법률", "법률 상담은 전문 변호사에게 문의해주세요."),
    ("소송", "법적 분쟁은 전문가 상담을 권해드려요."),
    ("세무", "세금 상담은 세무사에게 문의해주세요."),
    ("인허가", "인허가는 관할 기관에 확인해주세요."),
)


def detect_policy_violation(query: str) -> Tuple[bool, str | None, str | None]:
    """
    빠른 휴리스틱 가드레일 (명백한 케이스만).
    
    - Jailbreak 패턴: 시스템 프롬프트/역할 변경 시도 차단
    - Policy 키워드: 전문가 상담 권고 안내
    
    Content Safety (S1-S12)는 맥락 고려가 필요하므로 LLM이 판단.

    Returns (violation, code, detail).
    """

    normalized = re.sub(r"\s+", "", (query or "").lower())
    if not normalized:
        return False, None, None
    
    # 1) Jailbreak 패턴 (명백한 시스템 공격 시도)
    for pattern, code in _JAILBREAK_PATTERNS:
        if pattern.lower() in normalized:
            return True, code, ""
    
    # 2) 전문가 상담 권고 (POLICY_RESTRICTED는 LLM이 최종 판단)
    # 여기서는 감지만 하고, 최종 결정은 LLM이 맥락을 보고 판단
    # (예: "세무 관련 마켓 있어?" vs "세무 신고 방법")
    # → 휴리스틱으로는 구분 어려우므로, 이 부분은 LLM에 위임
    
    return False, None, None


_DEBUG_SUBSTRINGS: Tuple[str, ...] = (
    "호출 준비",
    "structured_plan_result",
    "consumer_retrieve_async",
    "seller_retrieve_async",
)


def clean_answer_text(text: str) -> str:
    """
    Strip LangGraph 내부 로그/JSON 라인 등을 제거해 사용자에게 필요한
    자연어 답변만 남긴다.
    """

    if not text:
        return ""

    cleaned_lines: List[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            cleaned_lines.append("")
            continue
        lowered = stripped.lower()
        if any(sub in lowered for sub in _DEBUG_SUBSTRINGS):
            continue
        if stripped.startswith("{") and '"type":' in lowered:
            # ToolMessage JSON 등은 숨긴다.
            continue
        cleaned_lines.append(line.rstrip())

    cleaned = "\n".join(cleaned_lines).strip()
    return cleaned


__all__ = [
    "latest_user_message",
    "format_messages",
    "extend_with_web_results",
    "extend_with_web_results_async",
    "render_fallback_message",
    "classify_query_type",
    "detect_policy_violation",
    "clean_answer_text",
]

