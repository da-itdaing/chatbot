from __future__ import annotations

"""
Loader script for consumer markets RAG data.

Usage (from project root):

    . .venv/bin/activate
    python -m app.data.markets_loader --reset

This will read `markets_seed.json`, convert each record into a LangChain
`Document`, and write them into a PGVector collection using the settings
defined in `app.config.Settings`.
"""

import argparse
import json
from pathlib import Path
from typing import Iterable, List

from dotenv import load_dotenv
from langchain_core.documents import Document
from langchain_openai import OpenAIEmbeddings
from langchain_postgres.vectorstores import PGVector

from app.config import ROOT_DIR, get_settings


# Ensure environment variables from chatbot.env are loaded, so that
# OpenAI / PG settings are visible to langchain-openai.
load_dotenv(ROOT_DIR / "chatbot.env")


def _load_json(path: Path) -> list[dict]:
    raw = path.read_text(encoding="utf-8")
    return json.loads(raw)


def _record_to_document(record: dict) -> Document:
    """
    Convert one markets_seed.json record into a LangChain Document.

    We keep the text fairly rich so that RAG answers have enough grounding,
    but avoid overly verbose, repeated boilerplate.
    """

    market_id = record.get("market_id", "")
    name = record.get("market_name", "")
    desc = record.get("market_description", "")
    category = record.get("market_category", "")
    attrs: Iterable[str] = record.get("market_attribute", []) or []
    amenities: Iterable[str] = record.get("market_ameni", []) or []
    rating = record.get("market_rating")
    locations: list[dict] = record.get("market_location", []) or []

    # Take first location as primary
    primary_loc = locations[0] if locations else {}
    address = primary_loc.get("address", "")
    distance_km = primary_loc.get("distance_km")
    zone_id = primary_loc.get("zone_id")
    lat = primary_loc.get("lat")
    lon = primary_loc.get("lon")

    text_lines: List[str] = []
    text_lines.append(f"[마켓 이름] {name}")
    text_lines.append(f"[카테고리] {category}")
    if attrs:
        text_lines.append(f"[분위기/특징] {', '.join(map(str, attrs))}")
    if amenities:
        text_lines.append(f"[편의시설] {', '.join(map(str, amenities))}")
    if address:
        text_lines.append(f"[주소] {address}")
    if distance_km is not None:
        text_lines.append(f"[기준 지점으로부터 거리(km)] {distance_km}")
    if rating is not None:
        text_lines.append(f"[평점(5점 만점)] {rating}")
    text_lines.append("")  # spacer
    text_lines.append("[상세 설명]")
    text_lines.append(desc)

    page_content = "\n".join(text_lines).strip()

    metadata = {
        "market_id": market_id,
        "market_name": name,
        "market_category": category,
        "market_attribute": list(attrs),
        "market_ameni": list(amenities),
        "market_rating": rating,
        "address": address,
        "zone_id": zone_id,
        "lat": lat,
        "lon": lon,
        "distance_km": distance_km,
    }
    return Document(page_content=page_content, metadata=metadata)


def build_documents(seed_path: Path) -> list[Document]:
    records = _load_json(seed_path)
    return [_record_to_document(rec) for rec in records]


def write_pgvector(documents: list[Document], *, reset: bool) -> None:
    settings = get_settings()
    embeddings = OpenAIEmbeddings(model=settings.openai_embedding_model)

    PGVector.from_documents(
        documents=documents,
        embedding=embeddings,
        connection=settings.pgvector_connection,
        collection_name=settings.consumer_collection,
        pre_delete_collection=reset,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Load markets_seed.json into PGVector")
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Drop the existing collection before loading",
    )
    args = parser.parse_args()

    settings = get_settings()
    seed_path = settings.markets_seed_path
    if not seed_path.exists():
        raise FileNotFoundError(f"Seed file not found: {seed_path}")

    docs = build_documents(seed_path)
    print(f"Loaded {len(docs)} market documents from {seed_path}")
    write_pgvector(docs, reset=args.reset)
    print(
        f"Written documents to PGVector collection "
        f"'{settings.consumer_collection}' @ {settings.pgvector_connection}"
    )


if __name__ == "__main__":
    main()


