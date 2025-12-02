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
from typing import Iterable, List, Tuple

from dotenv import load_dotenv
from langchain_core.documents import Document
from langchain_openai import OpenAIEmbeddings
from langchain_postgres.vectorstores import PGVector

from app.config import ROOT_DIR, get_settings


load_dotenv(ROOT_DIR / "chatbot.env")


def _load_json(path: Path) -> list[dict]:
    raw = path.read_text(encoding="utf-8")
    return json.loads(raw)


def _to_float(value) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _time_windows(pattern: dict) -> List[Tuple[int, int]]:
    windows: List[Tuple[int, int]] = []
    slots: List[str] = []
    slots.extend(pattern.get("weekday_peak_times") or [])
    slots.extend(pattern.get("weekend_peak_times") or [])
    for slot in slots:
        if not isinstance(slot, str) or "-" not in slot:
            continue
        start_text, end_text = slot.split("-", 1)
        try:
            start_hour = int(start_text.split(":")[0])
            end_hour = int(end_text.split(":")[0])
        except ValueError:
            continue
        windows.append((start_hour, end_hour))
    return windows


def _peak_score(pattern: dict, threshold: int) -> float:
    windows = _time_windows(pattern)
    if not windows:
        return 0.0
    hits = 0
    for start_hour, end_hour in windows:
        if end_hour >= threshold or start_hour >= threshold:
            hits += 1
    return hits / len(windows)


def _zone_to_document(zone: dict) -> Document:
    """
    2025-11 업데이트: 대여정보, 유동인구, 셀러지원 등 증강 필드 추가
    """
    zone_id = zone.get("zone_id", "")
    name = zone.get("zone_name", "")
    description = zone.get("zone_description", "")
    
    # 위치 정보 (기존 형식 + 새 형식 모두 지원)
    locations = zone.get("zone_location", []) or []
    primary_loc = locations[0] if locations else {}
    address = zone.get("address", "") or primary_loc.get("address", "")
    latitude = zone.get("latitude") or primary_loc.get("latitude")
    longitude = zone.get("longitude") or primary_loc.get("longitude")
    
    zone_type = zone.get("zone_type", "")
    style_tags: Iterable[str] = zone.get("zone_style_tags", []) or []
    allowed_categories: Iterable[str] = zone.get("allowed_categories", []) or []

    visitor_profile = zone.get("visitor_profile", {}) or {}
    age_ratio = visitor_profile.get("age_group_ratio", {}) or {}
    group_ratio = visitor_profile.get("group_type_ratio", {}) or {}

    time_pattern = zone.get("time_pattern", {}) or {}
    long_description = zone.get("long_description", "") or description
    commercial_insight = zone.get("commercial_insight", {}) or {}

    search_keywords: Iterable[str] = zone.get("search_keywords", []) or []
    recommended_items: Iterable[str] = zone.get("recommended_items_detail", []) or []
    
    # 증강 필드들
    rental_info = zone.get("rental_info", {}) or {}
    foot_traffic = zone.get("foot_traffic", {}) or {}
    nearby_amenities = zone.get("nearby_amenities", []) or []
    success_stories = zone.get("success_stories", []) or []
    recommended_categories = zone.get("recommended_categories", []) or []
    seasonal_info = zone.get("seasonal_info", {}) or {}
    seller_support = zone.get("seller_support", {}) or {}
    booking_info = zone.get("booking_info", {}) or {}
    search_tags = zone.get("search_tags", []) or []

    lines: List[str] = []
    lines.append(f"[존 이름] {name}")
    if zone_type:
    lines.append(f"[존 유형] {zone_type}")
    if address:
        lines.append(f"[주소] {address}")
    if style_tags:
        lines.append(f"[스타일 태그] {', '.join(map(str, style_tags))}")
    if allowed_categories:
        lines.append(f"[허용 업종] {', '.join(map(str, allowed_categories))}")
    
    # 추천 카테고리 (증강)
    if recommended_categories:
        lines.append(f"[추천 판매 카테고리] {', '.join(recommended_categories)}")
    
    # 대여 정보 (증강)
    if rental_info:
        prices = rental_info.get("prices", {})
        if prices:
            small_price = prices.get("소형", {}).get("daily", "")
            if small_price:
                lines.append(f"[부스 대여료] 소형 기준 {small_price}/일")
        includes = rental_info.get("includes", [])
        if includes:
            lines.append(f"[대여 포함사항] {', '.join(includes)}")
    
    # 유동인구 (증강)
    if foot_traffic:
        weekday_avg = foot_traffic.get("weekday_avg")
        weekend_avg = foot_traffic.get("weekend_avg")
        if weekday_avg or weekend_avg:
            traffic_str = f"평일 약 {weekday_avg:,}명" if weekday_avg else ""
            if weekend_avg:
                traffic_str += f", 주말 약 {weekend_avg:,}명" if traffic_str else f"주말 약 {weekend_avg:,}명"
            lines.append(f"[유동인구] {traffic_str}")
        peak_days = foot_traffic.get("peak_days", [])
        if peak_days:
            lines.append(f"[피크 요일] {', '.join(peak_days)}")
    
    # 셀러 지원 (증강)
    if seller_support:
        support_items = []
        if seller_support.get("orientation"):
            support_items.append("오리엔테이션")
        if seller_support.get("equipment_rental"):
            support_items.append("장비대여")
        if seller_support.get("marketing_support") and seller_support.get("marketing_support") != "없음":
            support_items.append(seller_support.get("marketing_support"))
        if support_items:
            lines.append(f"[셀러 지원] {', '.join(support_items)}")
    
    # 기존 필드들
    if age_ratio:
    lines.append(f"[연령 비중] {age_ratio}")
    if group_ratio:
    lines.append(f"[동행 형태 비중] {group_ratio}")
    if time_pattern:
    lines.append(f"[피크 타임] {time_pattern}")
    if commercial_insight:
    lines.append(f"[상권 인사이트] {commercial_insight}")
    if search_keywords:
        lines.append(f"[검색 키워드] {', '.join(map(str, search_keywords))}")
    if recommended_items:
        lines.append(f"[추천 상품/서비스] {', '.join(map(str, recommended_items))}")
    
    # 검색 태그 (증강)
    if search_tags:
        lines.append(f"[검색태그] {', '.join(search_tags[:10])}")
    
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
        # 증강 메타데이터
        "recommended_categories": recommended_categories,
        "search_tags": search_tags,
    }

    metadata.update(
        {
            "tag_count": len(list(style_tags)),
            "age_ratio_10s": _to_float(age_ratio.get("10s")) or 0.0,
            "age_ratio_20s": _to_float(age_ratio.get("20s")) or 0.0,
            "age_ratio_30s": _to_float(age_ratio.get("30s")) or 0.0,
            "age_ratio_40s_plus": _to_float(age_ratio.get("40s_plus")) or 0.0,
            "group_ratio_couple": _to_float(group_ratio.get("couple")) or 0.0,
            "group_ratio_family": _to_float(group_ratio.get("family")) or 0.0,
            "group_ratio_friends": _to_float(group_ratio.get("friends")) or 0.0,
            "group_ratio_solo": _to_float(group_ratio.get("solo")) or 0.0,
            "evening_peak_score": _peak_score(time_pattern, 18),
            "night_peak_score": _peak_score(time_pattern, 21),
            "time_pattern_raw": time_pattern,
            "search_keywords": list(search_keywords),
            "recommended_items_detail": list(recommended_items),
        }
    )

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


