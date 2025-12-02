"""
한국시간(KST) 처리 유틸리티.

챗봇에서 "오늘", "이번 주", "주말" 등 시간 관련 질문을 처리할 때 사용.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

# 한국 표준시 (UTC+9)
KST = ZoneInfo("Asia/Seoul")


def now_kst() -> datetime:
    """현재 한국시간 반환."""
    return datetime.now(KST)


def today_kst() -> datetime:
    """오늘 날짜 (한국시간 기준, 시간은 00:00:00)."""
    return now_kst().replace(hour=0, minute=0, second=0, microsecond=0)


def get_current_time_context() -> dict:
    """
    현재 시간 컨텍스트를 딕셔너리로 반환.
    프롬프트에 주입하여 시간 관련 질문에 정확히 답변하도록 함.
    """
    now = now_kst()
    
    # 요일 (0=월요일, 6=일요일)
    weekday = now.weekday()
    weekday_names = ["월요일", "화요일", "수요일", "목요일", "금요일", "토요일", "일요일"]
    
    # 이번 주 주말 계산
    days_until_saturday = (5 - weekday) % 7
    days_until_sunday = (6 - weekday) % 7
    
    # 이미 주말이면 오늘/내일
    if weekday == 5:  # 토요일
        this_saturday = now.date()
        this_sunday = (now + timedelta(days=1)).date()
    elif weekday == 6:  # 일요일
        this_saturday = (now - timedelta(days=1)).date()
        this_sunday = now.date()
    else:
        this_saturday = (now + timedelta(days=days_until_saturday)).date()
        this_sunday = (now + timedelta(days=days_until_sunday)).date()
    
    return {
        "current_datetime": now.strftime("%Y-%m-%d %H:%M:%S KST"),
        "current_date": now.strftime("%Y년 %m월 %d일"),
        "current_time": now.strftime("%H:%M"),
        "weekday": weekday_names[weekday],
        "is_weekend": weekday >= 5,
        "this_saturday": this_saturday.strftime("%Y-%m-%d"),
        "this_sunday": this_sunday.strftime("%Y-%m-%d"),
    }


def format_time_context_for_prompt() -> str:
    """
    프롬프트에 삽입할 시간 컨텍스트 문자열 생성.
    """
    ctx = get_current_time_context()
    
    weekend_status = "오늘은 주말입니다" if ctx["is_weekend"] else f"이번 주 주말: {ctx['this_saturday']} ~ {ctx['this_sunday']}"
    
    return f"""현재 시간 (한국시간 KST):
- 날짜: {ctx['current_date']} ({ctx['weekday']})
- 시간: {ctx['current_time']}
- {weekend_status}"""


__all__ = [
    "KST",
    "now_kst",
    "today_kst",
    "get_current_time_context",
    "format_time_context_for_prompt",
]

