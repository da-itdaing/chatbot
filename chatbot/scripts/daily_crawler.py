#!/usr/bin/env python3
"""
매일 자동으로 playgwangju.co.kr에서 데이터를 수집하고
PGVector를 업데이트하는 스케줄러.

실행 방법:
  1. 직접 실행: python scripts/daily_crawler.py
  2. systemd 서비스로 등록 (권장)
  3. cron으로 등록

스케줄: 매일 KST 06:00 (서버 부하가 적은 새벽 시간)
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

# 프로젝트 루트를 path에 추가
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# 로깅 설정
LOG_DIR = PROJECT_ROOT / "logs"
LOG_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / "daily_crawler.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger(__name__)

# 스케줄 설정 (KST 기준)
SCHEDULE_HOUR_KST = 6  # 매일 새벽 6시
SCHEDULE_MINUTE_KST = 0

# 상태 파일
STATE_FILE = PROJECT_ROOT / "data" / "crawler_state.json"


def load_state() -> dict:
    """크롤러 상태 로드"""
    if STATE_FILE.exists():
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"last_run": None, "last_success": None, "run_count": 0, "error_count": 0}


def save_state(state: dict) -> None:
    """크롤러 상태 저장"""
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def get_kst_now() -> datetime:
    """현재 KST 시간 반환"""
    from zoneinfo import ZoneInfo
    return datetime.now(ZoneInfo("Asia/Seoul"))


def get_next_run_time() -> datetime:
    """다음 실행 시간 계산 (KST)"""
    from zoneinfo import ZoneInfo
    
    kst = ZoneInfo("Asia/Seoul")
    now = datetime.now(kst)
    
    # 오늘 예정 시간
    today_scheduled = now.replace(
        hour=SCHEDULE_HOUR_KST,
        minute=SCHEDULE_MINUTE_KST,
        second=0,
        microsecond=0,
    )
    
    # 이미 지났으면 내일
    if now >= today_scheduled:
        return today_scheduled + timedelta(days=1)
    return today_scheduled


async def run_crawler() -> bool:
    """크롤러 실행"""
    logger.info("=" * 60)
    logger.info("크롤러 실행 시작")
    logger.info("=" * 60)
    
    try:
        # 1. 스크래핑
        logger.info("[1/4] playgwangju.co.kr 스크래핑 시작...")
        from scripts.scrape_playgwangju import scrape_all_events, save_events, OUTPUT_FILE
        
        events = scrape_all_events()
        if not events:
            logger.warning("수집된 이벤트가 없습니다.")
            return False
        
        save_events(events, OUTPUT_FILE)
        logger.info(f"  수집 완료: {len(events)}개 이벤트")
        
        # 2. 이미지 다운로드 및 S3 업로드
        logger.info("[2/4] 이미지 처리 중...")
        from scripts.download_and_upload_images import process_events
        process_events()
        
        # 3. markets_seed.json 재구성
        logger.info("[3/4] markets_seed.json 생성 중...")
        from scripts.build_markets_seed import main as build_seed
        build_seed()
        
        # 4. 좌표 수정
        logger.info("[4/4] 좌표 수정 및 PGVector 적재 중...")
        from scripts.fix_coordinates import main as fix_coords
        fix_coords()
        
        # 5. PGVector 적재
        # 환경 변수 로드
        env_file = PROJECT_ROOT / "chatbot.env"
        if env_file.exists():
            from dotenv import load_dotenv
            load_dotenv(env_file)
        
        # markets_loader 실행
        import subprocess
        result = subprocess.run(
            [
                sys.executable, "-m", "app.data.markets_loader", "--reset"
            ],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            env={**os.environ},
        )
        
        if result.returncode != 0:
            logger.error(f"PGVector 적재 실패: {result.stderr}")
            return False
        
        logger.info(f"  PGVector 적재 완료: {result.stdout.strip()}")
        
        logger.info("=" * 60)
        logger.info("크롤러 실행 완료")
        logger.info("=" * 60)
        return True
        
    except Exception as e:
        logger.exception(f"크롤러 실행 중 오류: {e}")
        return False


async def scheduler_loop():
    """스케줄러 메인 루프"""
    logger.info("크롤러 스케줄러 시작")
    logger.info(f"예정 실행 시간: 매일 KST {SCHEDULE_HOUR_KST:02d}:{SCHEDULE_MINUTE_KST:02d}")
    
    state = load_state()
    
    # 시작 시 즉시 한 번 실행 (마지막 실행이 24시간 이상 전인 경우)
    last_success = state.get("last_success")
    should_run_now = True
    
    if last_success:
        try:
            last_dt = datetime.fromisoformat(last_success)
            if (datetime.now() - last_dt.replace(tzinfo=None)).total_seconds() < 86400:
                should_run_now = False
                logger.info(f"마지막 성공: {last_success}, 24시간 이내이므로 스킵")
        except Exception:
            pass
    
    if should_run_now:
        logger.info("시작 시 즉시 실행")
        success = await run_crawler()
        state["last_run"] = datetime.now().isoformat()
        state["run_count"] = state.get("run_count", 0) + 1
        if success:
            state["last_success"] = datetime.now().isoformat()
        else:
            state["error_count"] = state.get("error_count", 0) + 1
        save_state(state)
    
    # 스케줄 루프
    while True:
        next_run = get_next_run_time()
        now = get_kst_now()
        wait_seconds = (next_run - now).total_seconds()
        
        logger.info(f"다음 실행: {next_run.strftime('%Y-%m-%d %H:%M:%S KST')} ({wait_seconds/3600:.1f}시간 후)")
        
        # 대기
        await asyncio.sleep(wait_seconds)
        
        # 실행
        success = await run_crawler()
        state["last_run"] = datetime.now().isoformat()
        state["run_count"] = state.get("run_count", 0) + 1
        if success:
            state["last_success"] = datetime.now().isoformat()
        else:
            state["error_count"] = state.get("error_count", 0) + 1
        save_state(state)


def main():
    """메인 함수"""
    # 시그널 핸들러 설정
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    def shutdown(sig, frame):
        logger.info(f"종료 시그널 수신: {sig}")
        loop.stop()
        sys.exit(0)
    
    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)
    
    try:
        loop.run_until_complete(scheduler_loop())
    except KeyboardInterrupt:
        logger.info("키보드 인터럽트로 종료")
    finally:
        loop.close()


if __name__ == "__main__":
    main()

