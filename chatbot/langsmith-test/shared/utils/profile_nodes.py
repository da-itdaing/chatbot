#!/usr/bin/env python
"""
노드별 실행 시간 프로파일링 스크립트.

사용법:
    python profile_nodes.py                     # 기본 테스트
    python profile_nodes.py "광주 플리마켓"      # 커스텀 쿼리
    python profile_nodes.py --runs 3            # 3회 실행 평균
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path
from typing import Dict, List

# 프로젝트 루트를 path에 추가
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from langchain_core.messages import HumanMessage

from app.graphs.consumer import build_consumer_graph_async


async def profile_single_run(
    graph,
    query: str,
    thread_id: str,
) -> tuple[Dict[str, float], float]:
    """단일 실행의 노드별 시간 측정."""
    state = {"messages": [HumanMessage(content=query)]}
    config = {"configurable": {"thread_id": thread_id}}

    total_start = time.time()
    node_times: Dict[str, float] = {}
    current_node = None
    node_start = None

    async for event in graph.astream_events(state, config=config, version="v2"):
        event_type = event.get("event")

        if event_type == "on_chain_start":
            name = event.get("name", "")
            if name and name not in ["LangGraph", "RunnableSequence"]:
                if current_node and node_start:
                    node_times[current_node] = time.time() - node_start
                current_node = name
                node_start = time.time()
        elif event_type == "on_chain_end":
            name = event.get("name", "")
            if name == current_node and node_start:
                node_times[current_node] = time.time() - node_start
                current_node = None
                node_start = None

    total_time = time.time() - total_start
    return node_times, total_time


def print_results(node_times: Dict[str, float], total_time: float, title: str = ""):
    """결과 출력."""
    print("=" * 70)
    if title:
        print(f"🔍 {title}")
        print("=" * 70)

    print(f"\n📊 노드별 실행 시간 (총 {total_time:.2f}초)")
    print("-" * 60)

    sorted_times = sorted(node_times.items(), key=lambda x: -x[1])
    for node, duration in sorted_times:
        pct = (duration / total_time) * 100 if total_time > 0 else 0
        bar = "█" * int(pct / 5) + "░" * (20 - int(pct / 5))
        print(f"{node:35s} {duration:6.2f}초 {bar} {pct:5.1f}%")

    print("-" * 60)
    print(f"{'Total':35s} {total_time:6.2f}초")

    # LLM 호출 노드 분석
    llm_keywords = [
        "classify_and_assess",
        "classify_case_and_plan",
        "generate",
        "check_hallucination",
        "rewrite",
        "basic_generate",
        "summarize_messages",
    ]
    llm_nodes = [n for n in node_times.keys() if any(kw in n for kw in llm_keywords)]
    llm_time = sum(node_times.get(n, 0) for n in llm_nodes)
    print(f"\n💡 LLM 호출 노드: {len(llm_nodes)}개, 총 {llm_time:.2f}초 ({llm_time/total_time*100:.1f}%)")


async def main():
    parser = argparse.ArgumentParser(description="노드별 실행 시간 프로파일링")
    parser.add_argument("query", nargs="?", default="광주 플리마켓 추천해줘", help="테스트 쿼리")
    parser.add_argument("--runs", "-n", type=int, default=1, help="실행 횟수 (평균)")
    args = parser.parse_args()

    graph = build_consumer_graph_async()

    if args.runs == 1:
        node_times, total_time = await profile_single_run(
            graph, args.query, f"profile-{int(time.time())}"
        )
        print_results(node_times, total_time, f"쿼리: {args.query}")
    else:
        # 여러 번 실행하여 평균 계산
        all_times: List[Dict[str, float]] = []
        total_times: List[float] = []

        for i in range(args.runs):
            print(f"실행 {i+1}/{args.runs}...", end=" ", flush=True)
            node_times, total_time = await profile_single_run(
                graph, args.query, f"profile-{int(time.time())}-{i}"
            )
            all_times.append(node_times)
            total_times.append(total_time)
            print(f"{total_time:.2f}초")

        # 평균 계산
        all_nodes = set()
        for nt in all_times:
            all_nodes.update(nt.keys())

        avg_times = {}
        for node in all_nodes:
            times = [nt.get(node, 0) for nt in all_times]
            avg_times[node] = sum(times) / len(times)

        avg_total = sum(total_times) / len(total_times)
        print_results(avg_times, avg_total, f"쿼리: {args.query} ({args.runs}회 평균)")

        # 표준편차 출력
        import statistics
        if len(total_times) > 1:
            std = statistics.stdev(total_times)
            print(f"\n📈 Total Latency: {avg_total:.2f}초 ± {std:.2f}초")


if __name__ == "__main__":
    asyncio.run(main())

