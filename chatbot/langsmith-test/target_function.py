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
from uuid import uuid4

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


async def run_itdaing_chatbot_async(
    inputs: Dict[str, Any],
    config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Async entrypoint used by LangSmith `aevaluate`.

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

    await _ensure_graphs()

    raw_thread_id = (
        str(inputs.get("thread_id"))
        if inputs.get("thread_id")
        else None
    )
    # LangGraph checkpoint에 남은 상태가 다음 케이스로 전파되지 않도록
    # 케이스별 고유 thread_id를 생성한다.
    # (Dataset 입력에 thread_id가 명시되면 그대로 사용)
    thread_id = raw_thread_id or f"{mode.value}:{user_id}:{session_id}:{uuid4().hex}"
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


def run_itdaing_chatbot(
    inputs: Dict[str, Any],
    config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Backwards-compatible synchronous wrapper.
    """

    return asyncio.run(run_itdaing_chatbot_async(inputs, config=config))


async def run_itdaing_chatbot_multiturn_async(
    inputs: Dict[str, Any],
    config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    멀티턴 테스트용 비동기 함수.
    
    inputs에 'turns' 배열이 있으면 순차적으로 처리하고,
    마지막 턴의 응답을 반환한다. 같은 thread_id를 유지하여
    컨텍스트 연속성을 테스트한다.
    
    Expected inputs format:
      {
        "turns": [
          {"role": "user", "content": "첫 번째 질문"},
          {"role": "user", "content": "두 번째 질문 (컨텍스트 참조)"},
        ],
        "mode": "consumer" | "seller",
        ...
      }
    
    또는 기존 'message' 형식도 지원 (단일턴으로 처리)
    """
    
    turns = inputs.get("turns")
    
    # turns가 없으면 기존 single-turn 함수로 위임
    if not turns or not isinstance(turns, list):
        return await run_itdaing_chatbot_async(inputs, config=config)
    
    # 멀티턴 처리
    mode_str = str(inputs.get("mode") or "consumer")
    mode = Mode.CONSUMER if mode_str != "seller" else Mode.SELLER
    
    user_id = str(inputs.get("user_id") or "eval-user")
    session_id = str(inputs.get("session_id") or "eval-session")
    
    await _ensure_graphs()
    
    # 멀티턴은 같은 thread_id를 유지해야 함
    thread_id = inputs.get("thread_id") or f"{mode.value}:{user_id}:{session_id}:{uuid4().hex}"
    
    experiment_id = None
    if config:
        experiment_id = config.get("experiment_id") or config.get("run_id")
    
    metadata: Dict[str, Any] = {
        "mode": mode.value,
        "turn_type": "multi",
        "total_turns": len(turns),
    }
    if experiment_id:
        metadata["experiment_id"] = experiment_id
    
    cfg: Dict[str, Any] = {
        "configurable": {"thread_id": thread_id},
        "metadata": metadata,
    }
    
    graph = _CONSUMER_GRAPH if mode == Mode.CONSUMER else _SELLER_GRAPH
    
    answers: list[str] = []
    
    for turn_idx, turn in enumerate(turns):
        # turn이 dict이고 'content' 키가 있으면 사용, 아니면 문자열 그대로 사용
        if isinstance(turn, dict):
            role = turn.get("role", "user")
            content = turn.get("content", "")
        else:
            role = "user"
            content = str(turn)
        
        # assistant 턴은 건너뜀 (이미 이전 응답에 포함됨)
        if role == "assistant":
            continue
        
        if not content:
            continue
        
        state = {
            "messages": [
                {
                    "role": "user",
                    "content": content,
                }
            ]
        }
        
        # 각 턴에 turn_index 메타데이터 추가
        turn_cfg = {
            **cfg,
            "metadata": {
                **metadata,
                "turn_index": turn_idx,
            },
        }
        
        result = await graph.ainvoke(state, config=turn_cfg)
        answer_text = _extract_answer(result.get("messages", []))
        answers.append(answer_text)
    
    # 마지막 턴의 응답 반환 (모든 중간 응답도 포함)
    return {
        "answer": answers[-1] if answers else "",
        "all_answers": answers,
        "thread_id": thread_id,
        "total_turns": len(answers),
    }


def run_itdaing_chatbot_multiturn(
    inputs: Dict[str, Any],
    config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    멀티턴 테스트용 동기 래퍼.
    """
    return asyncio.run(run_itdaing_chatbot_multiturn_async(inputs, config=config))


__all__ = [
    "run_itdaing_chatbot",
    "run_itdaing_chatbot_async",
    "run_itdaing_chatbot_multiturn",
    "run_itdaing_chatbot_multiturn_async",
]


