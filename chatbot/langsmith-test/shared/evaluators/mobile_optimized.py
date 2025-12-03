from __future__ import annotations

"""LangSmith evaluator to ensure mobile-friendly answers."""

import re
from typing import Any, Dict

from langsmith.schemas import Example, Run

MAX_SENTENCES = 4


def _count_sentences(text: str) -> int:
    stripped = text.strip()
    if not stripped:
        return 0
    # Split on sentence-ending punctuation while preserving Korean usage.
    parts = [segment.strip() for segment in re.split(r"[.!?\n]+", stripped)]
    return len([segment for segment in parts if segment])


def mobile_optimized_evaluator(run: Run, example: Example) -> Dict[str, Any]:
    """Return 1.0 when the answer is short, bullet-free, and guides to cards."""

    outputs = run.outputs or {}
    answer = str(outputs.get("answer") or outputs.get("delta") or "").strip()
    if not answer:
        return {
            "score": 0.0,
            "reason": "빈 답변",
            "mobile_sentence_count": 0,
            "has_bullets": False,
            "has_card_cta": False,
        }

    sentence_count = _count_sentences(answer)
    has_bullets = "•" in answer or "\n-" in answer or "\n*" in answer
    has_card_cta = "아래 카드" in answer or "카드에서" in answer

    meets_requirement = (
        sentence_count <= MAX_SENTENCES and not has_bullets and has_card_cta
    )

    return {
        "score": 1.0 if meets_requirement else 0.0,
        "mobile_sentence_count": sentence_count,
        "has_bullets": has_bullets,
        "has_card_cta": has_card_cta,
    }
