from __future__ import annotations

"""
Run LangSmith evaluations over the Itdaing chatbot using a Dataset.

This script is a thin wrapper around the LangSmith SDK, following:
- https://docs.langchain.com/langsmith/evaluation-quickstart#sdk
- https://docs.langchain.com/langsmith/evaluate-chatbot-tutorial
"""

import argparse
import sys
from pathlib import Path
from typing import Any, Dict, List

from langsmith import Client

try:
    from tqdm import tqdm
except ImportError:  # pragma: no cover - optional dependency
    def tqdm(iterable, *args, **kwargs):  # type: ignore[no-redef]
        return iterable

# Ensure the project root (containing the `app` package) is on sys.path
ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from target_function import run_itdaing_chatbot


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run LangSmith evaluation for the Itdaing chatbot on a Dataset.",
    )
    parser.add_argument(
        "--dataset-name",
        required=True,
        help="Name of the LangSmith Dataset to evaluate.",
    )
    parser.add_argument(
        "--experiment",
        required=True,
        help="Logical experiment id (e.g. baseline_v0, guardrail_v1).",
    )
    parser.add_argument(
        "--run-name",
        help="Optional LangSmith run/experiment name; defaults to experiment id.",
    )
    parser.add_argument(
        "--use-custom-evaluator",
        action="store_true",
        help="If set, use a custom LLM-as-judge evaluator instead of built-in criteria.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    client = Client()
    run_name = args.run_name or args.experiment
    experiment_id = args.experiment

    # Fetch dataset and examples from LangSmith
    dataset = client.read_dataset(dataset_name=args.dataset_name)
    examples = list(client.list_examples(dataset_id=dataset.id))

    print(
        f"Running evaluation locally for dataset='{args.dataset_name}' "
        f"({len(examples)} examples), experiment='{experiment_id}'"
    )

    for ex in tqdm(examples, desc="Evaluating examples"):
        inputs: Dict[str, Any] = ex.inputs or {}
        # Propagate experiment metadata into the target function config
        config: Dict[str, Any] = {
            "experiment_id": experiment_id,
            "run_name": run_name,
        }
        result = run_itdaing_chatbot(inputs, config=config)
        answer = result.get("answer", "")
        print(f"[{ex.id}] answer snippet: {answer[:80]!r}")


if __name__ == "__main__":
    main()



