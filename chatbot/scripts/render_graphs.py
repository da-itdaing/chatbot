from __future__ import annotations

"""
Utility to render LangGraph structures for the consumer/seller bots.

Outputs:
- artifacts/graphs/consumer_graph.png (and .mmd)
- artifacts/graphs/seller_graph.png   (and .mmd)
"""

from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "artifacts" / "graphs"


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    # Ensure we have environment variables loaded (for OpenAI / PG config).
    load_dotenv(ROOT / "chatbot.env")

    # Import graphs lazily after env is loaded, so that any OpenAIEmbeddings
    # or model clients that initialise at import time see the correct env.
    from app.graphs.consumer import build_consumer_graph
    from app.graphs.seller import build_seller_graph

    # Build graphs without a checkpointer; structure is independent of storage.
    consumer = build_consumer_graph(checkpointer=None)
    seller = build_seller_graph(checkpointer=None)

    # Mermaid (text) representations
    consumer_mermaid = consumer.get_graph().draw_mermaid()
    (OUT_DIR / "consumer_graph.mmd").write_text(consumer_mermaid, encoding="utf-8")

    seller_mermaid = seller.get_graph().draw_mermaid()
    (OUT_DIR / "seller_graph.mmd").write_text(seller_mermaid, encoding="utf-8")

    # PNGs (may require graphviz/pygraphviz; ignore errors if unavailable)
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


