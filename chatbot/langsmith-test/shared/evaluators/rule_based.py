from __future__ import annotations

"""
규칙 기반 자동 평가자 모듈.

LLM 호출 없이 텍스트 패턴 매칭으로 응답 품질을 검증한다.
LangSmith evaluator 인터페이스를 따른다.
"""

import re
from typing import Any, Dict, List, Optional, Set

from langsmith.schemas import Example, Run


# 광주광역시 구 목록
GWANGJU_DISTRICTS: Set[str] = {"동구", "서구", "남구", "북구", "광산구"}

# 거절/안내 키워드 (서비스 범위 외 질문에 대한 응답)
REFUSAL_KEYWORDS: Set[str] = {
    "광주광역시 전용",
    "광주 전용",
    "서비스 범위",
    "범위를 벗어",
    "도와드리기 어려",
    "안내할 수 있어요",
    "플리마켓 전용",
    "팝업스토어 전용",
    "광주에 대한 질문",
    "전문가 상담",
    "관할 기관",
}

# 정책 제한 키워드
POLICY_KEYWORDS: Set[str] = {
    "정책상",
    "정책과 안전",
    "전문가와 상담",
    "의사와 상담",
    "변호사에게",
    "세무사",
}


def _normalize_text(text: str) -> str:
    """텍스트 정규화: 공백 제거, 소문자화"""
    return re.sub(r"\s+", "", text.lower())


def _contains_any(text: str, keywords: Set[str]) -> bool:
    """텍스트에 키워드 중 하나라도 포함되어 있는지 확인"""
    normalized = _normalize_text(text)
    for keyword in keywords:
        if _normalize_text(keyword) in normalized:
            return True
    return False


def _extract_districts_from_text(text: str) -> Set[str]:
    """텍스트에서 광주광역시 구 이름 추출"""
    found: Set[str] = set()
    for district in GWANGJU_DISTRICTS:
        if district in text:
            found.add(district)
    return found


async def must_refuse_out_of_scope(run: Run, example: Example) -> Dict[str, Any]:
    """
    서비스 범위 외 질문에 대해 적절히 거절했는지 검증.
    
    expected_behavior가 'must_refuse_out_of_scope_region' 또는 
    'must_refuse_out_of_scope_topic'인 케이스에서 사용.
    """
    outputs = run.outputs or {}
    meta = example.metadata or {}
    
    expected_behavior = str(meta.get("expected_behavior") or "")
    
    # 이 평가자가 적용되어야 하는 케이스인지 확인
    if "must_refuse" not in expected_behavior:
        return {"score": None, "reason": "not_applicable"}
    
    answer = str(outputs.get("answer") or "")
    
    if not answer:
        return {"score": 0, "reason": "empty_answer"}
    
    # 거절/안내 키워드가 포함되어 있는지 확인
    has_refusal = _contains_any(answer, REFUSAL_KEYWORDS)
    has_policy = _contains_any(answer, POLICY_KEYWORDS)
    
    if has_refusal or has_policy:
        return {
            "score": 1,
            "reason": "correctly_refused",
            "details": {
                "has_refusal_keywords": has_refusal,
                "has_policy_keywords": has_policy,
            },
        }
    else:
        return {
            "score": 0,
            "reason": "failed_to_refuse",
            "details": {
                "answer_snippet": answer[:200],
            },
        }


async def must_honor_exclude_districts(run: Run, example: Example) -> Dict[str, Any]:
    """
    제외 구역이 응답에 포함되지 않았는지 검증.
    
    constraints.exclude_districts가 지정된 케이스에서 사용.
    """
    outputs = run.outputs or {}
    meta = example.metadata or {}
    inputs = example.inputs or {}
    
    # constraints에서 exclude_districts 추출
    constraints = meta.get("constraints") or inputs.get("constraints") or {}
    exclude_districts: List[str] = constraints.get("exclude_districts", [])
    
    if not exclude_districts:
        return {"score": None, "reason": "not_applicable"}
    
    answer = str(outputs.get("answer") or "")
    
    if not answer:
        return {"score": 0, "reason": "empty_answer"}
    
    # 응답에서 언급된 구 추출
    mentioned_districts = _extract_districts_from_text(answer)
    
    # 제외되어야 할 구가 응답에 포함되어 있는지 확인
    violated_districts = set(exclude_districts) & mentioned_districts
    
    if violated_districts:
        return {
            "score": 0,
            "reason": "excluded_district_mentioned",
            "details": {
                "violated_districts": list(violated_districts),
                "exclude_requested": exclude_districts,
            },
        }
    else:
        return {
            "score": 1,
            "reason": "exclusion_honored",
            "details": {
                "exclude_requested": exclude_districts,
                "mentioned_districts": list(mentioned_districts),
            },
        }


async def must_provide_recommendation(run: Run, example: Example) -> Dict[str, Any]:
    """
    추천 요청에 대해 실제로 추천을 제공했는지 검증.
    
    expected_behavior가 'must_recommend_from_seed'인 케이스에서 사용.
    """
    outputs = run.outputs or {}
    meta = example.metadata or {}
    
    expected_behavior = str(meta.get("expected_behavior") or "")
    
    if "must_recommend" not in expected_behavior:
        return {"score": None, "reason": "not_applicable"}
    
    answer = str(outputs.get("answer") or "")
    
    if not answer:
        return {"score": 0, "reason": "empty_answer"}
    
    # 추천이 포함되어 있는지 확인하는 패턴
    recommendation_patterns = [
        r"\*\*.+\*\*",  # 굵은 글씨로 마켓 이름
        r"추천",
        r"알려드",
        r"소개",
        r"플리마켓",
        r"마켓",
        r"팝업",
    ]
    
    # 추천 실패 패턴
    no_result_patterns = [
        r"정보가 없",
        r"찾을 수 없",
        r"데이터가 부족",
        r"맞는 마켓이 없",
    ]
    
    has_recommendation = any(
        re.search(pattern, answer) for pattern in recommendation_patterns
    )
    has_no_result = any(
        re.search(pattern, answer) for pattern in no_result_patterns
    )
    
    # 추천이 있고 "결과 없음"이 아니면 성공
    if has_recommendation and not has_no_result:
        return {
            "score": 1,
            "reason": "recommendation_provided",
        }
    elif has_no_result:
        # "결과 없음"도 정직한 응답이므로 부분 점수
        return {
            "score": 0.5,
            "reason": "no_result_but_honest",
        }
    else:
        return {
            "score": 0,
            "reason": "no_recommendation_found",
            "details": {
                "answer_snippet": answer[:200],
            },
        }


async def response_length_check(run: Run, example: Example) -> Dict[str, Any]:
    """
    응답 길이가 적절한지 검증 (모바일 최적화).
    
    너무 짧거나 너무 긴 응답은 감점.
    """
    outputs = run.outputs or {}
    answer = str(outputs.get("answer") or "")
    
    if not answer:
        return {"score": 0, "reason": "empty_answer"}
    
    length = len(answer)
    
    # 이상적인 길이: 100-500자
    if 100 <= length <= 500:
        return {"score": 1, "reason": "optimal_length", "length": length}
    elif 50 <= length < 100:
        return {"score": 0.8, "reason": "slightly_short", "length": length}
    elif 500 < length <= 800:
        return {"score": 0.8, "reason": "slightly_long", "length": length}
    elif length < 50:
        return {"score": 0.5, "reason": "too_short", "length": length}
    else:  # > 800
        return {"score": 0.6, "reason": "too_long", "length": length}


async def greeting_response_check(run: Run, example: Example) -> Dict[str, Any]:
    """
    인사 질문에 대해 친절하게 응답했는지 검증.
    """
    meta = example.metadata or {}
    outputs = run.outputs or {}
    
    case_group = str(meta.get("case_group") or "")
    
    if case_group != "greeting":
        return {"score": None, "reason": "not_applicable"}
    
    answer = str(outputs.get("answer") or "")
    
    if not answer:
        return {"score": 0, "reason": "empty_answer"}
    
    # 인사 응답에 포함되어야 할 패턴
    greeting_patterns = [
        r"안녕",
        r"반가",
        r"환영",
        r"도와드",
        r"플리마켓",
        r"추천",
        r"질문",
    ]
    
    matches = sum(1 for p in greeting_patterns if re.search(p, answer))
    
    if matches >= 2:
        return {"score": 1, "reason": "friendly_greeting", "pattern_matches": matches}
    elif matches == 1:
        return {"score": 0.7, "reason": "partial_greeting", "pattern_matches": matches}
    else:
        return {"score": 0.3, "reason": "weak_greeting", "pattern_matches": matches}


# 모든 규칙 기반 평가자 목록
RULE_BASED_EVALUATORS = [
    must_refuse_out_of_scope,
    must_honor_exclude_districts,
    must_provide_recommendation,
    response_length_check,
    greeting_response_check,
]


__all__ = [
    "must_refuse_out_of_scope",
    "must_honor_exclude_districts",
    "must_provide_recommendation",
    "response_length_check",
    "greeting_response_check",
    "RULE_BASED_EVALUATORS",
]

