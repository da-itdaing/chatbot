from __future__ import annotations

"""
LangSmith target function for the Itdaing chatbot.

이 모듈은 LangSmith SDK에서 사용하는 Target function을 제공하며,
FastAPI HTTP 레이어를 거치지 않고 LangGraph(consumer/seller)를 직접 호출합니다.

참고:
- https://docs.langchain.com/langsmith/evaluation-quickstart#sdk
- https://docs.langchain.com/langsmith/evaluate-chatbot-tutorial
"""

import asyncio
from typing import Any, Dict, Optional

from dotenv import load_dotenv
from langchain_core.messages import AIMessage
from enum import Enum

from langgraph.checkpoint.memory import MemorySaver

from app.config import ROOT_DIR, get_settings
from app.graphs.consumer import build_consumer_graph_async
from app.graphs.seller import build_seller_graph_async


class Mode(str, Enum):
    CONSUMER = "consumer"
    SELLER = "seller"


class Transport(str, Enum):
    SYNC = "sync"
    SYNC_STREAM = "sync_stream"
    ASYNC = "async"
    ASYNC_STREAM = "async_stream"


_CONSUMER_GRAPH: Any = None
_SELLER_GRAPH: Any = None


def _extract_answer(messages: Any) -> str:
    if not messages:
        return ""
    last = messages[-1]
    if isinstance(last, AIMessage):
        content = last.content
    elif isinstance(last, dict) and last.get("role") == "assistant":
        content = last.get("content", "")
    else:
        content = getattr(last, "content", "")
    return content if isinstance(content, str) else str(content)


async def _ensure_graphs() -> None:
    """
    Lazy-initialise LangGraph async graphs for evaluation.

    - OpenAI / LangSmith / PGVector 설정은 그대로 사용
    - 하지만 LangGraph 체크포인터(Postgres)는 사용하지 않고,
      in-memory 상태로만 그래프를 빌드한다.

    이는 LangSmith 평가 스크립트에서 단일턴/품질 중심 테스트를
    안정적으로 수행하기 위한 설정이며, 실제 운영 FastAPI 경로에서는
    여전히 AsyncPostgresSaver 기반 체크포인터를 사용한다.
    """

    global _CONSUMER_GRAPH, _SELLER_GRAPH
    if _CONSUMER_GRAPH is not None and _SELLER_GRAPH is not None:
        return

    # Ensure env vars from chatbot.env are loaded (OpenAI, Postgres, LangSmith, ...)
    load_dotenv(ROOT_DIR / "chatbot.env")
    get_settings()  # 설정/DSN 유효성만 확인

    # 평가 경로에서는 LangGraph 체크포인터(Postgres)가 아니라
    # in-memory MemorySaver를 사용해 상태를 관리한다.
    saver = MemorySaver()
    _CONSUMER_GRAPH = build_consumer_graph_async(checkpointer=saver)
    _SELLER_GRAPH = build_seller_graph_async(checkpointer=saver)


def run_itdaing_chatbot(
    inputs: Dict[str, Any],
    config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Synchronous wrapper around the async ChatClient for LangSmith.

    Expected inputs format (flexible, but recommended):
      {
        "message": "<user utterance>",
        "mode": "consumer" | "seller",    # optional, default "consumer"
        "user_id": "<string>",            # optional, default "eval-user"
        "session_id": "<string>",         # optional, default "eval-session"
        "transport": "sync" | "async" | ...  # optional, default "async"
      }
    """

    message = str(inputs.get("message") or "")
    if not message:
        raise ValueError("inputs['message'] is required for run_itdaing_chatbot")

    mode_str = str(inputs.get("mode") or "consumer")
    mode = Mode.CONSUMER if mode_str != "seller" else Mode.SELLER

    transport_str = str(inputs.get("transport") or "async")
    transport: Transport
    if transport_str in ("sync", "SYNC"):
        transport = Transport.SYNC
    elif transport_str in ("sync_stream", "SYNC_STREAM"):
        transport = Transport.SYNC_STREAM
    elif transport_str in ("async_stream", "ASYNC_STREAM"):
        transport = Transport.ASYNC_STREAM
    else:
        transport = Transport.ASYNC

    user_id = str(inputs.get("user_id") or "eval-user")
    session_id = str(inputs.get("session_id") or "eval-session")

    experiment_id = None
    if config:
        experiment_id = config.get("experiment_id") or config.get("run_id")

    async def _run() -> Dict[str, Any]:
        await _ensure_graphs()

        thread_id = f"{mode.value}:{user_id}:{session_id}"
        metadata: Dict[str, Any] = {
            "mode": mode.value,
            "transport": transport.value,
        }
        if experiment_id:
            metadata["experiment_id"] = experiment_id
        if config:
            # Propagate any upstream labels (graph_version, prompt_version, ...)
            for key in [
                "graph_version",
                "prompt_version",
                "seed_version",
                "guardrail_policy_version",
            ]:
                if key in config:
                    metadata[key] = config[key]

        cfg: Dict[str, Any] = {
            "configurable": {"thread_id": thread_id},
            "metadata": metadata,
        }
        state = {
            "messages": [
                {
                    "role": "user",
                    "content": message,
                }
            ]
        }

        graph = _CONSUMER_GRAPH if mode == Mode.CONSUMER else _SELLER_GRAPH
        result = await graph.ainvoke(state, config=cfg)
        answer_text = _extract_answer(result.get("messages", []))
        return {"answer": answer_text, "thread_id": thread_id}

    return asyncio.run(_run())


__all__ = ["run_itdaing_chatbot"]


