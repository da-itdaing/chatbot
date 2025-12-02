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

from dotenv import load_dotenv
from langsmith import Client

try:
    from tqdm import tqdm
except ImportError:  # pragma: no cover - optional dependency
    def tqdm(iterable, *args, **kwargs):  # type: ignore[no-redef]
        return iterable

# Original data is stored under this package:
#   langsmith-test/original/test_prompts*.json
ORIGINAL_DIR = Path(__file__).resolve().parent / "original"

# Unified prompts and labeled inputs live under the main app directory:
ROOT_DIR = Path(__file__).resolve().parents[1]
UNIFIED_PATH = ROOT_DIR / "app/sample_qa/test_prompts_unified.json"
INPUT_DIR = Path(__file__).resolve().parent / "input"
INPUT_TEST_PROMPTS_PATH = INPUT_DIR / "test_prompts.json"
INPUT_CONSUMER_SINGLE_PATH = INPUT_DIR / "test_prompts_consumer_single.json"
INPUT_CONSUMER_MULTI_PATH = INPUT_DIR / "test_prompts_consumer_multi.json"

DEFAULT_DATASET_NAME = "itdaing-chatbot-labeled"
CONSUMER_SINGLE_DATASET_NAME = "itdaing-consumer-single"
CONSUMER_MULTI_DATASET_NAME = "itdaing-consumer-multi"
# 새 labeled Dataset의 UUID는 최초 생성 시 LangSmith UI에서 확인해 주입한다.
# 기본값은 검증을 생략하기 위해 빈 문자열로 둔다.
DEFAULT_DATASET_ID = ""


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


def _get_or_create_dataset(
    client: Client,
    name: str,
    description: str | None = None,
    expected_id: str | None = None,
):
    try:
        dataset = client.read_dataset(dataset_name=name)
        if expected_id and str(dataset.id) != expected_id:
            raise ValueError(
                f"Dataset '{name}' found but ID mismatch "
                f"(expected {expected_id}, got {dataset.id})"
            )
        return dataset
    except Exception:
        if expected_id:
            raise ValueError(
                f"Dataset '{name}' with id {expected_id} was not found in LangSmith."
            )
        # Latest LangSmith SDK expects `dataset_name` instead of `name`.
        return client.create_dataset(dataset_name=name, description=description)


def upload_canonical(
    client: Client,
    dataset_name: str,
    subset_label: str | None = None,
    expected_dataset_id: str | None = None,
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
        expected_id=expected_dataset_id,
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


def upload_unified(
    client: Client,
    dataset_name: str,
    expected_dataset_id: str | None = None,
) -> None:
    """
    Upload app/sample_qa/test_prompts_unified.json into a LangSmith Dataset.

    Each example:
      inputs   = {"message": <text>, "mode": <consumer|seller>}
      metadata = {id, mode, case_group, case_type, section, raw, source_file}
    """

    if not UNIFIED_PATH.exists():
        raise FileNotFoundError(f"Unified prompts file not found: {UNIFIED_PATH}")

    with UNIFIED_PATH.open("r", encoding="utf-8") as f:
        data = json.load(f)
    prompts = data.get("prompts", [])
    if not isinstance(prompts, list):
        raise ValueError("Invalid unified prompts format: 'prompts' is not a list")

    ds = _get_or_create_dataset(
        client,
        dataset_name,
        description="Itdaing unified prompts (consumer+seller+guardrail)",
        expected_id=expected_dataset_id,
    )

    for p in tqdm(prompts, desc="Uploading unified prompts"):
        message = str(p.get("text") or p.get("input") or "")
        if not message:
            continue
        mode = str(p.get("mode") or "consumer")
        metadata: Dict[str, Any] = {
            "id": p.get("id"),
            "mode": mode,
            "case_group": p.get("case_group"),
            "case_type": p.get("case_type"),
            "section": p.get("section"),
            "raw": p.get("raw"),
            "source_file": str(UNIFIED_PATH),
        }
        client.create_example(
            inputs={"message": message, "mode": mode},
            outputs={},
            metadata=metadata,
            dataset_id=ds.id,
        )
def _load_input_prompts(path: Path) -> List[Dict[str, Any]]:
    """
    Load langsmith-test/input/test_prompts.json (labeled unified file).

    Expected top-level format:
    {
      "version": "...",
      "generated_at": "...",
      "schema": {...},
      "prompts": [ { id, mode, case_group, case_type, turn_type, transport,
                     difficulty, expected_behavior, section, input, constraints, ... }, ... ]
    }
    """

    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    prompts = data.get("prompts", [])
    if not isinstance(prompts, list):
        raise ValueError("Invalid input prompts format: 'prompts' is not a list")
    return prompts


def upload_canonical_v2(
    client: Client,
    dataset_name: str,
    expected_dataset_id: str | None = None,
) -> None:
    """
    Upload langsmith-test/input/test_prompts.json into a LangSmith Dataset.

    각 example:
      inputs  = {\"message\": <input>, \"mode\": <consumer|seller>}
      metadata = {id, mode, case_group, case_type, turn_type, transport,
                  difficulty, expected_behavior, section, constraints, source_file}
    """

    path = INPUT_TEST_PROMPTS_PATH
    prompts = _load_input_prompts(path)

    ds = _get_or_create_dataset(
        client,
        dataset_name,
        description="Itdaing labeled test prompts (input/test_prompts.json)",
        expected_id=expected_dataset_id,
    )

    for p in tqdm(prompts, desc="Uploading labeled input prompts"):
        message = str(p.get("input") or "").strip()
        if not message:
            continue
        mode = str(p.get("mode") or "consumer")
        metadata: Dict[str, Any] = {
            "id": p.get("id"),
            "mode": mode,
            "case_group": p.get("case_group"),
            "case_type": p.get("case_type"),
            "turn_type": p.get("turn_type"),
            "transport": p.get("transport"),
            "difficulty": p.get("difficulty"),
            "expected_behavior": p.get("expected_behavior"),
            "section": p.get("section"),
            "constraints": p.get("constraints"),
            "source_file": str(path),
        }
        client.create_example(
            inputs={"message": message, "mode": mode},
            outputs={},
            metadata=metadata,
            dataset_id=ds.id,
        )


def upload_consumer_single(
    client: Client,
    dataset_name: str,
    expected_dataset_id: str | None = None,
) -> None:
    """
    Upload langsmith-test/input/test_prompts_consumer_single.json into a LangSmith Dataset.

    싱글턴 전용 consumer 데이터셋 (멀티턴 필요 케이스 제외, placeholder/expected_behavior 수정 완료).
    """

    path = INPUT_CONSUMER_SINGLE_PATH
    prompts = _load_input_prompts(path)

    ds = _get_or_create_dataset(
        client,
        dataset_name,
        description="Itdaing consumer single-turn prompts (refined, no multi-turn cases)",
        expected_id=expected_dataset_id,
    )

    for p in tqdm(prompts, desc="Uploading consumer single-turn prompts"):
        message = str(p.get("input") or "").strip()
        if not message:
            continue
        mode = "consumer"
        metadata: Dict[str, Any] = {
            "id": p.get("id"),
            "mode": mode,
            "case_group": p.get("case_group"),
            "case_type": p.get("case_type"),
            "turn_type": "single",
            "transport": p.get("transport"),
            "difficulty": p.get("difficulty"),
            "expected_behavior": p.get("expected_behavior"),
            "section": p.get("section"),
            "constraints": p.get("constraints"),
            "source_file": str(path),
        }
        client.create_example(
            inputs={"message": message, "mode": mode},
            outputs={},
            metadata=metadata,
            dataset_id=ds.id,
        )


def upload_consumer_multi(
    client: Client,
    dataset_name: str,
    expected_dataset_id: str | None = None,
) -> None:
    """
    Upload langsmith-test/input/test_prompts_consumer_multi.json into a LangSmith Dataset.

    멀티턴 전용 consumer 데이터셋 (turns 배열 스키마).
    inputs에는 turns 배열 전체를 JSON 문자열로 저장.
    """

    path = INPUT_CONSUMER_MULTI_PATH
    prompts = _load_input_prompts(path)

    ds = _get_or_create_dataset(
        client,
        dataset_name,
        description="Itdaing consumer multi-turn prompts (turns array schema)",
        expected_id=expected_dataset_id,
    )

    for p in tqdm(prompts, desc="Uploading consumer multi-turn prompts"):
        turns = p.get("turns", [])
        if not turns:
            continue
        # 멀티턴은 turns 배열을 JSON 문자열로 저장
        turns_json = json.dumps(turns, ensure_ascii=False)
        # 마지막 사용자 메시지를 message로도 저장 (평가 시 참조용)
        last_user_msg = ""
        for turn in reversed(turns):
            if turn.get("role") == "user":
                last_user_msg = turn.get("content", "")
                break
        mode = "consumer"
        metadata: Dict[str, Any] = {
            "id": p.get("id"),
            "mode": mode,
            "case_group": p.get("case_group"),
            "case_type": p.get("case_type"),
            "turn_type": "multi",
            "transport": p.get("transport"),
            "difficulty": p.get("difficulty"),
            "expected_behavior": p.get("expected_behavior"),
            "section": p.get("section"),
            "constraints": p.get("constraints"),
            "source_file": str(path),
            "turn_count": len(turns),
        }
        client.create_example(
            inputs={
                "message": last_user_msg,
                "mode": mode,
                "turns": turns_json,
            },
            outputs={},
            metadata=metadata,
            dataset_id=ds.id,
        )




def upload_subset(
    client: Client,
    dataset_name: str,
    subset_filename: str,
    subset_label: str,
    expected_dataset_id: str | None = None,
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
        expected_id=expected_dataset_id,
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
        default=DEFAULT_DATASET_NAME,
        help=(
            "LangSmith Dataset name to create or update "
            f"(기본값: {DEFAULT_DATASET_NAME})."
        ),
    )
    parser.add_argument(
        "--dataset-id",
        help=(
            "Validate against an existing LangSmith Dataset UUID. "
            f"생략하면 기본 이름({DEFAULT_DATASET_NAME}) 사용 시 "
            f"{DEFAULT_DATASET_ID} 를 검증합니다."
        ),
    )
    parser.add_argument(
        "--mode",
        choices=[
            "canonical",
            "canonical_v2",
            "consumer_single",
            "consumer_multi",
            "30_se",
            "30_se_hard",
            "100",
            "100_hard",
            "unified",
        ],
        required=True,
        help="Which source to upload (original subsets, unified, or consumer single/multi).",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    # Ensure chatbot.env is loaded so LANGSMITH_* and OPENAI keys are visible
    load_dotenv(ROOT_DIR / "chatbot.env")
    client = Client()

    expected_dataset_id = args.dataset_id
    if not expected_dataset_id and args.dataset_name == DEFAULT_DATASET_NAME:
        expected_dataset_id = DEFAULT_DATASET_ID

    if args.mode == "canonical":
        upload_canonical(
            client,
            dataset_name=args.dataset_name,
            subset_label=None,
            expected_dataset_id=expected_dataset_id,
        )
    elif args.mode == "canonical_v2":
        upload_canonical_v2(
            client,
            dataset_name=args.dataset_name,
            expected_dataset_id=expected_dataset_id,
        )
    elif args.mode == "consumer_single":
        # consumer_single 모드일 때 기본 데이터셋명 사용
        ds_name = args.dataset_name
        if ds_name == DEFAULT_DATASET_NAME:
            ds_name = CONSUMER_SINGLE_DATASET_NAME
        upload_consumer_single(
            client,
            dataset_name=ds_name,
            expected_dataset_id=expected_dataset_id if args.dataset_id else None,
        )
    elif args.mode == "consumer_multi":
        # consumer_multi 모드일 때 기본 데이터셋명 사용
        ds_name = args.dataset_name
        if ds_name == DEFAULT_DATASET_NAME:
            ds_name = CONSUMER_MULTI_DATASET_NAME
        upload_consumer_multi(
            client,
            dataset_name=ds_name,
            expected_dataset_id=expected_dataset_id if args.dataset_id else None,
        )
    elif args.mode == "30_se":
        upload_subset(
            client,
            dataset_name=args.dataset_name,
            subset_filename="test_prompts_30_se.json",
            subset_label="30_se",
            expected_dataset_id=expected_dataset_id,
        )
    elif args.mode == "30_se_hard":
        upload_subset(
            client,
            dataset_name=args.dataset_name,
            subset_filename="test_prompts_30_se_hard.json",
            subset_label="30_se_hard",
            expected_dataset_id=expected_dataset_id,
        )
    elif args.mode == "100":
        upload_subset(
            client,
            dataset_name=args.dataset_name,
            subset_filename="test_prompts_100.json",
            subset_label="100",
            expected_dataset_id=expected_dataset_id,
        )
    elif args.mode == "100_hard":
        upload_subset(
            client,
            dataset_name=args.dataset_name,
            subset_filename="test_prompts_100_hard.json",
            subset_label="100_hard",
            expected_dataset_id=expected_dataset_id,
        )
    elif args.mode == "unified":
        upload_unified(
            client,
            dataset_name=args.dataset_name,
            expected_dataset_id=expected_dataset_id,
        )


if __name__ == "__main__":
    main()


