"""
OpenAI API 키 로테이션 유틸리티

여러 API 키를 관리하고, 레이트 리밋이나 에러 발생 시 자동으로 다음 키로 전환합니다.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Optional

from openai import APIError, RateLimitError

logger = logging.getLogger(__name__)


@dataclass
class KeyStatus:
    """개별 키 상태 관리"""
    key: str
    is_active: bool = True
    error_count: int = 0
    last_error_time: Optional[float] = None
    cooldown_until: Optional[float] = None
    
    # 키 마스킹 (로깅용)
    @property
    def masked_key(self) -> str:
        if len(self.key) > 10:
            return f"{self.key[:8]}...{self.key[-4:]}"
        return "***"


@dataclass
class KeyRotationManager:
    """
    API 키 로테이션 관리자
    
    - 라운드 로빈 방식으로 키 분배
    - 에러 발생 시 쿨다운 적용
    - 모든 키가 쿨다운 중이면 가장 빨리 복구되는 키 사용
    """
    keys: list[KeyStatus] = field(default_factory=list)
    current_index: int = 0
    
    # 설정
    max_errors_before_cooldown: int = 3  # 쿨다운 전 허용 에러 수
    cooldown_seconds: int = 60  # 쿨다운 시간 (초)
    error_reset_seconds: int = 300  # 에러 카운트 리셋 시간 (초)
    
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    
    def __post_init__(self):
        if not self.keys:
            raise ValueError("최소 하나의 API 키가 필요합니다")
    
    @classmethod
    def from_keys(cls, api_keys: list[str]) -> "KeyRotationManager":
        """API 키 목록으로 매니저 생성"""
        if not api_keys:
            raise ValueError("최소 하나의 API 키가 필요합니다")
        
        keys = [KeyStatus(key=k) for k in api_keys if k]
        return cls(keys=keys)
    
    def get_current_key(self) -> str:
        """현재 활성 키 반환 (라운드 로빈)"""
        now = time.time()
        
        # 모든 키를 순회하며 사용 가능한 키 찾기
        for _ in range(len(self.keys)):
            key_status = self.keys[self.current_index]
            
            # 쿨다운 체크
            if key_status.cooldown_until and now < key_status.cooldown_until:
                self._rotate_index()
                continue
            
            # 쿨다운 해제
            if key_status.cooldown_until and now >= key_status.cooldown_until:
                key_status.cooldown_until = None
                key_status.error_count = 0
                key_status.is_active = True
                logger.info(f"키 쿨다운 해제: {key_status.masked_key}")
            
            return key_status.key
        
        # 모든 키가 쿨다운 중이면 가장 빨리 복구되는 키 사용
        earliest_recovery = min(
            self.keys, 
            key=lambda k: k.cooldown_until or 0
        )
        logger.warning(
            f"모든 키가 쿨다운 중. 가장 빨리 복구되는 키 사용: {earliest_recovery.masked_key}"
        )
        return earliest_recovery.key
    
    async def get_current_key_async(self) -> str:
        """비동기 버전 - 현재 활성 키 반환"""
        async with self._lock:
            return self.get_current_key()
    
    def report_success(self) -> None:
        """API 호출 성공 보고"""
        key_status = self.keys[self.current_index]
        
        # 오래된 에러는 리셋
        now = time.time()
        if (key_status.last_error_time and 
            now - key_status.last_error_time > self.error_reset_seconds):
            key_status.error_count = 0
        
        self._rotate_index()
    
    async def report_success_async(self) -> None:
        """비동기 버전 - API 호출 성공 보고"""
        async with self._lock:
            self.report_success()
    
    def report_error(self, error: Exception) -> None:
        """API 호출 에러 보고"""
        key_status = self.keys[self.current_index]
        now = time.time()
        
        key_status.error_count += 1
        key_status.last_error_time = now
        
        # 레이트 리밋 에러는 즉시 쿨다운
        if isinstance(error, RateLimitError):
            key_status.cooldown_until = now + self.cooldown_seconds
            key_status.is_active = False
            logger.warning(
                f"레이트 리밋 발생. 키 쿨다운: {key_status.masked_key} "
                f"({self.cooldown_seconds}초)"
            )
        elif key_status.error_count >= self.max_errors_before_cooldown:
            key_status.cooldown_until = now + self.cooldown_seconds
            key_status.is_active = False
            logger.warning(
                f"연속 에러 {key_status.error_count}회. 키 쿨다운: {key_status.masked_key}"
            )
        
        self._rotate_index()
    
    async def report_error_async(self, error: Exception) -> None:
        """비동기 버전 - API 호출 에러 보고"""
        async with self._lock:
            self.report_error(error)
    
    def _rotate_index(self) -> None:
        """다음 키로 인덱스 이동"""
        self.current_index = (self.current_index + 1) % len(self.keys)
    
    def get_status_summary(self) -> dict:
        """키 상태 요약 반환 (디버깅/모니터링용)"""
        now = time.time()
        return {
            "total_keys": len(self.keys),
            "active_keys": sum(1 for k in self.keys if k.is_active),
            "current_index": self.current_index,
            "keys": [
                {
                    "masked_key": k.masked_key,
                    "is_active": k.is_active,
                    "error_count": k.error_count,
                    "cooldown_remaining": (
                        max(0, k.cooldown_until - now) 
                        if k.cooldown_until else 0
                    ),
                }
                for k in self.keys
            ],
        }


# 전역 키 매니저 (싱글톤)
_key_manager: Optional[KeyRotationManager] = None


def get_key_manager() -> KeyRotationManager:
    """전역 키 매니저 반환 (지연 초기화)"""
    global _key_manager
    if _key_manager is None:
        from app.config import get_settings
        settings = get_settings()
        _key_manager = KeyRotationManager.from_keys(settings.openai_api_keys)
        logger.info(
            f"KeyRotationManager 초기화: {len(_key_manager.keys)}개 키 등록"
        )
    return _key_manager


def reset_key_manager() -> None:
    """키 매니저 리셋 (테스트용)"""
    global _key_manager
    _key_manager = None


__all__ = [
    "KeyRotationManager",
    "KeyStatus",
    "get_key_manager",
    "reset_key_manager",
]

