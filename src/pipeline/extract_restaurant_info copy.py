"""
filtered_channel_video_urls.txt (필터링을 통과한 유튜브 URL 목록)를 입력으로 받아서,
  1) 각 영상의 소개란(description)을 가져오고
  2) LLM(Claude)으로 소개란에서 식당 이름 + 주소를 추출하고
     (국내 식당은 한글 이름/주소, 해외 식당은 영문 이름/주소 그대로 추출 - 번역하지 않음)
  3) Google Places API로 그 이름+주소를 검색해서 상세 정보(주소, 전화번호, 평점, 웹사이트 등)를 가져오고
  4) 미슐랭 가이드 공식 사이트를 이름으로 검색해서 수상 여부(스타/빕구르망/셀렉티드)를 확인
하여 restaurants_info.csv 로 저장하는 스크립트.

사전 준비:
  pip install yt-dlp openai requests beautifulsoup4 python-dotenv

환경변수:
  OPENAI_API_KEY      - 이름/주소 추출용 LLM 호출에 사용
  GOOGLE_PLACES_API_KEY - Places API가 활성화된 Google Cloud 프로젝트의 API 키

주의:
  - 미슐랭 가이드 스크래핑 부분(check_michelin)은 공식 API가 아니라 검색 페이지 HTML을 파싱하는
    방식이라, 사이트 구조가 바뀌면 동작하지 않을 수 있습니다. 실행 전에 셀렉터를 한 번 확인해보세요.
  - Google Places API, 미슐랭 사이트 모두 과도한 요청은 차단/비용 문제가 될 수 있어 각 호출 사이에
    딜레이를 넣었습니다.
"""

import os
import csv
import json
import time
import random
import difflib

import requests
import yt_dlp
from bs4 import BeautifulSoup
from dotenv import load_dotenv

try:
    from openai import OpenAI
except ImportError:
    OpenAI = None

load_dotenv()  # 스크립트와 같은 폴더의 .env 파일을 읽어와 os.environ에 반영

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
INPUT_FILE = os.path.join(SCRIPT_DIR, "filtered_channel_video_urls.txt")
OUTPUT_FILE = os.path.join(SCRIPT_DIR, "restaurants_info.csv")

GOOGLE_PLACES_API_KEY = os.environ.get("GOOGLE_PLACES_API_KEY", "")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
EXTRACTION_MODEL = "gpt-4o-mini"  # 필요하면 원하는 모델로 교체

YTDLP_DELAY = (1.0, 2.5)      # yt-dlp 요청 사이 딜레이(초)
PLACES_DELAY = (0.3, 0.8)     # Google Places API 요청 사이 딜레이
MICHELIN_DELAY = (1.5, 3.0)   # 미슐랭 사이트 요청 사이 딜레이

MICHELIN_MATCH_THRESHOLD = 0.6  # 이름 유사도 매칭 최소 기준 (0~1)


# ---------------------------------------------------------------------------
# 1. 유튜브 영상 설명(description) 가져오기
# ---------------------------------------------------------------------------

def get_video_description(url: str) -> tuple[str, str]:
    ydl_opts = {
        "skip_download": True,
        "extract_flat": False,
        "quiet": True,
        "no_warnings": True,
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)
    title = info.get("title", "")
    description = info.get("description") or ""
    return title, description


# ---------------------------------------------------------------------------
# 2. LLM으로 소개란에서 식당명 + 주소 추출
# ---------------------------------------------------------------------------

def extract_name_address(description: str) -> dict | None:
    """
    소개란 텍스트에서 식당 이름과 주소를 추출한다.
    국내 식당이면 한글 그대로, 해외 식당이면 영문(또는 현지어) 그대로 추출하고
    임의로 번역/음역하지 않도록 프롬프트에서 명시함.
    실패하거나 정보가 없으면 None 반환.
    """
    if OpenAI is None:
        raise RuntimeError("openai 패키지가 설치되어 있지 않습니다. `pip install openai` 실행 후 다시 시도하세요.")
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY 환경변수가 설정되어 있지 않습니다.")

    client = OpenAI(api_key=OPENAI_API_KEY)

    prompt = f"""아래는 미식 유튜브 영상의 소개란(description) 텍스트입니다.
이 안에서 소개하는 식당의 "이름"과 "주소"를 찾아서 JSON으로만 답하세요.

규칙:
- 이름과 주소는 원문에 적힌 언어 그대로 추출하세요. 번역하거나 로마자로 바꾸지 마세요.
  (국내 식당이면 한글 그대로, 해외 식당이면 영어/현지어 그대로)
- 여러 식당이 언급되면 영상에서 메인으로 다루는 식당 하나만 고르세요.
- 이름이나 주소 중 하나라도 확실히 찾을 수 없으면 해당 값에 null을 넣으세요.
- 반드시 아래 형식의 JSON 객체 하나만 출력하세요.

{{"name": "식당 이름 또는 null", "address": "주소 또는 null"}}

---
{description}
---
"""

    response = client.chat.completions.create(
        model=EXTRACTION_MODEL,
        max_tokens=300,
        response_format={"type": "json_object"},  # JSON 형식으로만 응답하도록 강제
        messages=[{"role": "user", "content": prompt}],
    )
    raw_text = response.choices[0].message.content.strip()

    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError:
        return None

    name = data.get("name")
    address = data.get("address")
    if not name or not address:
        return None
    return {"name": name, "address": address}


# ---------------------------------------------------------------------------
# 3. Google Places API 조회
# ---------------------------------------------------------------------------

def find_place(name: str, address: str) -> str | None:
    """이름+주소로 Google Places에서 place_id를 찾는다."""
    url = "https://maps.googleapis.com/maps/api/place/findplacefromtext/json"
    params = {
        "input": f"{name} {address}",
        "inputtype": "textquery",
        "fields": "place_id",
        "key": GOOGLE_PLACES_API_KEY,
    }
    resp = requests.get(url, params=params, timeout=10)
    resp.raise_for_status()
    data = resp.json()

    candidates = data.get("candidates", [])
    if not candidates:
        return None
    return candidates[0].get("place_id")


def get_place_details(place_id: str) -> dict:
    """place_id로 상세 정보를 가져온다."""
    url = "https://maps.googleapis.com/maps/api/place/details/json"
    fields = ",".join([
        "name", "formatted_address", "formatted_phone_number",
        "international_phone_number", "website", "rating",
        "user_ratings_total", "price_level", "opening_hours",
        "geometry", "types", "url",
    ])
    params = {
        "place_id": place_id,
        "fields": fields,
        "key": GOOGLE_PLACES_API_KEY,
    }
    resp = requests.get(url, params=params, timeout=10)
    resp.raise_for_status()
    data = resp.json()
    return data.get("result", {})


def lookup_google_place(name: str, address: str) -> dict:
    result = {
        "google_place_id": "", "google_name": "", "google_formatted_address": "",
        "google_phone": "", "google_website": "", "google_rating": "",
        "google_user_ratings_total": "", "google_price_level": "",
        "google_types": "", "google_maps_url": "", "google_lat": "", "google_lng": "",
    }
    place_id = find_place(name, address)
    if not place_id:
        return result

    details = get_place_details(place_id)
    geometry = details.get("geometry", {}).get("location", {})

    result.update({
        "google_place_id": place_id,
        "google_name": details.get("name", ""),
        "google_formatted_address": details.get("formatted_address", ""),
        "google_phone": details.get("international_phone_number", "") or details.get("formatted_phone_number", ""),
        "google_website": details.get("website", ""),
        "google_rating": details.get("rating", ""),
        "google_user_ratings_total": details.get("user_ratings_total", ""),
        "google_price_level": details.get("price_level", ""),
        "google_types": ", ".join(details.get("types", [])),
        "google_maps_url": details.get("url", ""),
        "google_lat": geometry.get("lat", ""),
        "google_lng": geometry.get("lng", ""),
    })
    return result


# ---------------------------------------------------------------------------
# 4. 미슐랭 가이드 확인 (공식 API가 없어 검색 페이지를 스크래핑)
# ---------------------------------------------------------------------------

def check_michelin(name: str) -> dict:
    """
    guide.michelin.com에서 식당명을 검색해서 가장 유사한 결과의 수상 등급을 가져온다.
    사이트 구조가 바뀌면 셀렉터(CSS class 등)를 다시 확인해야 할 수 있음.
    매칭되는 게 없거나 파싱에 실패하면 michelin_status를 "정보 없음"으로 남긴다.
    """
    result = {"michelin_status": "정보 없음", "michelin_url": ""}
    try:
        search_url = "https://guide.michelin.com/en/search"
        resp = requests.get(
            search_url,
            params={"q": name},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=10,
        )
        print(resp.status_code, resp.text[:300])
        if resp.status_code != 200:
            return result

        soup = BeautifulSoup(resp.text, "html.parser")
        # 검색 결과 카드 셀렉터는 사이트 구조 변경 시 조정이 필요합니다.
        cards = soup.select("a.card__menu, div.card")
        best_ratio = 0.0
        best_card = None

        for card in cards:
            card_name_el = card.select_one(".card__menu-content--title, h3")
            if not card_name_el:
                continue
            card_name = card_name_el.get_text(strip=True)
            ratio = difflib.SequenceMatcher(None, name.lower(), card_name.lower()).ratio()
            if ratio > best_ratio:
                best_ratio = ratio
                best_card = card

        if best_card is not None and best_ratio >= MICHELIN_MATCH_THRESHOLD:
            distinction_el = best_card.select_one(".card__menu-top .distinction-icon, .card__menu-top img")
            distinction = distinction_el.get("alt", "").strip() if distinction_el else ""
            href = best_card.get("href", "")
            result["michelin_status"] = distinction or "매칭됨 (등급 파싱 실패)"
            result["michelin_url"] = f"https://guide.michelin.com{href}" if href.startswith("/") else href

    except Exception as e:
        result["michelin_status"] = f"조회 오류: {e}"

    return result


# ---------------------------------------------------------------------------
# 메인 파이프라인
# ---------------------------------------------------------------------------

def main():
    if not os.path.exists(INPUT_FILE):
        print(f"오류: {INPUT_FILE} 파일이 없습니다.")
        return

    with open(INPUT_FILE, "r", encoding="utf-8") as f:
        urls = [line.strip() for line in f if line.strip()]

    print(f"총 {len(urls)}개 영상을 처리합니다...\n")

    fieldnames = [
        "video_url", "video_title", "extracted_name", "extracted_address",
        "google_place_id", "google_name", "google_formatted_address",
        "google_phone", "google_website", "google_rating",
        "google_user_ratings_total", "google_price_level", "google_types",
        "google_maps_url", "google_lat", "google_lng",
        "michelin_status", "michelin_url", "note",
    ]

    with open(OUTPUT_FILE, "w", encoding="utf-8", newline="") as out_f:
        writer = csv.DictWriter(out_f, fieldnames=fieldnames)
        writer.writeheader()

        for idx, url in enumerate(urls, start=1):
            row = {name: "" for name in fieldnames}
            row["video_url"] = url

            try:
                title, description = get_video_description(url)
                row["video_title"] = title
            except Exception as e:
                row["note"] = f"영상 정보 조회 실패: {e}"
                print(f"[{idx}/{len(urls)}] [실패] {url} - {e}")
                writer.writerow(row)
                continue

            time.sleep(random.uniform(*YTDLP_DELAY))

            extracted = extract_name_address(description)
            if not extracted:
                row["note"] = "이름/주소 추출 실패"
                print(f"[{idx}/{len(urls)}] [추출 실패] {title}")
                writer.writerow(row)
                continue

            row["extracted_name"] = extracted["name"]
            row["extracted_address"] = extracted["address"]

            try:
                google_info = lookup_google_place(extracted["name"], extracted["address"])
                row.update(google_info)
            except Exception as e:
                row["note"] = f"Google Places 조회 실패: {e}"

            time.sleep(random.uniform(*PLACES_DELAY))

            michelin_info = check_michelin(extracted["name"])
            row.update(michelin_info)

            time.sleep(random.uniform(*MICHELIN_DELAY))

            print(f"[{idx}/{len(urls)}] [완료] {extracted['name']} - {row.get('google_formatted_address') or '주소 매칭 실패'} / 미슐랭: {row['michelin_status']}")
            writer.writerow(row)

    print(f"\n처리 완료! 결과 -> '{OUTPUT_FILE}'")


if __name__ == "__main__":
    main()