#!/usr/bin/env python
"""
P99 Latency 분석 스크립트

LangSmith 실험 결과를 분석하여:
- P99, P95, P90 느린 케이스 식별
- 카테고리별 latency 분포
- 턴 수별 latency 상관관계
- 느린 케이스의 공통 패턴 분석
"""

import argparse
import json
import statistics
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from dotenv import load_dotenv
from langsmith import Client

# PYTHONPATH 설정
CHATBOT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(CHATBOT_ROOT))
load_dotenv(CHATBOT_ROOT / "chatbot.env")

ANALYSIS_DIR = Path(__file__).parent / "experiments" / "analysis"
ANALYSIS_DIR.mkdir(parents=True, exist_ok=True)


def get_experiment_runs(
    client: Client,
    experiment_name: str,
    dataset_name: Optional[str] = None,
) -> List[Any]:
    """LangSmith에서 실험 결과 조회"""
    
    runs_data = []
    
    # 1. experiment_name이 포함된 프로젝트 찾기
    projects = list(client.list_projects())
    matching_projects = [p for p in projects if experiment_name in p.name]
    
    if matching_projects:
        print(f"  → 매칭된 프로젝트: {[p.name for p in matching_projects]}")
        
        for project in matching_projects:
            try:
                runs = client.list_runs(
                    project_name=project.name,
                    limit=500,
                )
                for run in runs:
                    runs_data.append(run)
            except Exception as e:
                print(f"  Warning: {project.name} 조회 실패: {e}")
    
    # 2. 프로젝트를 못 찾으면 데이터셋 기반으로 검색
    if not runs_data and dataset_name:
        try:
            datasets = list(client.list_datasets())
            matching_ds = [d for d in datasets if dataset_name in d.name]
            if matching_ds:
                print(f"  → 데이터셋 기반 검색: {matching_ds[0].name}")
                # 데이터셋의 예제들에서 run 찾기
                examples = list(client.list_examples(dataset_id=matching_ds[0].id))
                print(f"  → {len(examples)}개 예제 발견")
        except Exception as e:
            print(f"  Warning: 데이터셋 검색 실패: {e}")
    
    return runs_data


def analyze_runs(runs: List[Any]) -> Dict[str, Any]:
    """실험 결과 분석"""
    
    if not runs:
        return {"error": "No runs found"}
    
    # 데이터 추출
    latencies: List[float] = []
    quality_scores: List[float] = []
    run_details: List[Dict[str, Any]] = []
    
    for run in runs:
        # latency 계산
        if run.end_time and run.start_time:
            latency = (run.end_time - run.start_time).total_seconds()
            latencies.append(latency)
        else:
            latency = None
        
        # metadata 추출
        metadata = run.extra.get("metadata", {}) if run.extra else {}
        inputs = run.inputs or {}
        outputs = run.outputs or {}
        
        # 턴 수 계산
        turns = inputs.get("turns", [])
        if isinstance(turns, list):
            user_turns = len([t for t in turns if isinstance(t, dict) and t.get("role") == "user"])
        else:
            user_turns = 1  # single turn
        
        # 쿼리 추출
        if turns and isinstance(turns, list):
            query = " | ".join([t.get("content", "")[:50] for t in turns if isinstance(t, dict) and t.get("role") == "user"])
        else:
            query = inputs.get("message", "")[:100]
        
        detail = {
            "run_id": str(run.id),
            "run_name": run.name,
            "latency": latency,
            "query": query,
            "user_turns": user_turns,
            "category": metadata.get("category", "unknown"),
            "answer_length": len(outputs.get("answer", "")),
            "error": run.error if hasattr(run, "error") else None,
        }
        
        # feedback에서 quality_score 추출
        try:
            feedbacks = list(run.feedbacks) if hasattr(run, "feedbacks") and run.feedbacks else []
            for fb in feedbacks:
                if fb.key == "quality_score" and fb.score is not None:
                    detail["quality_score"] = fb.score
                    quality_scores.append(fb.score)
        except Exception:
            pass
        
        run_details.append(detail)
    
    # 통계 계산
    valid_latencies = [l for l in latencies if l is not None]
    
    if not valid_latencies:
        return {"error": "No valid latency data"}
    
    sorted_latencies = sorted(valid_latencies)
    n = len(sorted_latencies)
    
    def percentile(p: float) -> float:
        idx = int(n * p / 100)
        return sorted_latencies[min(idx, n - 1)]
    
    stats = {
        "total_runs": len(runs),
        "valid_runs": n,
        "latency": {
            "mean": round(statistics.mean(valid_latencies), 2),
            "median": round(statistics.median(valid_latencies), 2),
            "stdev": round(statistics.stdev(valid_latencies), 2) if n > 1 else 0,
            "min": round(min(valid_latencies), 2),
            "max": round(max(valid_latencies), 2),
            "p50": round(percentile(50), 2),
            "p90": round(percentile(90), 2),
            "p95": round(percentile(95), 2),
            "p99": round(percentile(99), 2),
        },
    }
    
    if quality_scores:
        stats["quality_score"] = {
            "mean": round(statistics.mean(quality_scores), 2),
            "median": round(statistics.median(quality_scores), 2),
            "min": round(min(quality_scores), 2),
            "max": round(max(quality_scores), 2),
        }
    
    # 카테고리별 분석
    category_stats: Dict[str, List[float]] = {}
    for detail in run_details:
        cat = detail.get("category", "unknown")
        lat = detail.get("latency")
        if lat is not None:
            if cat not in category_stats:
                category_stats[cat] = []
            category_stats[cat].append(lat)
    
    stats["by_category"] = {
        cat: {
            "count": len(lats),
            "mean": round(statistics.mean(lats), 2),
            "p90": round(sorted(lats)[int(len(lats) * 0.9)], 2) if len(lats) > 1 else round(lats[0], 2),
        }
        for cat, lats in category_stats.items()
    }
    
    # 턴 수별 분석
    turn_stats: Dict[int, List[float]] = {}
    for detail in run_details:
        turns = detail.get("user_turns", 1)
        lat = detail.get("latency")
        if lat is not None:
            if turns not in turn_stats:
                turn_stats[turns] = []
            turn_stats[turns].append(lat)
    
    stats["by_turns"] = {
        f"{turns}_turns": {
            "count": len(lats),
            "mean": round(statistics.mean(lats), 2),
            "p90": round(sorted(lats)[int(len(lats) * 0.9)], 2) if len(lats) > 1 else round(lats[0], 2),
        }
        for turns, lats in sorted(turn_stats.items())
    }
    
    # P99 케이스 식별 (상위 10% 느린 케이스)
    p90_threshold = percentile(90)
    slow_cases = [
        d for d in run_details
        if d.get("latency") is not None and d["latency"] >= p90_threshold
    ]
    slow_cases.sort(key=lambda x: x.get("latency", 0), reverse=True)
    
    stats["slow_cases"] = slow_cases[:10]  # 상위 10개
    
    return stats


def generate_markdown_report(
    stats: Dict[str, Any],
    experiment_name: str,
) -> str:
    """Markdown 형식 리포트 생성"""
    
    if "error" in stats:
        return f"# Error\n\n{stats['error']}"
    
    latency = stats.get("latency", {})
    
    report = f"""# P99 Latency 분석 리포트

## 실험 정보
- **실험명**: {experiment_name}
- **총 실행 수**: {stats.get('total_runs', 0)}
- **유효 실행 수**: {stats.get('valid_runs', 0)}
- **분석 시간**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

---

## Latency 통계

| 지표 | 값 |
|------|-----|
| 평균 | {latency.get('mean', '-')}초 |
| 중앙값 | {latency.get('median', '-')}초 |
| 표준편차 | {latency.get('stdev', '-')}초 |
| 최소 | {latency.get('min', '-')}초 |
| 최대 | {latency.get('max', '-')}초 |
| **P50** | {latency.get('p50', '-')}초 |
| **P90** | {latency.get('p90', '-')}초 |
| **P95** | {latency.get('p95', '-')}초 |
| **P99** | {latency.get('p99', '-')}초 |

"""
    
    # Quality Score
    if "quality_score" in stats:
        qs = stats["quality_score"]
        report += f"""
## Quality Score 통계

| 지표 | 값 |
|------|-----|
| 평균 | {qs.get('mean', '-')}/5.0 |
| 중앙값 | {qs.get('median', '-')}/5.0 |
| 최소 | {qs.get('min', '-')}/5.0 |
| 최대 | {qs.get('max', '-')}/5.0 |

"""
    
    # 카테고리별 분석
    if "by_category" in stats:
        report += "## 카테고리별 Latency\n\n"
        report += "| 카테고리 | 케이스 수 | 평균 | P90 |\n"
        report += "|----------|----------|------|-----|\n"
        for cat, data in sorted(stats["by_category"].items(), key=lambda x: x[1]["mean"], reverse=True):
            report += f"| {cat} | {data['count']} | {data['mean']}초 | {data['p90']}초 |\n"
        report += "\n"
    
    # 턴 수별 분석
    if "by_turns" in stats:
        report += "## 턴 수별 Latency\n\n"
        report += "| 턴 수 | 케이스 수 | 평균 | P90 |\n"
        report += "|-------|----------|------|-----|\n"
        for turns, data in stats["by_turns"].items():
            report += f"| {turns} | {data['count']} | {data['mean']}초 | {data['p90']}초 |\n"
        report += "\n"
    
    # 느린 케이스 목록
    if "slow_cases" in stats and stats["slow_cases"]:
        report += "## 상위 느린 케이스 (P90+)\n\n"
        report += "| # | Latency | 턴 수 | 카테고리 | 쿼리 |\n"
        report += "|---|---------|-------|----------|------|\n"
        for i, case in enumerate(stats["slow_cases"], 1):
            query = case.get("query", "")[:40].replace("|", "\\|")
            report += f"| {i} | {case.get('latency', '-'):.1f}초 | {case.get('user_turns', '-')} | {case.get('category', '-')} | {query}... |\n"
        report += "\n"
    
    # 개선 제안
    report += """---

## 개선 제안

### P99가 높은 원인 분석
"""
    
    if latency.get("p99", 0) > 30:
        report += "- P99가 30초 이상으로 매우 높음 → 복잡한 케이스 최적화 필요\n"
    
    if "by_turns" in stats:
        max_turn_latency = max((d["mean"] for d in stats["by_turns"].values()), default=0)
        if max_turn_latency > 15:
            report += "- 멀티턴 대화에서 latency가 높음 → 요약 전략 개선 권장\n"
    
    if "by_category" in stats:
        for cat, data in stats["by_category"].items():
            if data["mean"] > 20:
                report += f"- `{cat}` 카테고리 평균 latency가 높음 → 해당 유형 최적화 필요\n"
    
    return report


def main():
    parser = argparse.ArgumentParser(description="P99 Latency 분석")
    parser.add_argument(
        "--experiment",
        type=str,
        required=True,
        help="분석할 실험 이름 (예: multi-1202-v1)",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="출력 파일 경로 (기본: experiments/analysis/<experiment>_analysis.json)",
    )
    args = parser.parse_args()
    
    print(f"🔍 P99 분석 시작: {args.experiment}")
    
    client = Client()
    
    # 실험 결과 조회
    print("  LangSmith에서 결과 조회 중...")
    runs = get_experiment_runs(client, args.experiment)
    print(f"  → {len(runs)}개 run 발견")
    
    # 분석
    print("  분석 중...")
    stats = analyze_runs(runs)
    
    # 결과 저장
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    
    # JSON 저장
    json_path = ANALYSIS_DIR / f"{args.experiment}_analysis_{timestamp}.json"
    with open(json_path, "w") as f:
        json.dump(stats, f, indent=2, ensure_ascii=False, default=str)
    print(f"  → JSON 저장: {json_path}")
    
    # Markdown 리포트 생성
    report = generate_markdown_report(stats, args.experiment)
    md_path = ANALYSIS_DIR / f"{args.experiment}_analysis_{timestamp}.md"
    with open(md_path, "w") as f:
        f.write(report)
    print(f"  → Markdown 저장: {md_path}")
    
    # 콘솔 출력
    print("\n" + "=" * 60)
    print(report)
    
    return stats


if __name__ == "__main__":
    main()

