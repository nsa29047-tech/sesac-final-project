"""
필터링된 유튜브 영상 목록(filtered_channel_video_urls.txt)을 입력으로 받아,
  1) yt-dlp로 영상 소개란을 가져오고
  2) OpenAI로 소개란에서 식당 한국어 상호명/주소/국가코드를 추출하고
  3) Google Places API(New)로 '현지 공식 상호명'을 확보하고
  4) Tavily 검색 + OpenAI로 미슐랭 등재 이력을 판별
하여 restaurants_info.csv 로 저장하는 파이프라인.

기존에 직접 작성하신 단일 영상 테스트용 스크립트를 다음 부분을 보강해서 배치용으로 정리했습니다.

바뀐/보강된 부분
  - filtered_channel_video_urls.txt 전체를 순회하도록 일반화 (기존: target_url 하나만 처리)
  - 각 단계 실패해도 다음 영상/가게로 넘어가도록 예외 처리 추가
  - 결과를 매 건마다 CSV에 즉시 기록 (중간에 죽어도 그동안 처리한 건 보존됨)
  - 미슐랭 판별 시 guide.michelin.com 도메인으로 먼저 검색 -> 없으면 일반 웹 검색으로 폴백
    (Tavily의 include_domains로 공식 사이트가 색인되어 있으면 그쪽을 우선 신뢰)
  - MichelinInfo에 source_urls를 추가해서, 나중에 사람이 결과를 눈으로 검증할 수 있게 근거 링크를 남김
  - 프롬프트에 "검색 결과에 명시적 근거가 없는 연도는 추측해서 채우지 말 것"을 명시 (환각 방지)
  - Google Places 호출 실패 시 원인 파악을 위해 상태코드/응답 본문을 출력
  - 각 API 호출 사이에 딜레이를 둬서 rate limit 방지

사전 준비:
  pip install yt-dlp openai requests python-dotenv pydantic pandas openpyxl

환경변수(.env):
  OPENAI_API_KEY        - 필수
  GOOGLE_PLACES_API_KEY - Places API (New)가 활성화된 키. 없으면 구글 조회는 건너뜀
  TAVILY_API_KEY        - 없으면 미슐랭 조회는 "정보 없음"으로 처리됨
"""

import os
import re
import csv
import time
import random
from enum import Enum
from typing import List, Optional, Dict, Any

import requests
import yt_dlp
from dotenv import load_dotenv
from pydantic import BaseModel, Field
from openai import OpenAI

# -------------------------------------------------------------
# 0. 환경 변수 로드
# -------------------------------------------------------------
load_dotenv()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
GOOGLE_PLACES_API_KEY = os.getenv("GOOGLE_PLACES_API_KEY")
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY")

if not OPENAI_API_KEY:
    raise ValueError("⚠️ .env 파일에 OPENAI_API_KEY가 필요합니다.")
if not GOOGLE_PLACES_API_KEY:
    print("⚠️ GOOGLE_PLACES_API_KEY가 없어서 Google Places 조회는 건너뜁니다.")
if not TAVILY_API_KEY:
    print("⚠️ TAVILY_API_KEY가 없어서 미슐랭 조회는 모두 '정보 없음'으로 처리됩니다.")

client = OpenAI(api_key=OPENAI_API_KEY)

from pathlib import Path

# 프로젝트 루트(sesac-final-project/) 기준으로 경로를 잡는다. 실행 위치(cwd)와 무관하게 동작.
ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "data"
URLS_DIR = DATA_DIR / "urls"
INPUT_FILE = str(URLS_DIR / "filtered_channel_video_urls.txt")
OUTPUT_FILE = str(DATA_DIR / "restaurants_info.csv")

YTDLP_DELAY = (1.0, 2.5)
STORE_DELAY = (1.0, 2.0)  # Google/Tavily/OpenAI 호출 사이 딜레이 (가게 단위)


# -------------------------------------------------------------
# 1. Pydantic 스키마 정의
# -------------------------------------------------------------
class EditionType(str, Enum):
    REGULAR = "REGULAR"
    SPECIAL = "SPECIAL"
    NONE = "NONE"


class MichelinGrade(str, Enum):
    THREE_STARS = "3_STARS"
    TWO_STARS = "2_STARS"
    ONE_STAR = "1_STAR"
    BIB_GOURMAND = "BIB_GOURMAND"
    SELECTED = "SELECTED"
    NONE = "NONE"


class MichelinYearRecord(BaseModel):
    edition: str = Field(description="연도 또는 에디션명 (예: '2025', '2017 홋카이도 특별판')")
    grade: MichelinGrade = Field(description="획득 등급")


class MichelinInfo(BaseModel):
    is_michelin: bool = Field(description="미슐랭 등재 이력이 있는지 여부")
    edition_type: EditionType = Field(description="가이드 종류 (REGULAR, SPECIAL, NONE)")
    latest_grade: MichelinGrade = Field(description="가장 최근 획득한 등급")
    history: List[MichelinYearRecord] = Field(
        default_factory=list,
        description="정규 도시: 최근 5개년(2021~2025) 이력 / 특별판: 최근 2회 에디션 이력. "
                    "검색 결과에 명시적으로 근거가 없는 연도는 절대 채우지 말 것."
    )
    is_active: bool = Field(description="현재 유효 여부 (정규는 최신년도 유지, 특별판은 마지막 특별판 등재)")
    summary_badge: str = Field(description="요약 뱃지 (예: '2021-2025 5년 연속 2스타', '미등재')")
    michelin_url: Optional[str] = Field(default=None, description="공식 링크 (검색 결과 중 guide.michelin.com이 있으면 그 URL)")
    source_urls: List[str] = Field(default_factory=list, description="판정에 실제로 사용한 검색 결과 출처 URL 목록")
    reason: str = Field(description="판정 상세 근거. 근거가 부족하면 그 사실을 명시할 것")


class StoreInfo(BaseModel):
    korean_name: str = Field(description="영상 본문에 언급된 한국어 상호명 (예: 멘야 사이미, 밍글스)")
    address: str = Field(description="가게 주소 또는 도시/지역명")
    country_code: str = Field(default="KR", description="ISO 2자리 국가코드 (대문자: KR, JP, US, FR 등)")
    extra_info: Optional[str] = Field(default=None, description="메뉴, 타임스탬프 등")


class StoreExtractionResult(BaseModel):
    has_store_info: bool = Field(description="가게 정보 포함 여부")
    stores: List[StoreInfo] = Field(default_factory=list, description="추출된 가게 목록")


class SubCategoryResult(BaseModel):
    sub_category: str = Field(description="음식 세부 카테고리 한 단어 또는 짧은 구 (예: 족발, 초밥, 치킨, 파인다이닝, 한정식, 삼겹살 등)")


# -------------------------------------------------------------
# 1.5 Google 카테고리 정리 & 1차 대분류 매핑
# -------------------------------------------------------------
# 거의 모든 장소에 공통으로 붙는 boilerplate 타입 - 정보 가치가 없어서 제거
GOOGLE_BOILERPLATE_TYPES = {"restaurant", "food", "point_of_interest", "establishment", "meal_takeaway", "meal_delivery"}

# 더 구체적인 타입(예: french_restaurant)이 있으면 같이 붙어오는 대륙/권역 단위 상위 타입 - 중복이라 제거
GOOGLE_UMBRELLA_TYPES = {"european_restaurant", "asian_restaurant", "middle_eastern_restaurant",
                          "african_restaurant", "latin_american_restaurant"}

# Google primaryType(또는 types) -> 1차 대분류. 필요에 따라 계속 추가/수정하면 됨.
BROAD_CATEGORY_MAP = {
    # 한식
    "korean_restaurant": "한식", "korean_barbecue_restaurant": "한식",
    # 일식
    "japanese_restaurant": "일식", "sushi_restaurant": "일식", "ramen_restaurant": "일식",
    "japanese_curry_restaurant": "일식", "tonkatsu_restaurant": "일식",
    # 중식
    "chinese_restaurant": "중식", "cantonese_restaurant": "중식", "dim_sum_restaurant": "중식",
    "taiwanese_restaurant": "중식",
    # 동남아/인도
    "thai_restaurant": "동남아식", "vietnamese_restaurant": "동남아식", "indonesian_restaurant": "동남아식",
    "malaysian_restaurant": "동남아식", "filipino_restaurant": "동남아식",
    "indian_restaurant": "인도식", "north_indian_restaurant": "인도식", "south_indian_restaurant": "인도식",
    "pakistani_restaurant": "인도식", "sri_lankan_restaurant": "인도식", "bangladeshi_restaurant": "인도식",
    # 양식 (유럽 + 아메리카)
    "french_restaurant": "양식", "italian_restaurant": "양식", "spanish_restaurant": "양식",
    "german_restaurant": "양식", "greek_restaurant": "양식", "mediterranean_restaurant": "양식",
    "european_restaurant": "양식", "american_restaurant": "양식", "steak_house": "양식",
    "hamburger_restaurant": "양식", "pizza_restaurant": "양식", "seafood_restaurant": "양식",
    "mexican_restaurant": "양식", "brazilian_restaurant": "양식",
    # 중동
    "turkish_restaurant": "중동식", "lebanese_restaurant": "중동식", "middle_eastern_restaurant": "중동식",
}


def clean_google_types(raw_types: List[str]) -> List[str]:
    """boilerplate 타입 제거 + 더 구체적인 타입이 있으면 대륙 단위 umbrella 타입도 제거."""
    cleaned = [t for t in raw_types if t not in GOOGLE_BOILERPLATE_TYPES]
    specific = [t for t in cleaned if t not in GOOGLE_UMBRELLA_TYPES]
    return specific if specific else cleaned  # 구체적인 게 하나도 없으면 umbrella 타입이라도 남김


def map_broad_category(primary_type: str, cleaned_types: List[str]) -> str:
    """1차 대분류 (한식/일식/중식/양식/동남아식/인도식/중동식/기타)."""
    if primary_type in BROAD_CATEGORY_MAP:
        return BROAD_CATEGORY_MAP[primary_type]
    for t in cleaned_types:
        if t in BROAD_CATEGORY_MAP:
            return BROAD_CATEGORY_MAP[t]
    return "기타"


def classify_sub_category(
    korean_name: str,
    broad_category: str,
    google_category: str,
    cleaned_types: List[str],
    editorial_summary: str,
    extra_info: Optional[str],
) -> str:
    """
    2차 세부 카테고리 (족발/초밥/치킨/파인다이닝/한정식/삼겹살 등).
    Google 타입만으로는 커버가 안 되는 한식 메뉴 기반 세분류가 많아서, 상호명/영상에서 뽑은
    메뉴 정보/구글 소개글을 종합해서 LLM이 판단하게 함. 근거가 없으면 대분류를 그대로 반환.
    """
    prompt = f"""
아래 정보를 참고해서 이 음식점의 세부 음식 카테고리를 한 단어 또는 짧은 구로 분류하세요.

[상호명]: {korean_name}
[1차 대분류]: {broad_category}
[구글 분류]: {google_category or '없음'} (구글 원본 타입: {', '.join(cleaned_types) if cleaned_types else '없음'})
[구글 소개글]: {editorial_summary or '없음'}
[영상에서 추출된 메뉴/특징 정보]: {extra_info or '없음'}

세부 카테고리 예시: 족발, 초밥, 오마카세, 라멘, 치킨, 파인다이닝, 한정식, 삼겹살, 곱창, 국밥, 파스타, 스테이크, 버거, 카페
- 예시에 없어도 적절한 세부 카테고리를 새로 만들어도 됩니다.
- 판단할 근거가 전혀 없으면 1차 대분류와 동일한 값을 그대로 쓰세요.
"""
    try:
        response = client.beta.chat.completions.parse(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "너는 음식점을 세부 카테고리로 분류하는 전문가야."},
                {"role": "user", "content": prompt}
            ],
            response_format=SubCategoryResult,
        )
        parsed = response.choices[0].message.parsed
        return parsed.sub_category if parsed else broad_category
    except Exception:
        return broad_category


# -------------------------------------------------------------
# 2. [Step 1] yt-dlp: 유튜브 설명 가져오기
# -------------------------------------------------------------
def get_youtube_description(url: str) -> dict:
    ydl_opts = {'skip_download': True, 'quiet': True, 'no_warnings': True}
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)
        return {'title': info.get('title', ''), 'description': info.get('description') or ''}


# -------------------------------------------------------------
# 3. [Step 2] OpenAI: 한국 상호명, 주소, 국가코드 추출
# -------------------------------------------------------------
def extract_stores_from_description(title: str, description: str) -> StoreExtractionResult:
    if not description.strip():
        return StoreExtractionResult(has_store_info=False, stores=[])

    prompt = f"""
다음 유튜브 영상의 제목과 설명에서 소개된 음식점의 '한국어 상호명(korean_name)', '주소(address)', '2자리 국가코드(country_code: KR, JP, US, FR 등)'를 추출하세요.
- 여러 가게가 있으면 각각 분리하여 목록으로 만드세요.
- 가게 정보가 없으면 has_store_info를 false로 설정하세요.

[영상 제목]: {title}
[영상 설명]:
{description}
"""
    response = client.beta.chat.completions.parse(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": "너는 상호명, 주소, 국가코드를 정확하게 구조화하여 추출하는 전문가야."},
            {"role": "user", "content": prompt}
        ],
        response_format=StoreExtractionResult,
    )
    parsed = response.choices[0].message.parsed
    return parsed if parsed is not None else StoreExtractionResult(has_store_info=False, stores=[])


# -------------------------------------------------------------
# 4. [Step 3] Google Places API (New): 해당 국가의 '공식 현지 상호명' 획득
# -------------------------------------------------------------
def extract_cid(maps_url: Optional[str]) -> str:
    """googleMapsUri(예: https://maps.google.com/?cid=123...)에서 cid 숫자만 추출. 없으면 빈 문자열."""
    match = re.search(r"[?&]cid=(\d+)", maps_url or "")
    return match.group(1) if match else ""


def search_restaurant_google(korean_name: str, address: Optional[str] = None, country_code: str = "KR") -> Optional[Dict[str, Any]]:
    if not GOOGLE_PLACES_API_KEY:
        return None

    query = f"{korean_name} {address}".strip() if address else korean_name
    url = "https://places.googleapis.com/v1/places:searchText"

    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": GOOGLE_PLACES_API_KEY,
        "X-Goog-FieldMask": (
            "places.id,places.displayName,places.formattedAddress,"
            "places.rating,places.userRatingCount,places.location,"
            "places.googleMapsUri,places.primaryType,places.primaryTypeDisplayName,places.types,"
            "places.businessStatus,places.priceLevel,"
            "places.nationalPhoneNumber,places.internationalPhoneNumber,places.websiteUri,"
            "places.editorialSummary,"
            "places.regularOpeningHours,places.currentOpeningHours"
            # 아래는 Enterprise+Atmosphere 등급이라 비용이 더 나가는 필드들. 필요하면 주석 해제.
            # ",places.servesVegetarianFood,places.takeout,places.delivery,places.dineIn,"
            # "places.reservable,places.outdoorSeating,places.goodForChildren,places.allowsDogs,"
            # "places.servesBreakfast,places.servesLunch,places.servesDinner,places.servesBrunch,"
            # "places.servesCoffee,places.servesDessert,places.servesCocktails"
        )
    }
    payload = {"textQuery": query, "maxResultCount": 1}
    if country_code:
        payload["regionCode"] = country_code.strip().upper()

    try:
        response = requests.post(url, headers=headers, json=payload, timeout=10)
    except requests.RequestException as e:
        print(f"    ⚠️ Google Places 요청 실패: {e}")
        return None

    if response.status_code != 200:
        # Places API (New)가 활성화 안 되어 있거나 키 제한 문제일 수 있어 원인 확인용으로 출력
        print(f"    ⚠️ Google Places 응답 오류 ({response.status_code}): {response.text[:300]}")
        return None

    places = response.json().get("places", [])
    if not places:
        return None

    place = places[0]

    regular_hours = place.get("regularOpeningHours", {}) or {}
    current_hours = place.get("currentOpeningHours", {}) or {}
    primary_type = place.get("primaryType", "")
    cleaned_types = clean_google_types(place.get("types", []))

    return {
        "place_id": place.get("id"),
        "official_local_name": place.get("displayName", {}).get("text"),
        "formatted_address": place.get("formattedAddress"),
        "rating": place.get("rating"),
        "user_rating_count": place.get("userRatingCount"),
        "category": place.get("primaryTypeDisplayName", {}).get("text"),
        "category_code": primary_type,
        "types": ", ".join(cleaned_types),
        "types_list": cleaned_types,
        "broad_category": map_broad_category(primary_type, cleaned_types),
        "lat": place.get("location", {}).get("latitude"),
        "lng": place.get("location", {}).get("longitude"),
        "cid": extract_cid(place.get("googleMapsUri")),
        "business_status": place.get("businessStatus", ""),
        "price_level": place.get("priceLevel", ""),
        "phone": place.get("internationalPhoneNumber") or place.get("nationalPhoneNumber", ""),
        "website": place.get("websiteUri", ""),
        "editorial_summary": place.get("editorialSummary", {}).get("text", ""),
        # weekdayDescriptions: ["Monday: 11:00 AM – 9:00 PM", ...] 형태의 사람이 읽기 좋은 문자열 리스트
        "opening_hours": " | ".join(regular_hours.get("weekdayDescriptions", [])),
        "open_now": current_hours.get("openNow", regular_hours.get("openNow", "")),
    }


# -------------------------------------------------------------
# 5. [Step 4] 미슐랭 판별: Tavily 검색(공식 사이트 우선) + LLM
# -------------------------------------------------------------
RAW_CONTENT_TRUNCATE = 4000  # 결과 하나당 raw_content를 이 길이까지만 잘라서 프롬프트에 사용 (토큰 절약)


def tavily_search(
    query: str,
    include_domains: Optional[List[str]] = None,
    max_results: int = 5,
    include_raw_content: bool = False,
) -> List[Dict[str, str]]:
    """
    Tavily 검색을 호출해서 결과 리스트([{title, url, content, raw_content}, ...])와 answer를 합쳐 반환.
    include_raw_content=True로 주면 검색 스니펫(content)이 아니라 페이지 전체(또는 훨씬 긴) 텍스트를
    raw_content로 같이 받아온다. content만으로는 "One Michelin Star" 같은 핵심 문구가 스니펫에서
    잘려서 안 보이는 경우가 있어서, 공식 사이트를 특정해서 검색할 때는 이 옵션을 켜는 걸 권장.
    """
    payload = {
        "api_key": TAVILY_API_KEY,
        "query": query,
        "search_depth": "basic",
        "max_results": max_results,
        "include_answer": True,
        "include_raw_content": include_raw_content,
    }
    if include_domains:
        payload["include_domains"] = include_domains

    resp = requests.post("https://api.tavily.com/search", json=payload, timeout=15)
    resp.raise_for_status()
    data = resp.json()

    results = data.get("results", [])
    if data.get("answer"):
        results = [{"title": "AI 검색 요약", "url": "", "content": data["answer"], "raw_content": ""}] + results
    return results


def check_michelin_status(official_name: str, korean_name: str, address: Optional[str] = None, country_code: str = "KR") -> MichelinInfo:
    if not TAVILY_API_KEY:
        return MichelinInfo(
            is_michelin=False, edition_type=EditionType.NONE,
            latest_grade=MichelinGrade.NONE, is_active=False,
            summary_badge="미등재", reason="TAVILY_API_KEY 없음"
        )

    all_results: List[Dict[str, str]] = []
    try:
        # 1차: 미슐랭 공식 사이트에 색인된 내용 우선 검색 (raw_content로 전체 텍스트까지 받아옴)
        all_results = tavily_search(
            f'{official_name} Michelin Guide restaurant',
            include_domains=["guide.michelin.com"],
            max_results=5,
            include_raw_content=True,
        )

        # 2차: 공식 사이트에서 못 찾으면 일반 웹 검색으로 폴백
        if not all_results:
            all_results = tavily_search(
                f'"{official_name}" Michelin Guide 미슐랭 빕구르망 스타 특별판',
                max_results=5,
            )

        # 3차: 그래도 없으면 한국어 상호명 + 주소로 보완 검색
        if not all_results:
            all_results = tavily_search(
                f'"{korean_name}" "{official_name}" "{address or ""}" 미슐랭 Michelin',
                max_results=5,
            )
    except Exception as e:
        return MichelinInfo(
            is_michelin=False, edition_type=EditionType.NONE,
            latest_grade=MichelinGrade.NONE, is_active=False,
            summary_badge="조회 오류", reason=f"Tavily 검색 에러: {e}"
        )

    source_urls = [r["url"] for r in all_results if r.get("url")]

    def _best_text(r: Dict[str, str]) -> str:
        # raw_content(전체 텍스트)가 있으면 그쪽을 우선 쓰고, 없으면 짧은 스니펫(content)로 대체
        raw = (r.get("raw_content") or "").strip()
        if raw:
            return raw[:RAW_CONTENT_TRUNCATE]
        return r.get("content", "")

    context = "\n\n".join(
        f"출처: {r.get('title')}\nURL: {r.get('url')}\n내용: {_best_text(r)}" for r in all_results
    )

    prompt = f"""
당신은 전 세계 미슐랭 가이드 레스토랑 데이터 분석 전문가입니다.
아래 검색 결과만 근거로, 식당 [{official_name} / 한국명: {korean_name}] (주소: {address or '미지정'}, 국가: {country_code})의
미슐랭 등재 이력을 판별하세요.

[중요] 검색 결과에 명시적으로 나와 있지 않은 연도/등급은 절대로 추측해서 채우지 마세요.
근거가 부족하면 history를 비워두고, reason에 "근거 부족"이라고 명시하세요.
확신이 서지 않으면 is_michelin=False, edition_type=NONE으로 판정하는 쪽을 택하세요.

[이력 수집 기준]
1. 정규 도시 (서울, 도쿄, 파리, 뉴욕 등): 검색 결과에서 확인되는 연도만 history에 채우고(최대 2021~2025),
   최신년도(2024/2025)에 유지 중이라는 근거가 있으면 is_active = True
2. 비정기 특별판 지역 (삿포로/홋카이도, 규슈 등): 검색 결과에서 확인되는 특별판 이력만 채우고,
   마지막 특별판에 등재되었다는 근거가 있으면 is_active = True
3. 미등재 또는 근거 부족: is_michelin = False, edition_type = NONE, latest_grade = NONE, summary_badge = "미등재"

[검색 데이터]:
{context if context else "검색 결과 없음"}
"""

    response = client.beta.chat.completions.parse(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": "너는 미슐랭 가이드 공식 데이터 분석 AI야. 근거 없는 추측은 하지 않아."},
            {"role": "user", "content": prompt}
        ],
        response_format=MichelinInfo,
    )
    result = response.choices[0].message.parsed
    if result is None:
        return MichelinInfo(
            is_michelin=False, edition_type=EditionType.NONE,
            latest_grade=MichelinGrade.NONE, is_active=False,
            summary_badge="판정 실패", reason="LLM 파싱 실패"
        )

    # LLM이 '등재'로 판정했는데 source_urls를 못 채웠을 경우에만 우리가 검색했던 출처로 보강.
    # is_michelin=False(미등재)일 때는 채우지 않음 - 그래야 "미등재는 근거 URL도 없다"는 의미가 유지됨.
    if result.is_michelin and not result.source_urls:
        result.source_urls = source_urls
    return result


# -------------------------------------------------------------
# 6. 배치 파이프라인 실행
# -------------------------------------------------------------
FIELDNAMES = [
    "video_url", "video_title",
    "korean_name", "address", "country_code",
    "google_official_name", "google_formatted_address",
    "google_rating", "google_user_rating_count", "google_cid",
    "google_category", "google_category_code", "google_types",
    "category_broad", "category_detail",
    "google_business_status", "google_price_level",
    "google_phone", "google_website", "google_editorial_summary",
    "google_opening_hours", "google_open_now",
    "is_michelin", "edition_type", "latest_grade",
    "is_active", "summary_badge", "history",
    "source_urls", "note",
]


def main():
    if not os.path.exists(INPUT_FILE):
        print(f"오류: {INPUT_FILE} 파일이 없습니다.")
        return

    with open(INPUT_FILE, "r", encoding="utf-8") as f:
        urls = [line.strip() for line in f if line.strip()]

    print(f"총 {len(urls)}개 영상을 처리합니다...\n")

    with open(OUTPUT_FILE, "w", encoding="utf-8-sig", newline="") as out_f:
        writer = csv.DictWriter(out_f, fieldnames=FIELDNAMES)
        writer.writeheader()

        for v_idx, url in enumerate(urls, start=1):
            print("=" * 70)
            print(f"🎬 [{v_idx}/{len(urls)}] {url}")

            try:
                video_data = get_youtube_description(url)
            except Exception as e:
                print(f"    ⚠️ 영상 정보 조회 실패: {e}")
                writer.writerow({**{k: "" for k in FIELDNAMES}, "video_url": url, "note": f"영상 조회 실패: {e}"})
                continue

            time.sleep(random.uniform(*YTDLP_DELAY))
            print(f"📌 제목: {video_data['title']}")

            try:
                extracted = extract_stores_from_description(video_data['title'], video_data['description'])
            except Exception as e:
                print(f"    ⚠️ 상호명 추출 실패: {e}")
                writer.writerow({**{k: "" for k in FIELDNAMES}, "video_url": url,
                                  "video_title": video_data['title'], "note": f"추출 실패: {e}"})
                continue

            if not extracted.has_store_info or not extracted.stores:
                print("    ℹ️ 가게 정보 없음")
                writer.writerow({**{k: "" for k in FIELDNAMES}, "video_url": url,
                                  "video_title": video_data['title'], "note": "가게 정보 없음"})
                continue

            for store in extracted.stores:
                row = {k: "" for k in FIELDNAMES}
                row["video_url"] = url
                row["video_title"] = video_data['title']
                row["korean_name"] = store.korean_name
                row["address"] = store.address
                row["country_code"] = store.country_code

                print(f"\n  🇰🇷 {store.korean_name} ({store.country_code}) / 📍 {store.address}")

                g_info = None
                try:
                    g_info = search_restaurant_google(store.korean_name, store.address, store.country_code)
                except Exception as e:
                    row["note"] += f"Google 조회 실패: {e}; "

                official_name = store.korean_name
                if g_info:
                    official_name = g_info["official_local_name"] or store.korean_name
                    row["google_official_name"] = g_info.get("official_local_name", "")
                    row["google_formatted_address"] = g_info.get("formatted_address", "")
                    row["google_rating"] = g_info.get("rating", "")
                    row["google_user_rating_count"] = g_info.get("user_rating_count", "")
                    row["google_cid"] = g_info.get("cid", "")
                    row["google_category"] = g_info.get("category", "")
                    row["google_category_code"] = g_info.get("category_code", "")
                    row["google_types"] = g_info.get("types", "")
                    row["google_business_status"] = g_info.get("business_status", "")
                    row["google_price_level"] = g_info.get("price_level", "")
                    row["google_phone"] = g_info.get("phone", "")
                    row["google_website"] = g_info.get("website", "")
                    row["google_editorial_summary"] = g_info.get("editorial_summary", "")
                    row["google_opening_hours"] = g_info.get("opening_hours", "")
                    row["google_open_now"] = g_info.get("open_now", "")
                    print(f"    🏛️ 구글 공식명: {official_name} / {g_info.get('formatted_address')} / {g_info.get('category')}")
                else:
                    print("    ⚠️ Google Places 매칭 실패 (한글 상호명으로 미슐랭 조회 진행)")

                try:
                    m_info = check_michelin_status(
                        official_name=official_name,
                        korean_name=store.korean_name,
                        address=g_info["formatted_address"] if g_info else store.address,
                        country_code=store.country_code,
                    )
                except Exception as e:
                    row["note"] += f"미슐랭 조회 실패: {e}; "
                    m_info = MichelinInfo(
                        is_michelin=False, edition_type=EditionType.NONE,
                        latest_grade=MichelinGrade.NONE, is_active=False,
                        summary_badge="조회 오류", reason=str(e)
                    )

                row["is_michelin"] = m_info.is_michelin
                row["edition_type"] = m_info.edition_type.value
                row["latest_grade"] = m_info.latest_grade.value
                row["is_active"] = m_info.is_active
                row["summary_badge"] = m_info.summary_badge
                row["history"] = "; ".join(f"{h.edition}:{h.grade.value}" for h in m_info.history)
                row["source_urls"] = " | ".join(m_info.source_urls)

                print(f"    🏅 {m_info.summary_badge}")

                writer.writerow(row)
                out_f.flush()  # 중간에 중단돼도 여기까지는 파일에 남도록

                time.sleep(random.uniform(*STORE_DELAY))

    print(f"\n처리 완료! 결과 -> '{OUTPUT_FILE}'")

    # CSV는 한글/엑셀 조합에서 인코딩 문제가 종종 있어서, xlsx로도 같이 저장 (엑셀에서 보기엔 이쪽이 안전함)
    try:
        import pandas as pd
        # cid는 19자리 정수라 숫자로 읽으면 엑셀이 15자리까지만 살리고 나머지를 0으로 바꿈 -> 문자열로 유지
        df = pd.read_csv(OUTPUT_FILE, encoding="utf-8-sig", dtype={"google_cid": str})
        xlsx_path = OUTPUT_FILE.replace(".csv", ".xlsx")
        df.to_excel(xlsx_path, index=False)
        print(f"엑셀용 xlsx 파일도 저장했습니다 -> '{xlsx_path}' (한글 깨짐 걱정 없이 이 파일을 여세요)")
    except ImportError:
        print("pandas/openpyxl이 설치되어 있지 않아 xlsx 변환은 건너뜁니다. (pip install pandas openpyxl 후 재실행하면 생성됩니다)")


def convert_existing_csv_to_xlsx():
    """이미 만들어둔 restaurants_info.csv가 있다면, 파이프라인을 다시 돌리지 않고 xlsx로만 변환할 때 사용."""
    if not os.path.exists(OUTPUT_FILE):
        print(f"{OUTPUT_FILE} 파일이 없습니다.")
        return
    import pandas as pd
    df = pd.read_csv(OUTPUT_FILE, encoding="utf-8-sig", dtype={"google_cid": str})
    xlsx_path = OUTPUT_FILE.replace(".csv", ".xlsx")
    df.to_excel(xlsx_path, index=False)
    print(f"변환 완료 -> '{xlsx_path}'")


if __name__ == "__main__":
    main()