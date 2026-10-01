"""
유튜브 채널의 영상 URL을 수집해 channel_video_urls.txt로 저장한다. (파이프라인 1단계)
notebooks/crawling.ipynb의 첫 셀을 스크립트로 옮긴 것.

동작:
  1) yt-dlp flat 모드로 채널의 전체 영상 목록을 가져온다 (최신순).
  2) 목록이 최신순이라는 점을 이용해 이분 탐색으로 기준일(기본 2023-01-01) 이후 영상의
     마지막 위치만 찾는다. 영상 상세 조회(업로드일)를 전체가 아니라 log2(N)회만 한다.
  3) 기준일 이후 영상의 URL만 data/urls/channel_video_urls.txt에 저장한다.

실행:
  uv run python src/pipeline/collect_video_urls.py
  uv run python src/pipeline/collect_video_urls.py --since 20230101 --channel https://www.youtube.com/@bimirya/videos
"""
import argparse
import time
from pathlib import Path

import yt_dlp

# 프로젝트 루트(sesac-final-project/) 기준으로 경로를 잡는다. 실행 위치(cwd)와 무관하게 동작.
ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "data"
URLS_DIR = DATA_DIR / "urls"
OUTPUT_FILE = URLS_DIR / "channel_video_urls.txt"

DEFAULT_CHANNEL_URL = "https://www.youtube.com/@bimirya/videos"
DEFAULT_SINCE = "20230101"  # 이 날짜(YYYYMMDD) 이상 업로드된 영상만 수집

# 상세 조회 사이 대기 시간(초). 429 차단 방지용
REQUEST_DELAY = 0.5


def fetch_channel_entries(channel_url):
    """채널의 전체 영상 목록(id, title)을 최신순으로 가져온다."""
    opts = {"extract_flat": "in_playlist", "quiet": True, "no_warnings": True}
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(channel_url, download=False)
    return [e for e in info.get("entries", []) if e]


def get_upload_date(ydl, video_id):
    """영상 ID의 upload_date(YYYYMMDD)를 가져온다. 실패하면 빈 문자열."""
    try:
        info = ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=False)
        return info.get("upload_date") or ""
    except Exception:
        return ""


def find_cutoff_index(entries, since):
    """최신순 목록에서 since보다 오래된 첫 영상의 인덱스를 이분 탐색으로 찾는다.
    entries[:cutoff]가 since 이후 영상이다."""
    detail_opts = {"skip_download": True, "extract_flat": False, "quiet": True, "no_warnings": True}
    left, right = 0, len(entries) - 1
    cutoff = len(entries)

    with yt_dlp.YoutubeDL(detail_opts) as ydl:
        while left <= right:
            mid = (left + right) // 2
            date = get_upload_date(ydl, entries[mid]["id"])
            print(f"탐색 중 -> [{mid}번째] 날짜: {date} | {entries[mid].get('title', '')}")
            time.sleep(REQUEST_DELAY)

            if date and date >= since:
                left = mid + 1  # 기준일 이후 영상이므로 더 오래된 쪽을 확인
            else:
                cutoff = mid  # 기준일 이전(또는 날짜 조회 실패)이므로 더 최신 쪽을 확인
                right = mid - 1
    return cutoff


def main(channel_url=DEFAULT_CHANNEL_URL, since=DEFAULT_SINCE, output_file=OUTPUT_FILE):
    entries = fetch_channel_entries(channel_url)
    print(f"전체 영상 {len(entries)}개 수집 완료. 이분 탐색으로 {since} 이후 영상을 찾습니다...")

    cutoff = find_cutoff_index(entries, since)
    valid_entries = entries[:cutoff]

    output_file = Path(output_file)
    output_file.parent.mkdir(parents=True, exist_ok=True)
    with open(output_file, "w", encoding="utf-8") as f:
        for entry in valid_entries:
            f.write(f"https://www.youtube.com/watch?v={entry['id']}\n")

    print(f"\n완료! 전체 {len(entries)}개 중 {since} 이후 영상 {len(valid_entries)}개를 '{output_file}'에 저장했습니다.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="유튜브 채널 영상 URL 수집")
    parser.add_argument("--channel", default=DEFAULT_CHANNEL_URL, help="채널 /videos URL")
    parser.add_argument("--since", default=DEFAULT_SINCE, help="이 날짜(YYYYMMDD) 이상 영상만 수집")
    args = parser.parse_args()
    main(args.channel, args.since)
