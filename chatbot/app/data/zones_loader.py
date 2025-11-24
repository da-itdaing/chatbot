from __future__ import annotations

"""
Loader script for seller zone RAG data.

Usage:

    . .venv/bin/activate
    python -m app.data.zones_loader --reset

This reads `zones_seed.json` and stores zone summaries into a PGVector
collection suitable for the seller LangGraph.
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


load_dotenv(ROOT_DIR / "chatbot.env")


def _load_json(path: Path) -> list[dict]:
    raw = path.read_text(encoding="utf-8")
    return json.loads(raw)


def _zone_to_document(zone: dict) -> Document:
    zone_id = zone.get("zone_id", "")
    name = zone.get("zone_name", "")
    address = zone.get("address", "")
    zone_type = zone.get("zone_type", "")
    style_tags: Iterable[str] = zone.get("zone_style_tags", []) or []
    allowed_categories: Iterable[str] = zone.get("allowed_categories", []) or []
    latitude = zone.get("latitude")
    longitude = zone.get("longitude")

    visitor_profile = zone.get("visitor_profile", {}) or {}
    age_ratio = visitor_profile.get("age_group_ratio", {}) or {}
    group_ratio = visitor_profile.get("group_type_ratio", {}) or {}

    time_pattern = zone.get("time_pattern", {}) or {}
    long_description = zone.get("long_description", "")
    commercial_insight = zone.get("commercial_insight", {}) or {}

    search_keywords: Iterable[str] = zone.get("search_keywords", []) or []
    recommended_items: Iterable[str] = zone.get("recommended_items_detail", []) or []

    lines: List[str] = []
    lines.append(f"[존 이름] {name}")
    lines.append(f"[존 유형] {zone_type}")
    if address:
        lines.append(f"[주소] {address}")
    if style_tags:
        lines.append(f"[스타일 태그] {', '.join(map(str, style_tags))}")
    if allowed_categories:
        lines.append(f"[허용 업종] {', '.join(map(str, allowed_categories))}")
    lines.append(f"[연령 비중] {age_ratio}")
    lines.append(f"[동행 형태 비중] {group_ratio}")
    lines.append(f"[피크 타임] {time_pattern}")
    lines.append(f"[상권 인사이트] {commercial_insight}")
    if search_keywords:
        lines.append(f"[검색 키워드] {', '.join(map(str, search_keywords))}")
    if recommended_items:
        lines.append(f"[추천 상품/서비스] {', '.join(map(str, recommended_items))}")
    lines.append("")
    lines.append("[상세 설명]")
    lines.append(long_description)

    page_content = "\n".join(lines).strip()

    metadata = {
        "zone_id": zone_id,
        "zone_name": name,
        "zone_type": zone_type,
        "address": address,
        "zone_style_tags": list(style_tags),
        "allowed_categories": list(allowed_categories),
        "latitude": latitude,
        "longitude": longitude,
    }

    return Document(page_content=page_content, metadata=metadata)


def build_documents(seed_path: Path) -> list[Document]:
    zones = _load_json(seed_path)
    return [_zone_to_document(z) for z in zones]


def write_pgvector(documents: list[Document], *, reset: bool) -> None:
    settings = get_settings()
    conn = settings.pgvector_zone_connection or settings.pgvector_connection
    embeddings = OpenAIEmbeddings(model=settings.openai_embedding_model)

    PGVector.from_documents(
        documents=documents,
        embedding=embeddings,
        connection=conn,
        collection_name=settings.seller_zone_collection,
        pre_delete_collection=reset,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Load zones_seed.json into PGVector")
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Drop the existing zone collection before loading",
    )
    args = parser.parse_args()

    settings = get_settings()
    seed_path = settings.zones_seed_path
    if not seed_path.exists():
        raise FileNotFoundError(f"Seed file not found: {seed_path}")

    docs = build_documents(seed_path)
    print(f"Loaded {len(docs)} zone documents from {seed_path}")
    write_pgvector(docs, reset=args.reset)
    print(
        f"Written documents to PGVector collection "
        f"'{settings.seller_zone_collection}'"
    )


if __name__ == "__main__":
    main()


