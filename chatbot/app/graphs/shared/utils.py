from __future__ import annotations

from typing import List, Sequence

from langchain_core.documents import Document
from langchain_core.messages import BaseMessage, HumanMessage

from app.utils.search import WebSearchClient


def latest_user_message(messages: Sequence[BaseMessage]) -> HumanMessage:
    for message in reversed(messages):
        if isinstance(message, HumanMessage):
            return message
    raise ValueError("대화 내에 사용자 메시지가 존재하지 않습니다.")


def format_messages(messages: Sequence[BaseMessage]) -> str:
    lines: List[str] = []
    for msg in messages:
        if not isinstance(msg, BaseMessage):
            continue
        role = msg.type.upper()
        content = msg.content if isinstance(msg.content, str) else str(msg.content)
        lines.append(f"[{role}] {content}")
    return "\n".join(lines)


def extend_with_web_results(
    docs: Sequence[Document],
    query: str,
    web_search: WebSearchClient,
) -> List[Document]:
    materialized = list(docs)
    if materialized or not web_search.enabled:
        return materialized
    extra = web_search.search_sync(query)
    if extra:
        materialized.extend(extra)
    return materialized


async def extend_with_web_results_async(
    docs: Sequence[Document],
    query: str,
    web_search: WebSearchClient,
) -> List[Document]:
    materialized = list(docs)
    if materialized or not web_search.enabled:
        return materialized
    extra = await web_search.search_async(query)
    if extra:
        materialized.extend(extra)
    return materialized


__all__ = [
    "latest_user_message",
    "format_messages",
    "extend_with_web_results",
    "extend_with_web_results_async",
]

