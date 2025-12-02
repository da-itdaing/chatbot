#!/usr/bin/env python3
"""
스크래핑된 이벤트의 이미지를 다운로드하고 S3에 업로드하는 스크립트.

S3 경로 형식 (백엔드와 동일):
uploads/popup/0/{date}/{uuid}.{ext}
"""

from __future__ import annotations

import hashlib
import json
import os
import ssl
import time
import uuid
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

import boto3
import requests
import urllib3
from botocore.exceptions import ClientError
from requests.adapters import HTTPAdapter

# SSL 경고 비활성화
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# 경로 설정
SCRIPT_DIR = Path(__file__).resolve().parent
DATA_DIR = SCRIPT_DIR.parent / "data"
SCRAPED_FILE = DATA_DIR / "scraped_events.json"
OUTPUT_FILE = DATA_DIR / "events_with_s3_images.json"
LOCAL_IMAGE_DIR = DATA_DIR / "images"

# S3 설정
S3_BUCKET = "daitdaing-static-files"
S3_REGION = "ap-northeast-2"
S3_BASE_DIR = "uploads/popup/0"


class SSLAdapter(HTTPAdapter):
    """SSL 보안 수준을 낮춘 어댑터"""
    def init_poolmanager(self, *args, **kwargs):
        ctx = ssl.create_default_context()
        ctx.set_ciphers("DEFAULT@SECLEVEL=1")
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        kwargs["ssl_context"] = ctx
        return super().init_poolmanager(*args, **kwargs)


def get_session() -> requests.Session:
    """HTTP 세션 생성"""
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    })
    session.mount("https://", SSLAdapter())
    session.verify = False
    return session


def get_s3_client():
    """S3 클라이언트 생성"""
    return boto3.client("s3", region_name=S3_REGION)


def download_image(session: requests.Session, url: str, local_path: Path) -> bool:
    """이미지 다운로드"""
    try:
        resp = session.get(url, timeout=30, stream=True)
        resp.raise_for_status()
        
        with open(local_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=8192):
                f.write(chunk)
        
        return True
    except Exception as e:
        print(f"  다운로드 실패: {e}")
        return False


def get_content_type(file_path: Path) -> str:
    """파일 확장자로 Content-Type 결정"""
    ext = file_path.suffix.lower()
    content_types = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".gif": "image/gif",
        ".webp": "image/webp",
    }
    return content_types.get(ext, "image/jpeg")


def upload_to_s3(s3_client, local_path: Path, s3_key: str) -> str | None:
    """S3에 이미지 업로드"""
    try:
        content_type = get_content_type(local_path)
        
        s3_client.upload_file(
            str(local_path),
            S3_BUCKET,
            s3_key,
            ExtraArgs={
                "ContentType": content_type,
                "CacheControl": "public, max-age=31536000",
            }
        )
        
        # 퍼블릭 URL 생성
        s3_url = f"https://{S3_BUCKET}.s3.{S3_REGION}.amazonaws.com/{s3_key}"
        return s3_url
    except ClientError as e:
        print(f"  S3 업로드 실패: {e}")
        return None


def generate_s3_key(event_id: str, ext: str = ".jpg") -> str:
    """S3 키 생성"""
    date_str = datetime.now().strftime("%Y-%m-%d")
    unique_id = hashlib.md5(event_id.encode()).hexdigest()[:16]
    return f"{S3_BASE_DIR}/{date_str}/{unique_id}{ext}"


def process_events():
    """이벤트 이미지 처리"""
    # 데이터 로드
    with open(SCRAPED_FILE, "r", encoding="utf-8") as f:
        events = json.load(f)
    
    print(f"총 {len(events)}개 이벤트 처리")
    
    # 로컬 이미지 디렉토리 생성
    LOCAL_IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    
    # HTTP 세션 및 S3 클라이언트 생성
    session = get_session()
    
    # S3 클라이언트 (AWS 자격 증명 필요)
    try:
        s3_client = get_s3_client()
        # 버킷 접근 테스트
        s3_client.head_bucket(Bucket=S3_BUCKET)
        use_s3 = True
        print(f"S3 버킷 '{S3_BUCKET}' 접근 가능")
    except Exception as e:
        print(f"S3 접근 불가: {e}")
        print("로컬 다운로드만 수행합니다.")
        use_s3 = False
        s3_client = None
    
    # 각 이벤트 처리
    processed = []
    for i, event in enumerate(events):
        print(f"\n[{i+1}/{len(events)}] {event['title'][:40]}...")
        
        image_url = event.get("image_url", "")
        if not image_url:
            print("  이미지 URL 없음")
            processed.append(event)
            continue
        
        # 파일 확장자 추출
        parsed = urlparse(image_url)
        path_ext = Path(parsed.path).suffix.lower()
        ext = path_ext if path_ext in [".jpg", ".jpeg", ".png", ".gif", ".webp"] else ".jpg"
        
        # 로컬 파일 경로
        local_filename = f"{event['event_id']}{ext}"
        local_path = LOCAL_IMAGE_DIR / local_filename
        
        # 이미 다운로드된 경우 스킵
        if local_path.exists():
            print(f"  이미 다운로드됨: {local_filename}")
        else:
            # 이미지 다운로드
            if not download_image(session, image_url, local_path):
                processed.append(event)
                continue
            print(f"  다운로드 완료: {local_filename}")
        
        # S3 업로드
        if use_s3 and s3_client:
            s3_key = generate_s3_key(event["event_id"], ext)
            s3_url = upload_to_s3(s3_client, local_path, s3_key)
            
            if s3_url:
                event["s3_image_url"] = s3_url
                event["s3_image_key"] = s3_key
                print(f"  S3 업로드 완료: {s3_key}")
            else:
                # S3 실패시 원본 URL 유지
                event["s3_image_url"] = image_url
        else:
            # S3 미사용시 로컬 경로 저장
            event["local_image_path"] = str(local_path)
        
        processed.append(event)
        
        # 요청 간 딜레이
        time.sleep(0.3)
    
    # 결과 저장
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(processed, f, ensure_ascii=False, indent=2)
    
    print(f"\n\n처리 완료: {OUTPUT_FILE}")
    
    # 통계
    s3_count = sum(1 for e in processed if e.get("s3_image_url"))
    local_count = sum(1 for e in processed if e.get("local_image_path"))
    print(f"S3 업로드: {s3_count}건")
    print(f"로컬 저장: {local_count}건")


if __name__ == "__main__":
    process_events()

