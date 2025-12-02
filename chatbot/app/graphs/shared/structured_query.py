from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List, Literal, Sequence, Tuple

from langchain_core.documents import Document
from pydantic import BaseModel, Field


KeywordField = Literal[
    "market_category",
    "market_attribute",
    "market_ameni",
    "search_tags",  # 마켓 검색 태그 (야시장, 빈티지, 핸드메이드, 마켓 이름 등)
    "zone_style_tags",
    "allowed_categories",
    "search_keywords",
    "recommended_items_detail",
    "zone_type",
]

NumericField = Literal[
    "market_rating",
    "distance_km",
    "latitude",
    "longitude",
    "tag_count",
    "age_ratio_10s",
    "age_ratio_20s",
    "age_ratio_30s",
    "age_ratio_40s_plus",
    "group_ratio_couple",
    "group_ratio_family",
    "group_ratio_friends",
    "group_ratio_solo",
    "evening_peak_score",
    "night_peak_score",
]

SortField = Literal[
    "market_rating",
    "distance_km",
    "latitude",
    "longitude",
    "tag_count",
    "age_ratio_10s",
    "age_ratio_20s",
    "age_ratio_30s",
    "age_ratio_40s_plus",
]


class KeywordFilter(BaseModel):
    field: KeywordField
    include: List[str] = Field(default_factory=list)
    exclude: List[str] = Field(default_factory=list)
    match_any: bool = Field(
        default=True,
        description="True면 include 목록 중 하나라도 일치하면 통과, False면 모두 포함해야 함.",
    )


class NumericFilter(BaseModel):
    field: NumericField
    operator: Literal["gte", "lte", "gt", "lt", "eq"]
    value: float


class SortSpec(BaseModel):
    field: SortField
    direction: Literal["asc", "desc"] = "asc"


GWANGJU_DISTRICTS: Tuple[str, ...] = ("동구", "서구", "남구", "북구", "광산구")


class StructuredRetrievalPlan(BaseModel):
    """
    자연어 질문을 토대로 필터/정렬 전략을 담는 스키마.
    """

    target_entity: Literal["zone", "store", "either"] = "either"
    keyword_filters: List[KeywordFilter] = Field(default_factory=list)
    numeric_filters: List[NumericFilter] = Field(default_factory=list)
    exclude_districts: List[str] = Field(
        default_factory=list,
        description="제외할 광주광역시 구 목록 (동구, 서구, 남구, 북구, 광산구)",
    )
    sort: List[SortSpec] = Field(default_factory=list)
    allow_broadening: bool = True
    strict_filters: bool = False
    rationale: str = ""
    risk_level: Literal["low", "medium", "high"] = "low"


@dataclass
class FilterResult:
    documents: List[Document]
    label: Literal["exact_match", "broadened", "no_match"]


def _normalize_tokens(values: Iterable[str | float | int | None]) -> List[str]:
    tokens: List[str] = []
    for value in values:
        if value is None:
            continue
        if isinstance(value, (int, float)):
            tokens.append(str(value))
            continue
        token = str(value).strip()
        if token:
            tokens.append(token.lower())
    return tokens


def _metadata_tokens(metadata: dict, field: str) -> List[str]:
    raw = metadata.get(field)
    if raw is None:
        return []
    if isinstance(raw, str):
        return _normalize_tokens([raw])
    if isinstance(raw, (list, tuple, set)):
        return _normalize_tokens(raw)
    return _normalize_tokens([raw])


def _metadata_number(metadata: dict, field: str) -> float | None:
    value = metadata.get(field)
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _normalize_district_name(value: str | None) -> str | None:
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    for district in GWANGJU_DISTRICTS:
        if district in text:
            return district
    return None


def _metadata_district(metadata: dict) -> str | None:
    candidate_keys = (
        "district",
        "market_district",
        "zone_district",
        "region",
        "area",
    )
    for key in candidate_keys:
        district = _normalize_district_name(metadata.get(key))
        if district:
            return district
    address_fields = (
        "address",
        "market_address",
        "zone_address",
        "location",
    )
    for key in address_fields:
        address = metadata.get(key)
        if isinstance(address, str):
            district = _normalize_district_name(address)
            if district:
                return district
    return None


def _match_keyword_filter(metadata: dict, flt: KeywordFilter) -> bool:
    tokens = _metadata_tokens(metadata, flt.field)
    if flt.include:
        includes = [token.lower() for token in flt.include if token.strip()]
        if includes:
            if flt.match_any:
                if not any(any_term in tokens for any_term in includes):
                    return False
            else:
                if not all(all_term in tokens for all_term in includes):
                    return False
    if flt.exclude:
        excludes = [token.lower() for token in flt.exclude if token.strip()]
        for token in excludes:
            if token in tokens:
                return False
    return True


def _match_numeric_filter(metadata: dict, flt: NumericFilter) -> bool:
    value = _metadata_number(metadata, flt.field)
    if value is None:
        return False
    target = flt.value
    op = flt.operator
    if op == "gte":
        return value >= target
    if op == "gt":
        return value > target
    if op == "lte":
        return value <= target
    if op == "lt":
        return value < target
    if op == "eq":
        return abs(value - target) < 1e-6
    return False


def _apply_keyword_filters(documents: Sequence[Document], plan: StructuredRetrievalPlan) -> List[Document]:
    if not plan.keyword_filters:
        return list(documents)
    filtered: List[Document] = []
    for doc in documents:
        metadata = doc.metadata or {}
        passed = True
        for flt in plan.keyword_filters:
            if not _match_keyword_filter(metadata, flt):
                passed = False
                break
        if passed:
            filtered.append(doc)
    return filtered


def _apply_numeric_filters(documents: Sequence[Document], plan: StructuredRetrievalPlan) -> List[Document]:
    if not plan.numeric_filters:
        return list(documents)
    filtered: List[Document] = []
    for doc in documents:
        metadata = doc.metadata or {}
        passed = True
        for flt in plan.numeric_filters:
            if not _match_numeric_filter(metadata, flt):
                passed = False
                break
        if passed:
            filtered.append(doc)
    return filtered


def _apply_district_exclusions(
    documents: Sequence[Document],
    plan: StructuredRetrievalPlan,
) -> List[Document]:
    if not plan.exclude_districts:
        return list(documents)
    excluded = {
        district
        for district in (_normalize_district_name(value) for value in plan.exclude_districts)
        if district
    }
    if not excluded:
        return list(documents)
    filtered: List[Document] = []
    for doc in documents:
        metadata = doc.metadata or {}
        district = _metadata_district(metadata)
        if district and district in excluded:
            continue
        filtered.append(doc)
    return filtered


def _apply_sort(documents: Sequence[Document], plan: StructuredRetrievalPlan) -> List[Document]:
    if not plan.sort:
        return list(documents)

    def _sort_key(field: SortSpec):
        def key(doc: Document) -> Tuple[bool, float]:
            value = _metadata_number(doc.metadata or {}, field.field)
            if value is None:
                return (True, 0.0)
            return (False, value)

        return key

    sorted_docs = list(documents)
    # 다중 정렬 지원: 뒤에서부터 적용
    for spec in reversed(plan.sort):
        reverse = spec.direction == "desc"
        sorted_docs.sort(key=_sort_key(spec), reverse=reverse)
    return sorted_docs


def apply_structured_plan(
    documents: Sequence[Document],
    plan: StructuredRetrievalPlan | None,
) -> FilterResult:
    if not plan:
        return FilterResult(list(documents), "exact_match")

    staged = list(documents)
    staged = _apply_keyword_filters(staged, plan)
    staged = _apply_numeric_filters(staged, plan)
    staged = _apply_district_exclusions(staged, plan)

    if not staged:
        if plan.allow_broadening:
            return FilterResult(list(documents), "broadened")
        return FilterResult([], "no_match")

    staged = _apply_sort(staged, plan)
    return FilterResult(staged, "exact_match")


__all__ = [
    "StructuredRetrievalPlan",
    "KeywordFilter",
    "NumericFilter",
    "SortSpec",
    "FilterResult",
    "apply_structured_plan",
]

