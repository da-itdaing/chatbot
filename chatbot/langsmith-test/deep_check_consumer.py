#!/usr/bin/env python
"""
Consumer 케이스 깊은 품질 체크 스크립트.

- input이 실제 사용자 발화로 읽히는지
- mode/case_type/expected_behavior가 일관성 있는지
- 멀티턴이 필요한 케이스를 단일턴으로 무리하게 압축하지 않았는지
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, List

INPUT_PATH = Path(__file__).resolve().parent / "input" / "test_prompts.json"


def load_prompts() -> List[Dict[str, Any]]:
    """test_prompts.json 로드."""
    with INPUT_PATH.open("r", encoding="utf-8") as f:
        data = json.load(f)
    return data.get("prompts", [])


def analyze_consumer_quality(prompts: List[Dict[str, Any]]) -> None:
    """consumer 케이스를 여러 기준으로 품질 체크."""
    
    consumer_cases = [p for p in prompts if p.get("mode") == "consumer"]
    print(f"Total consumer cases: {len(consumer_cases)}\n")
    
    # 1. 메타 설명형 input (설명문처럼 읽히는 케이스)
    meta_desc = []
    for p in consumer_cases:
        inp = str(p.get("input", ""))
        # "**", ":", "\"  같은 패턴이 있으면 설명문 가능성
        if any(marker in inp for marker in ["**", ": \"", "예:", "→"]):
            meta_desc.append({
                "id": p["id"],
                "case_type": p.get("case_type"),
                "input": inp[:100] + ("..." if len(inp) > 100 else "")
            })
    
    print(f"[1] Meta-descriptive inputs (still contain explanation markers):")
    if meta_desc:
        for item in meta_desc[:10]:  # 최대 10개만 샘플
            print(f"  - id={item['id']} type={item['case_type']}")
            print(f"    input: {item['input']}")
    else:
        print("  (None found - good!)")
    print()
    
    # 2. 본질적으로 멀티턴인 케이스인데 단일턴으로 압축된 것 (추정)
    suspected_multi = []
    multi_keywords = ["지난번", "아까", "방금", "전에", "다시", "또", "어제", "이전에", "앞서"]
    for p in consumer_cases:
        if p.get("turn_type") != "single":
            continue
        inp = str(p.get("input", ""))
        if any(kw in inp for kw in multi_keywords):
            suspected_multi.append({
                "id": p["id"],
                "case_type": p.get("case_type"),
                "expected_behavior": p.get("expected_behavior"),
                "input": inp[:100] + ("..." if len(inp) > 100 else "")
            })
    
    print(f"[2] Single-turn cases with multi-turn context markers (consider converting to multi-turn):")
    print(f"    Total: {len(suspected_multi)}")
    if suspected_multi:
        for item in suspected_multi[:15]:  # 최대 15개 샘플
            print(f"  - id={item['id']} type={item['case_type']} expected={item['expected_behavior']}")
            print(f"    input: {item['input']}")
    print()
    
    # 3. retrieval_recommendation 인데 조건이 너무 추상적/모호해서 seed 기준 추천이 어려운 케이스
    vague_retrieval = []
    for p in consumer_cases:
        if p.get("case_type") != "retrieval_recommendation":
            continue
        inp = str(p.get("input", "")).lower()
        # 지역/카테고리/조건이 거의 없고, "그냥", "아무", "적당한" 같은 단어만 있는 경우
        if any(vague_word in inp for vague_word in ["아무", "그냥", "대충", "적당", "알아서"]):
            # 지역명(광주/동구/서구/남구/북구/광산구)이 전혀 없으면 더 문제
            if not any(district in inp for district in ["광주", "동구", "서구", "남구", "북구", "광산구"]):
                vague_retrieval.append({
                    "id": p["id"],
                    "input": p["input"][:100] + ("..." if len(p["input"]) > 100 else "")
                })
    
    print(f"[3] retrieval_recommendation with very vague/abstract queries (may need refinement or reassignment to edge_robustness):")
    print(f"    Total: {len(vague_retrieval)}")
    if vague_retrieval:
        for item in vague_retrieval[:10]:
            print(f"  - id={item['id']}")
            print(f"    input: {item['input']}")
    print()
    
    # 4. case_type과 expected_behavior가 불일치 (likely labeling mistake)
    mismatch = []
    for p in consumer_cases:
        case_type = p.get("case_type", "")
        expected = p.get("expected_behavior", "")
        
        # retrieval_recommendation인데 must_refuse/must_resist 같은 expected_behavior
        if case_type == "retrieval_recommendation" and any(
            refuse_kw in expected for refuse_kw in ["refuse", "resist", "reject", "enforce", "detect"]
        ):
            mismatch.append({
                "id": p["id"],
                "case_type": case_type,
                "expected_behavior": expected,
                "reason": "retrieval case with refuse/resist behavior"
            })
        
        # guardrail_safety/prompt_injection 인데 must_recommend
        if case_type in ["guardrail_safety", "prompt_injection", "policy_bypass"] and "recommend" in expected:
            mismatch.append({
                "id": p["id"],
                "case_type": case_type,
                "expected_behavior": expected,
                "reason": "guardrail/injection case with recommend behavior"
            })
    
    print(f"[4] case_type vs expected_behavior mismatches (potential labeling errors):")
    print(f"    Total: {len(mismatch)}")
    if mismatch:
        for item in mismatch[:10]:
            print(f"  - id={item['id']} type={item['case_type']} expected={item['expected_behavior']}")
            print(f"    reason: {item['reason']}")
    print()
    
    # 5. input이 거의 비어 있거나 너무 짧은 케이스 (품질 의심)
    empty_or_short = []
    for p in consumer_cases:
        inp = str(p.get("input", "")).strip()
        if not inp or len(inp) < 5:
            empty_or_short.append({
                "id": p["id"],
                "input": inp or "(empty)",
                "case_type": p.get("case_type")
            })
    
    print(f"[5] Consumer cases with empty or very short input (< 5 chars):")
    print(f"    Total: {len(empty_or_short)}")
    if empty_or_short:
        for item in empty_or_short:
            print(f"  - id={item['id']} type={item['case_type']} input='{item['input']}'")
    print()
    
    # 6. input이 특정 형태의 플레이스홀더/설명문 (예: "비속어 포함 문장", "판매자 개인정보 요청")
    placeholder_inputs = []
    for p in consumer_cases:
        inp = str(p.get("input", ""))
        # 너무 추상적이거나 실제 구체 발화가 없는 형태
        placeholders = ["포함 문장", "개인정보 요청", "금지 문장", "설명문", "예시", "메타"]
        if any(ph in inp for ph in placeholders) and len(inp) < 25:
            placeholder_inputs.append({
                "id": p["id"],
                "input": inp
            })
    
    print(f"[6] Placeholder-like inputs (need concrete user utterances):")
    print(f"    Total: {len(placeholder_inputs)}")
    if placeholder_inputs:
        for item in placeholder_inputs:
            print(f"  - id={item['id']} input='{item['input']}'")
    print()
    
    # 7. retrieval_recommendation 케이스 중 constraints가 정교하지 않은 것 (선택 체크)
    simple_constraints = []
    for p in consumer_cases:
        if p.get("case_type") != "retrieval_recommendation":
            continue
        constraints = p.get("constraints", {})
        # constraints가 아주 단순하게 table만 명시돼 있거나, where가 너무 일반적이면 나중에 구체화 필요
        if not constraints or (len(constraints) == 1 and "table" in constraints):
            simple_constraints.append({
                "id": p["id"],
                "section": p.get("section"),
                "constraints": constraints
            })
    
    print(f"[7] retrieval_recommendation cases with minimal constraints (may benefit from adding filters):")
    print(f"    Total: {len(simple_constraints)}")
    if simple_constraints:
        for item in simple_constraints[:5]:
            print(f"  - id={item['id']} section={item['section']} constraints={item['constraints']}")
    print()
    
    # 8. expected_behavior가 너무 포괄적인 경우
    generic_expected = []
    for p in consumer_cases:
        expected = p.get("expected_behavior", "")
        # "must_request_clarification" 같이 너무 일반적인 behavior는 세분화 필요
        if expected in ["must_request_clarification", "must_handle"]:
            generic_expected.append({
                "id": p["id"],
                "case_type": p.get("case_type"),
                "expected_behavior": expected,
                "input": p["input"][:80] + ("..." if len(p["input"]) > 80 else "")
            })
    
    print(f"[8] Cases with overly generic expected_behavior (consider refining):")
    print(f"    Total: {len(generic_expected)}")
    if generic_expected:
        for item in generic_expected[:10]:
            print(f"  - id={item['id']} type={item['case_type']} expected={item['expected_behavior']}")
            print(f"    input: {item['input']}")
    print()


def main() -> None:
    prompts = load_prompts()
    analyze_consumer_quality(prompts)
    print("Deep quality check complete.")


if __name__ == "__main__":
    main()

