from __future__ import annotations

"""
Quick validator for langsmith-test/input/test_prompts.json.

- 필수 필드/enum 값 검사
- mode별(case_type, expected_behavior 등) 누락 여부 확인
- consumer retrieval 케이스에서 target ids/constraints 통계 출력
"""

import json
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List

INPUT_PATH = Path(__file__).resolve().parent / "input" / "test_prompts.json"


def load_prompts() -> Dict[str, Any]:
    if not INPUT_PATH.exists():
        raise FileNotFoundError(f"test_prompts.json not found: {INPUT_PATH}")
    with INPUT_PATH.open("r", encoding="utf-8") as f:
        return json.load(f)


def validate_required_fields(data: Dict[str, Any]) -> List[str]:
    errors: List[str] = []
    schema = data.get("schema", {})
    required = schema.get("required_fields", [])
    prompts = data.get("prompts", [])
    for idx, p in enumerate(prompts):
        for field in required:
            if field not in p:
                errors.append(f"[MISSING] idx={idx}, id={p.get('id')} missing field '{field}'")
    return errors


def summarize_by_mode_and_case(prompts: List[Dict[str, Any]]) -> None:
    counts = Counter()
    for p in prompts:
        mode = p.get("mode", "unknown")
        case_type = p.get("case_type", "unknown")
        key = f"{mode}/{case_type}"
        counts[key] += 1
    print("=== Counts by mode/case_type ===")
    for key, cnt in sorted(counts.items()):
        print(f"{key}: {cnt}")


def check_consumer_retrieval(prompts: List[Dict[str, Any]]) -> None:
    total = 0
    missing_targets = 0
    table_counter = Counter()

    for p in prompts:
        if p.get("mode") != "consumer":
            continue
        if p.get("case_type") != "retrieval_recommendation":
            continue
        total += 1
        t_ids = p.get("target_market_ids") or []
        if not t_ids:
            missing_targets += 1
        constraints = p.get("constraints") or {}
        table = constraints.get("table") or "unknown"
        table_counter[table] += 1

    print("\n=== Consumer retrieval_recommendation stats ===")
    print(f"total cases: {total}")
    print(f"cases with empty target_market_ids: {missing_targets}")
    print("constraints.table distribution:")
    for table, cnt in sorted(table_counter.items()):
        print(f"  {table}: {cnt}")


def main() -> None:
    print(f"Validating dataset at {INPUT_PATH}")
    data = load_prompts()
    prompts: List[Dict[str, Any]] = data.get("prompts", [])

    errors = validate_required_fields(data)
    if errors:
        print("=== Required field errors ===")
        for e in errors:
            print(e)
    else:
        print("All required fields present.")

    summarize_by_mode_and_case(prompts)
    check_consumer_retrieval(prompts)


if __name__ == "__main__":
    main()


