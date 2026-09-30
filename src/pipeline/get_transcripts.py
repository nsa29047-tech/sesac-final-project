import os
import re
import time
from playwright.sync_api import sync_playwright

# 1. 현재 스크립트(get_transcripts.py)가 위치한 절대 경로 기준 설정
from pathlib import Path

# 프로젝트 루트(sesac-final-project/) 기준으로 경로를 잡는다. 실행 위치(cwd)와 무관하게 동작.
ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "data"
URLS_DIR = DATA_DIR / "urls"
BASE_DIR = ROOT


def get_video_id(url):
    match = re.search(r'(?:v=|\/)([0-9A-Za-z_-]{11})', url)
    return match.group(1) if match else url


def save_youtube_transcript_browser(page, video_url, output_filename):
    video_id = get_video_id(video_url)
    clean_url = f'https://www.youtube.com/watch?v={video_id}'

    try:
        page.goto(clean_url, wait_until='domcontentloaded', timeout=20000)
        time.sleep(2)

        # 1. '...더보기' 클릭
        expand_btn = page.query_selector(
            '#expand, tp-yt-paper-button#expand, #description-inline-expander'
        )
        if expand_btn:
            try:
                expand_btn.click()
                time.sleep(0.5)
            except:
                pass

        # 2. '스크립트 표시' 버튼 클릭
        transcript_btn = page.query_selector(
            'button[aria-label*="스크립트"], button[aria-label*="Transcript"], ytd-video-description-transcript-section-renderer button'
        )
        if not transcript_btn:
            for b in page.query_selector_all('button'):
                t = b.inner_text()
                if (
                    '스크립트 표시' in t
                    or 'Show transcript' in t
                    or '스크립트' in t
                ):
                    transcript_btn = b
                    break

        if not transcript_btn:
            print(f'⚠️ 자막/스크립트 버튼 없음: {clean_url}')
            return False

        transcript_btn.click()
        time.sleep(1.5)

        # 3. 자막 세그먼트 텍스트 추출
        page.wait_for_selector(
            'ytd-transcript-segment-renderer', timeout=8000
        )
        segments = page.query_selector_all('ytd-transcript-segment-renderer')

        lines = []
        for seg in segments:
            text = seg.inner_text().strip()
            parts = text.split('\n')
            if len(parts) >= 2:
                lines.append(parts[-1].strip())
            elif text:
                lines.append(text)

        if not lines:
            print(f'⚠️ 자막 내용 비어있음: {clean_url}')
            return False

        with open(output_filename, 'w', encoding='utf-8') as f:
            f.write('\n'.join(lines))

        print(f' 성공적으로 저장되었습니다: {output_filename}')
        return True

    except Exception as e:
        print(f' 자막 추출 실패 ({video_id}): {e}')
        return False


def main(input_file=str(URLS_DIR / 'filtered_channel_video_urls.txt'), output_dir=str(DATA_DIR / 'transcripts')):
    # 절대 경로로 변환
    abs_input_file = (
        os.path.join(BASE_DIR, input_file)
        if not os.path.isabs(input_file)
        else input_file
    )
    abs_output_dir = (
        os.path.join(BASE_DIR, output_dir)
        if not os.path.isabs(output_dir)
        else output_dir
    )

    os.makedirs(abs_output_dir, exist_ok=True)
    if not os.path.exists(abs_input_file):
        print(f"'{abs_input_file}' 파일이 없습니다.")
        return

    with open(abs_input_file, 'r', encoding='utf-8') as f:
        urls = [line.strip() for line in f if line.strip()]

    print(
        f'총 {len(urls)}개의 URL 브라우저 자동 수집을 시작합니다.\n'
        + '-' * 50
    )
    success_count = 0

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=[
                '--lang=ko-KR,ko',
                '--disable-blink-features=AutomationControlled',
            ],
        )
        context = browser.new_context(
            user_agent=(
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'
                ' AppleWebKit/537.36 (KHTML, like Gecko)'
                ' Chrome/123.0.0.0 Safari/537.36'
            ),
            locale='ko-KR',
        )
        page = context.new_page()

        for idx, url in enumerate(urls, 1):
            video_id = get_video_id(url)
            output_filename = os.path.join(abs_output_dir, f'{video_id}.txt')
            print(f'[{idx}/{len(urls)}] 처리 중: {url}')

            if save_youtube_transcript_browser(page, url, output_filename):
                success_count += 1

            time.sleep(1.0)

        browser.close()

    print('-' * 50)
    print(f'✨ 작업 완료: 총 {len(urls)}개 중 {success_count}개 성공')


if __name__ == '__main__':
    main()