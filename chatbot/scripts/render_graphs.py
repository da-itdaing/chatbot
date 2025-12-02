from __future__ import annotations

"""
컨슈머/셀러 LangGraph 구조를 시각화하여 아티팩트로 저장하는 스크립트.

출력:
- artifacts/graphs/consumer_graph.png (및 .mmd)
- artifacts/graphs/seller_graph.png   (및 .mmd)
"""

from pathlib import Path
import sys

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "artifacts" / "graphs"

# 루트 경로를 sys.path에 주입하여 `app` 패키지를 모듈로 인식시킨다.
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    # OpenAI, Postgres 설정을 위해 환경 변수를 선 로드한다.
    load_dotenv(ROOT / "chatbot.env")

    # 환경 변수를 읽은 뒤에 그래프를 지연 임포트하여 초기화 순서를 보장한다.
    from app.graphs.consumer import build_consumer_graph
    from app.graphs.seller import build_seller_graph

    # 체크포인터 없이 그래프를 구성한다. 구조 확인용이므로 저장소 의존성이 없다.
    consumer = build_consumer_graph(checkpointer=None)
    seller = build_seller_graph(checkpointer=None)

    # Mermaid 텍스트 표현 저장
    consumer_mermaid = consumer.get_graph().draw_mermaid()
    (OUT_DIR / "consumer_graph.mmd").write_text(consumer_mermaid, encoding="utf-8")

    seller_mermaid = seller.get_graph().draw_mermaid()
    (OUT_DIR / "seller_graph.mmd").write_text(seller_mermaid, encoding="utf-8")

    # PNG 생성 (graphviz/pygraphviz 미설치 시 예외를 무시한다)
    try:
        consumer.get_graph().draw_png(str(OUT_DIR / "consumer_graph.png"))  # type: ignore[arg-type]
    except Exception as exc:  # pragma: no cover - optional dependency
        print("Skipping consumer_graph.png:", exc)

    try:
        seller.get_graph().draw_png(str(OUT_DIR / "seller_graph.png"))  # type: ignore[arg-type]
    except Exception as exc:  # pragma: no cover - optional dependency
        print("Skipping seller_graph.png:", exc)


if __name__ == "__main__":
    main()


