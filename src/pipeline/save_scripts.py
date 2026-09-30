"""
filtered_channel_video_urls.txt 에 있는 유튜브 영상들의 자막(스크립트)을
transcripts/ 폴더에 "{순번}. {영상 제목}.txt" 형식으로 저장하는 스크립트.

사용법:
    1) pip install youtube-transcript-api
    2) 이 파일과 같은 위치에 filtered_channel_video_urls.txt (한 줄에 URL 하나씩) 준비
    3) python save_transcripts.py

- 200개 규모 작업을 감안해 진행상황을 transcripts/_progress.json 에 저장합니다.
  중간에 끊기거나 에러가 나도 다시 실행하면 이미 성공한 영상은 건너뛰고
  이어서 처리합니다.
- 프록시(Webshare 등)가 필요하면 build_api() 안의 주석 처리된 부분을
  실제 자격증명으로 채워서 사용하세요.
"""

import os
import re
import json
import time
from urllib.request import Request, urlopen
from urllib.parse import quote

from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api._errors import (
    TranscriptsDisabled,
    NoTranscriptFound,
    VideoUnavailable,
)

# 프록시를 쓸 경우에만 주석 해제
# from youtube_transcript_api.proxies import WebshareProxyConfig


# 실행 위치(cwd)와 무관하게, 항상 "이 스크립트 파일이 있는 폴더"를 기준으로 경로를 잡는다.
# (터미널을 다른 폴더에서 열고 실행하면 상대경로만으로는 파일을 못 찾는 문제 방지)
from pathlib import Path

# 프로젝트 루트(sesac-final-project/) 기준으로 경로를 잡는다. 실행 위치(cwd)와 무관하게 동작.
ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "data"
URLS_DIR = DATA_DIR / "urls"

INPUT_FILE = str(URLS_DIR / "filtered_channel_video_urls.txt")
OUTPUT_DIR = str(DATA_DIR / "transcripts")
PROGRESS_FILE = os.path.join(OUTPUT_DIR, "_progress.json")

REQUEST_DELAY_SEC = 1.0  # 영상 사이 딜레이(레이트리밋/차단 방지용, 필요시 조정)
MAX_RETRIES = 2          # 일시적 오류(네트워크 등) 재시도 횟수


def get_video_id(url: str) -> str:
    """유튜브 URL에서 Video ID 추출 (watch, youtu.be, shorts 형태 모두 지원)"""
    match = re.search(r'(?:v=|youtu\.be/|shorts/)([0-9A-Za-z_-]{11})', url)
    return match.group(1) if match else url.strip()


def get_video_title(video_id: str) -> str:
    """API 키 없이 oEmbed 엔드포인트로 영상 제목 가져오기. 실패 시 video_id로 대체."""
    watch_url = f"https://www.youtube.com/watch?v={video_id}"
    oembed_url = f"https://www.youtube.com/oembed?url={quote(watch_url)}&format=json"
    try:
        req = Request(oembed_url, headers={"User-Agent": "Mozilla/5.0"})
        with urlopen(req, timeout=10) as res:
            data = json.loads(res.read().decode("utf-8"))
            title = data.get("title", "").strip()
            return title if title else video_id
    except Exception:
        return video_id


def sanitize_filename(name: str) -> str:
    """윈도우/맥/리눅스에서 공통으로 파일명에 쓸 수 없는 문자를 제거하고 길이를 제한"""
    name = re.sub(r'[\\/*?:"<>|]', '', name)
    name = re.sub(r'\s+', ' ', name).strip().rstrip('.')
    return name[:150] if name else ""


def load_progress() -> dict:
    if os.path.exists(PROGRESS_FILE):
        with open(PROGRESS_FILE, "r", encoding="utf-8") as f:
            try:
                return json.load(f)
            except json.JSONDecodeError:
                return {}
    return {}


def save_progress(progress: dict) -> None:
    with open(PROGRESS_FILE, "w", encoding="utf-8") as f:
        json.dump(progress, f, ensure_ascii=False, indent=2)


def build_api() -> YouTubeTranscriptApi:
    # 프록시가 필요한 경우 아래 주석을 해제하고 Webshare "Residential" 플랜의
    # 실제 Proxy Username / Password를 넣어서 사용하세요.
    # return YouTubeTranscriptApi(
    #     proxy_config=WebshareProxyConfig(
    #         proxy_username="실제_프록시_유저네임",
    #         proxy_password="실제_프록시_비밀번호",
    #     )
    # )
    return YouTubeTranscriptApi()


def fetch_transcript_text(ytt_api, video_id: str):
    """자막을 fetch해서 텍스트 라인 리스트로 반환. 실패 시 None."""
    last_error = None
    for attempt in range(1, MAX_RETRIES + 2):  # 최초 1회 + 재시도
        try:
            transcript = ytt_api.fetch(video_id, languages=["ko", "en"])
            return [entry.text for entry in transcript]
        except (TranscriptsDisabled, NoTranscriptFound, VideoUnavailable) as e:
            # 자막 자체가 없는 경우는 재시도해도 의미 없으므로 바로 포기
            print(f"  ⚠ 자막 없음/비활성화: {e}")
            return None
        except Exception as e:
            last_error = e
            if attempt <= MAX_RETRIES:
                wait = 2 * attempt
                print(f"  ↻ 오류 발생, {wait}초 후 재시도({attempt}/{MAX_RETRIES}): {e}")
                time.sleep(wait)
            else:
                print(f"  ✗ 재시도 실패: {last_error}")
    return None


def process_urls_from_file(input_file: str = INPUT_FILE, output_dir: str = OUTPUT_DIR) -> None:
    os.makedirs(output_dir, exist_ok=True)

    if not os.path.exists(input_file):
        print(f"'{input_file}' 파일을 찾을 수 없습니다.")
        return

    with open(input_file, "r", encoding="utf-8") as f:
        urls = [line.strip() for line in f if line.strip()]

    if not urls:
        print(f"'{input_file}'에 처리할 URL이 없습니다.")
        return

    progress = load_progress()
    ytt_api = build_api()

    print(f"총 {len(urls)}개의 URL 작업을 시작합니다.\n" + "-" * 50)

    success_count = 0
    fail_count = 0

    for idx, url in enumerate(urls, 1):
        video_id = get_video_id(url)

        # 이미 성공적으로 처리된 영상은 건너뜀 (재실행 시 이어서 진행)
        if progress.get(video_id, {}).get("status") == "success":
            print(f"[{idx}/{len(urls)}] 이미 완료됨, 건너뜀: {video_id}")
            success_count += 1
            continue

        print(f"[{idx}/{len(urls)}] 처리 중: {url}")

        title = get_video_title(video_id)
        safe_title = sanitize_filename(title) or video_id
        filename = f"{idx}. {safe_title}.txt"
        output_path = os.path.join(output_dir, filename)

        lines = fetch_transcript_text(ytt_api, video_id)

        if lines is not None:
            with open(output_path, "w", encoding="utf-8") as f:
                for line in lines:
                    f.write(f"{line}\n")
            progress[video_id] = {
                "index": idx,
                "title": title,
                "status": "success",
                "file": filename,
            }
            success_count += 1
            print(f"  ✓ 저장 완료: {filename}")
        else:
            progress[video_id] = {
                "index": idx,
                "title": title,
                "status": "failed",
                "file": None,
            }
            fail_count += 1

        save_progress(progress)
        time.sleep(REQUEST_DELAY_SEC)

    print("-" * 50)
    print(f"✨ 작업 완료: 총 {len(urls)}개 중 성공 {success_count}개 / 실패 {fail_count}개")
    if fail_count:
        print(f"실패한 영상 목록은 {PROGRESS_FILE} 에서 status가 'failed'인 항목을 확인하세요.")


if __name__ == "__main__":
    process_urls_from_file()