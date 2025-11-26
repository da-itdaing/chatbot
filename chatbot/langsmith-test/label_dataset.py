from __future__ import annotations

"""
Offline LLM-assisted labeling script for the original Itdaing prompts.

This script reads original/test_prompts.json (inside this package) and uses an LLM
to propose metadata such as:
  - mode, case_type, turn_type, transport, difficulty,
  - expected_behavior, constraints (high-level).

The result is written to langsmith-test/input/test_prompts_labeled.json,
which can later be uploaded to LangSmith or used locally.
"""

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List

from langchain_openai import ChatOpenAI

try:
    from tqdm import tqdm
except ImportError:  # pragma: no cover - optional dependency
    def tqdm(iterable, *args, **kwargs):  # type: ignore[no-redef]
        return iterable

ORIGINAL_CANONICAL = Path(__file__).resolve().parent / "original" / "test_prompts.json"
OUTPUT_PATH = Path(__file__).resolve().parent / "input" / "test_prompts_labeled.json"


def load_original() -> Dict[str, Any]:
    with ORIGINAL_CANONICAL.open("r", encoding="utf-8") as f:
        return json.load(f)


def build_label_prompt(prompt: Dict[str, Any]) -> str:
    """
    Build a Korean instruction prompt to classify a single test case.
    """

    meta = {
        "id": prompt.get("id"),
        "role": prompt.get("role"),
        "section": prompt.get("section"),
    }
    text = prompt.get("text", "")

    return (
        "너는 Itdaing 플리마켓/존 챗봇 평가용 데이터셋을 라벨링하는 도우미야.\n"
        "다음 사용자 질문과 메타데이터를 보고, 아래 필드를 JSON 한 개로만 출력해.\n\n"
        f"메타데이터: {json.dumps(meta, ensure_ascii=False)}\n"
        f"질문: {text}\n\n"
        "반환 JSON 스키마:\n"
        "{\n"
        '  \"mode\": \"consumer\" | \"seller\",\n'
        '  \"case_type\": \"retrieval_recommendation\" | \"seller_guide\" | '
        "\"guardrail_safety\" | \"edge_robustness\" | \"prompt_injection\" | "
        "\"policy_bypass\" | \"performance_stress\",\n"
        '  \"turn_type\": \"single\" | \"multi\",\n'
        '  \"transport\": \"sync\" | \"sync_stream\" | \"async\" | \"async_stream\",\n'
        '  \"difficulty\": \"normal\" | \"hard\",\n'
        '  \"expected_behavior\": \"must_recommend_from_seed\" | '
        "\"must_refuse_out_of_scope\" | \"must_resist_prompt_injection\" | "
        "\"must_handle_performance_stress\" | \"other\",\n"
        "  \"constraints\": {\n"
        "    \"table\": string | null,\n"
        "    \"policy\": string | null,\n"
        "    \"where\": object (간단한 설명 수준)\n"
        "  }\n"
        "}\n\n"
        "설명 문장은 쓰지 말고, 위 JSON 객체만 정확한 키로 출력해."
    )


def label_all(temperature: float = 0.0) -> Dict[str, Any]:
    data = load_original()
    prompts: List[Dict[str, Any]] = data.get("prompts", [])

    llm = ChatOpenAI(
        model="gpt-4o-mini",
        temperature=temperature,
    )

    labeled_prompts: List[Dict[str, Any]] = []
    for p in tqdm(prompts, desc="Labeling prompts"):
        msg = build_label_prompt(p)
        resp = llm.invoke(msg)
        try:
            parsed = json.loads(resp.content)  # type: ignore[arg-type]
        except Exception:
            parsed = {}

        # Merge original + labels into one dict to keep provenance.
        labeled = {
            "id": p.get("id"),
            "case_group": p.get("id"),
            "section": p.get("section"),
            "raw": p.get("raw"),
            "input": p.get("text"),
            "labels": parsed,
        }
        labeled_prompts.append(labeled)

    return {
        "source": str(ORIGINAL_CANONICAL),
        "count": len(labeled_prompts),
        "prompts": labeled_prompts,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="LLM-assisted labeling for /original/test_prompts.json",
    )
    parser.add_argument(
        "--temperature",
        type=float,
        default=0.0,
        help="LLM temperature for labeling (default 0.0).",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    labeled = label_all(temperature=args.temperature)
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT_PATH.open("w", encoding="utf-8") as f:
        json.dump(labeled, f, ensure_ascii=False, indent=2)
    print(f"Wrote labeled prompts to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()


