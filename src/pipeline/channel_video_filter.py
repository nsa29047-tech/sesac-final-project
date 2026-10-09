import os
import random
import time
import yt_dlp

from pathlib import Path
from video_meta_cache import get_video_meta

# 프로젝트 루트(sesac-final-project/) 기준으로 경로를 잡는다. 실행 위치(cwd)와 무관하게 동작.
ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "data"
URLS_DIR = DATA_DIR / "urls"

input_file = str(URLS_DIR / "channel_video_urls.txt")
valid_output_file = str(URLS_DIR / "filtered_channel_video_urls.txt")
excluded_output_file = str(URLS_DIR / "excluded_channel_video_urls.txt")

# 요청 사이 대기 시간(초). 너무 빠르게 연속 요청하면 YouTube 쪽에서 429(Too Many Requests)로
# 차단될 수 있어서 매 영상마다 살짝 랜덤한 딜레이를 둠
MIN_DELAY = 1.0
MAX_DELAY = 2.5

# 1. 파일에서 URL 목록 읽어오기
if not os.path.exists(input_file):
    print(f"오류: {input_file} 파일이 없습니다.")
else:
    with open(input_file, "r", encoding="utf-8") as f:
        urls = [line.strip() for line in f if line.strip()]

    print(f"총 {len(urls)}개의 URL을 확인합니다...\n")

    ydl_opts = {
        'skip_download': True,
        'extract_flat': False,
        'quiet': True,
        'no_warnings': True
    }

    valid_urls = []
    excluded_urls = []

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        for idx, url in enumerate(urls, start=1):
            cached = False
            try:
                # 가져온 소개란은 data/descriptions/에 캐시해 3단계(extract_restaurant_info.py)가 재사용한다
                meta = get_video_meta(url, ydl)
                cached = meta['cached']
                title = meta['title'] or '제목 없음'
                description = meta['description']

                # 2. 필터링 조건: 소개란에 '가게' 또는 '장소' 언급이 있는 영상만 통과
                # 중국 소재 여부는 이후 Google Places API에서 받아오는 주소(address_components의
                # country 필드)로 정확히 판별할 예정이라, 이 단계에서는 텍스트로 걸러내지 않음
                # (설명란에 'china'라는 단어가 있는지 없는지는 실제 주소와 무관할 수 있어 신뢰도가 낮음)
                if ('가게' not in description) and ('장소' not in description):
                    print(f"[{idx}/{len(urls)}] [제외] {title}")
                    excluded_urls.append(url)
                else:
                    print(f"[{idx}/{len(urls)}] [유지] {title}")
                    valid_urls.append(url)

            except Exception as e:
                print(f"[{idx}/{len(urls)}] [오류/제외] {url} - {e}")
                excluded_urls.append(url)

            # 마지막 URL 처리 후에는 대기할 필요 없음
            if idx < len(urls) and not cached:
                time.sleep(random.uniform(MIN_DELAY, MAX_DELAY))

    # 3-1. 통과된 URL 목록 저장
    with open(valid_output_file, "w", encoding="utf-8") as f:
        for valid_url in valid_urls:
            f.write(valid_url + "\n")

    # 3-2. 제외된 URL 목록 저장
    with open(excluded_output_file, "w", encoding="utf-8") as f:
        for excluded_url in excluded_urls:
            f.write(excluded_url + "\n")

    print(f"\n처리 완료!")
    print(f"- 통과된 URL: {len(valid_urls)}개 -> '{valid_output_file}'")
    print(f"- 제외된 URL: {len(excluded_urls)}개 -> '{excluded_output_file}'")