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
    
    2025-11 업데이트: 운영시간, 가격대, 이벤트 날짜, 검색 태그 등 증강 필드 추가
    """

    market_id = record.get("market_id", "")
    name = record.get("market_name", "")
    desc = record.get("market_description", "")
    category = record.get("market_category", "")
    attrs: Iterable[str] = record.get("market_attribute", []) or []
    amenities: Iterable[str] = record.get("market_ameni", []) or []
    rating = record.get("market_rating")
    locations: list[dict] = record.get("market_location", []) or []

    # Take first location as primary (legacy format) or use direct fields
    primary_loc = locations[0] if locations else {}
    address = record.get("address") or primary_loc.get("address", "")
    distance_km = record.get("distance_km") or primary_loc.get("distance_km")
    zone_id = record.get("zone_id") or primary_loc.get("zone_id")
    lat = record.get("lat") or record.get("latitude") or primary_loc.get("latitude") or primary_loc.get("lat")
    lon = record.get("lon") or record.get("longitude") or primary_loc.get("longitude") or primary_loc.get("lon")

    # 증강 필드들
    operating_hours = record.get("operating_hours", {})
    operating_days = record.get("operating_days", [])
    price_range = record.get("price_range", "")
    event_dates = record.get("event_dates", [])
    event_type = record.get("event_type", "")
    search_tags = record.get("search_tags", [])
    contact_info = record.get("contact_info", {})
    sns_links = record.get("sns_links", {})
    facility_details = record.get("facility_details", {})

    text_lines: List[str] = []
    text_lines.append(f"[마켓 이름] {name}")
    text_lines.append(f"[카테고리] {category}")
    if attrs:
        text_lines.append(f"[분위기/특징] {', '.join(map(str, attrs))}")
    if amenities:
        text_lines.append(f"[편의시설] {', '.join(map(str, amenities))}")
    if address:
        text_lines.append(f"[주소] {address}")
    
    # 운영 정보 추가
    if operating_hours:
        weekday = operating_hours.get("weekday", "")
        weekend = operating_hours.get("weekend", "")
        if weekday or weekend:
            hours_str = f"평일 {weekday}" if weekday else ""
            if weekend:
                hours_str += f", 주말 {weekend}" if hours_str else f"주말 {weekend}"
            text_lines.append(f"[운영시간] {hours_str}")
    
    if operating_days:
        text_lines.append(f"[운영요일] {', '.join(operating_days)}")
    
    if price_range:
        text_lines.append(f"[가격대] {price_range}")
    
    # 이벤트/개최 일정 (플리마켓의 경우)
    if event_dates:
        upcoming = [e for e in event_dates[:3]]  # 최근 3개만
        if upcoming:
            dates_str = ", ".join([f"{e.get('date', '')}({e.get('day_of_week', '')})" for e in upcoming])
            text_lines.append(f"[개최일정] {dates_str}")
        if event_type:
            text_lines.append(f"[개최유형] {'정기' if event_type == 'regular' else '비정기'}")
    
    if distance_km is not None:
        text_lines.append(f"[기준 지점으로부터 거리(km)] {distance_km}")
    if rating is not None:
        text_lines.append(f"[평점(5점 만점)] {rating}")
    
    # 검색 태그 추가 (검색 정확도 향상)
    if search_tags:
        text_lines.append(f"[검색키워드] {', '.join(search_tags[:10])}")
    
    text_lines.append("")  # spacer
    text_lines.append("[상세 설명]")
    text_lines.append(desc)

    page_content = "\n".join(text_lines).strip()

    # 이미지 URL
    image_url = record.get("image_url", "")

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
        # 증강 메타데이터
        "operating_hours": operating_hours,
        "operating_days": operating_days,
        "price_range": price_range,
        "event_type": event_type,
        "search_tags": search_tags,
        "contact_phone": contact_info.get("phone", ""),
        "instagram": sns_links.get("instagram", ""),
        "image_url": image_url,
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


