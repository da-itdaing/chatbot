from __future__ import annotations

"""
Consumer 전용 LangSmith 평가 러너.

- unified Dataset (예: itdaing-chatbot-unified) 에서 mode=consumer 인 example만 필터링
- run_itdaing_chatbot_async 를 Target function 으로 사용
- 선택적으로 LLM-as-judge evaluator 활성화
"""

import argparse
import asyncio
import os
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, cast

from dotenv import load_dotenv
from langsmith import Client, aevaluate
from langsmith.schemas import Example

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from target_function import run_itdaing_chatbot_async  # type: ignore  # noqa: E402
from run_langsmith_evals import llm_judge_evaluator  # type: ignore  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run LangSmith evaluations for consumer chatbot examples only."
    )
    parser.add_argument(
        "--dataset-name",
        type=str,
        default="itdaing-chatbot-unified",
        help="LangSmith Dataset name (default: itdaing-chatbot-unified).",
    )
    parser.add_argument(
        "--dataset-id",
        type=str,
        help="Optional Dataset UUID for validation.",
    )
    parser.add_argument(
        "--experiment",
        type=str,
        required=True,
        help="Experiment id (e.g. consumer_rag_v1, consumer_guardrail_v1).",
    )
    parser.add_argument(
        "--run-name",
        type=str,
        help="Optional run name for LangSmith; defaults to experiment id.",
    )
    parser.add_argument(
        "--project-name",
        type=str,
        help="Optional LangSmith project name (overrides LANGSMITH_PROJECT).",
    )
    parser.add_argument(
        "--case-type",
        type=str,
        help="Filter by case_type (e.g. retrieval_recommendation, guardrail_safety).",
    )
    parser.add_argument(
        "--difficulty",
        type=str,
        help="Filter by difficulty (e.g. normal, hard).",
    )
    parser.add_argument(
        "--transport",
        type=str,
        help="Filter by transport (e.g. sync, async, async_stream).",
    )
    parser.add_argument(
        "--subset",
        type=str,
        help="Optional substring to match against case_group or section for filtering.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        help="Optional maximum number of examples to evaluate.",
    )
    parser.add_argument(
        "--use-judge",
        action="store_true",
        help="Include the LLM-as-judge evaluator defined in run_langsmith_evals.py.",
    )
    return parser.parse_args()


def _filter_examples(
    examples: Iterable[Example],
    *,
    case_type: Optional[str],
    difficulty: Optional[str],
    transport: Optional[str],
    subset: Optional[str],
    limit: Optional[int],
) -> List[Example]:
    subset_lower = subset.lower() if subset else None
    out: List[Example] = []
    for ex in examples:
        meta = ex.metadata or {}
        mode = str(meta.get("mode") or ex.inputs.get("mode") if ex.inputs else "").lower()
        if mode and mode != "consumer":
            continue
        if case_type and str(meta.get("case_type") or "") != case_type:
            continue
        if difficulty and str(meta.get("difficulty") or "") != difficulty:
            continue
        if transport and str(meta.get("transport") or "") != transport:
            continue
        if subset_lower:
            key = (
                str(meta.get("case_group") or "")
                + " "
                + str(meta.get("section") or "")
            ).lower()
            if subset_lower not in key:
                continue
        out.append(ex)
        if limit and len(out) >= limit:
            break
    return out


async def main_async() -> None:
    args = parse_args()

    # 환경 변수 로드 (OpenAI/LangSmith/DB 등)
    load_dotenv(ROOT_DIR / "chatbot.env")
    if args.project_name:
        os.environ["LANGSMITH_PROJECT"] = args.project_name

    client = Client()
    run_name = args.run_name or args.experiment
    experiment_id = args.experiment

    expected_dataset_id = args.dataset_id
    dataset = client.read_dataset(dataset_name=args.dataset_name)
    if expected_dataset_id and str(dataset.id) != expected_dataset_id:
        raise ValueError(
            f"Dataset ID mismatch: expected {expected_dataset_id}, got {dataset.id}"
        )

    all_examples = list(client.list_examples(dataset_id=dataset.id))
    filtered = _filter_examples(
        all_examples,
        case_type=args.case_type,
        difficulty=args.difficulty,
        transport=args.transport,
        subset=args.subset,
        limit=args.limit,
    )
    if not filtered:
        print("No consumer examples matched the given filters.")
        return

    print(
        f"Running consumer-only aevaluate() on dataset='{dataset.name}' "
        f"({dataset.id}), examples={len(filtered)}, experiment_prefix='{experiment_id}'"
    )

    evaluators: List[Awaitable[Dict[str, Any]] | Any] = []
    evaluator_fns: List[Any] = []
    if args.use_judge:
        evaluator_fns.append(llm_judge_evaluator)
    evaluators_arg = cast(Optional[List[Any]], evaluator_fns or None)

    await aevaluate(
        run_itdaing_chatbot_async,
        data=filtered,
        experiment_prefix=experiment_id,
        metadata={"run_name": run_name, "experiment_id": experiment_id, "mode": "consumer"},
        evaluators=evaluators_arg,
        client=client,
    )

    print("Consumer-only LangSmith aevaluate() completed.")


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()


