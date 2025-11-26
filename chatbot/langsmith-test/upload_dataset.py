from __future__ import annotations

"""
Upload original Itdaing test prompts into a LangSmith Dataset.

This script is intentionally minimal and follows the LangSmith SDK patterns:
- https://docs.langchain.com/langsmith/evaluation-quickstart#sdk
"""

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List

from langsmith import Client

try:
    from tqdm import tqdm
except ImportError:  # pragma: no cover - optional dependency
    def tqdm(iterable, *args, **kwargs):  # type: ignore[no-redef]
        return iterable

# Original data is stored under this package:
#   langsmith-test/original/test_prompts*.json
ORIGINAL_DIR = Path(__file__).resolve().parent / "original"


def _load_canonical_prompts(path: Path) -> List[Dict[str, Any]]:
    """Load /original/test_prompts.json (canonical 278-case file)."""

    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    prompts = data.get("prompts", [])
    if not isinstance(prompts, list):
        raise ValueError("Invalid canonical prompts format: 'prompts' is not a list")
    return prompts


def _load_subset_inputs(path: Path) -> List[str]:
    """
    Load subset-style JSON files in /original (e.g. test_prompts_30_se.json).

    These are simple lists of {\"input\": \"...\"}.
    """

    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise ValueError(f"Subset file {path} must be a JSON list")
    messages: List[str] = []
    for idx, item in enumerate(data):
        if not isinstance(item, dict) or "input" not in item:
            raise ValueError(f"Invalid entry at index {idx} in {path}: {item!r}")
        messages.append(str(item["input"]))
    return messages


def _get_or_create_dataset(client: Client, name: str, description: str | None = None):
    try:
        return client.read_dataset(dataset_name=name)
    except Exception:
        # Latest LangSmith SDK expects `dataset_name` instead of `name`.
        return client.create_dataset(dataset_name=name, description=description)


def upload_canonical(
    client: Client,
    dataset_name: str,
    subset_label: str | None = None,
) -> None:
    """
    Upload canonical /original/test_prompts.json into a LangSmith Dataset.

    Each example:
      inputs  = {\"message\": <text>}
      metadata = {id, role, section, subset?, source_file}
    """

    path = ORIGINAL_DIR / "test_prompts.json"
    prompts = _load_canonical_prompts(path)

    ds = _get_or_create_dataset(
        client,
        dataset_name,
        description="Itdaing original test prompts (canonical)",
    )

    for p in tqdm(prompts, desc="Uploading canonical prompts"):
        message = str(p.get("text") or "")
        if not message:
            continue
        metadata: Dict[str, Any] = {
            "id": p.get("id"),
            "role": p.get("role"),
            "section": p.get("section"),
            "raw": p.get("raw"),
            "source_file": str(path),
        }
        if subset_label:
            metadata["dataset_subset"] = subset_label

        client.create_example(
            inputs={"message": message},
            outputs={},  # no reference answer yet
            metadata=metadata,
            dataset_id=ds.id,
        )


def upload_subset(
    client: Client,
    dataset_name: str,
    subset_filename: str,
    subset_label: str,
) -> None:
    """
    Upload a subset-style JSON (30_se, 100, ...) as its own Dataset.

    Each example:
      inputs  = {\"message\": <input>}
      metadata = {subset, source_file, index}
    """

    path = ORIGINAL_DIR / subset_filename
    messages = _load_subset_inputs(path)

    ds = _get_or_create_dataset(
        client,
        dataset_name,
        description=f"Itdaing original subset: {subset_label}",
    )

    for idx, msg in enumerate(tqdm(messages, desc=f"Uploading subset {subset_label}")):
        metadata: Dict[str, Any] = {
            "dataset_subset": subset_label,
            "source_file": str(path),
            "index": idx,
        }
        client.create_example(
            inputs={"message": msg},
            outputs={},
            metadata=metadata,
            dataset_id=ds.id,
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Upload Itdaing original test prompts into a LangSmith Dataset."
    )
    parser.add_argument(
        "--dataset-name",
        required=True,
        help="LangSmith Dataset name to create or update.",
    )
    parser.add_argument(
        "--mode",
        choices=["canonical", "30_se", "30_se_hard", "100", "100_hard"],
        required=True,
        help="Which original source to upload.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    client = Client()

    if args.mode == "canonical":
        upload_canonical(client, dataset_name=args.dataset_name, subset_label=None)
    elif args.mode == "30_se":
        upload_subset(
            client,
            dataset_name=args.dataset_name,
            subset_filename="test_prompts_30_se.json",
            subset_label="30_se",
        )
    elif args.mode == "30_se_hard":
        upload_subset(
            client,
            dataset_name=args.dataset_name,
            subset_filename="test_prompts_30_se_hard.json",
            subset_label="30_se_hard",
        )
    elif args.mode == "100":
        upload_subset(
            client,
            dataset_name=args.dataset_name,
            subset_filename="test_prompts_100.json",
            subset_label="100",
        )
    elif args.mode == "100_hard":
        upload_subset(
            client,
            dataset_name=args.dataset_name,
            subset_filename="test_prompts_100_hard.json",
            subset_label="100_hard",
        )


if __name__ == "__main__":
    main()


