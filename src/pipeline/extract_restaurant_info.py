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
import math
import threading
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from enum import Enum
from typing import List, Optional, Dict, Any, Tuple

import requests
import yt_dlp
from video_meta_cache import get_video_meta
from fetch_price_level import FIELDNAMES as PRICE_FIELDS, OUTPUT_FILE as PRICES_FILE, price_fields
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
    UNKNOWN = "UNKNOWN"  # 미슐랭 등재는 확인했지만 등급 근거를 찾지 못함
    NONE = "NONE"


class MichelinYearRecord(BaseModel):
    edition: str = Field(description="연도 또는 에디션명 (예: '2025', '2017 홋카이도 특별판')")
    grade: MichelinGrade = Field(description="획득 등급")


class MichelinLLMResult(BaseModel):
    """LLM이 채우는 부분. 등재 여부·최신 등급·현재 유효 여부는 여기서 받지 않고 current_grade/history로 계산한다."""
    edition_type: EditionType = Field(description="가이드 종류 (REGULAR, SPECIAL, NONE)")
    current_grade: MichelinGrade = Field(
        default=MichelinGrade.NONE,
        description="검색 결과가 이 식당을 '현재' 미슐랭 가이드 등재 식당으로 보여 줄 때의 등급 (연도를 몰라도 됨). "
                    "과거에만 등재됐거나 근거가 없으면 NONE."
    )
    current_evidence: str = Field(
        default="", description="current_grade 의 근거가 된 검색 결과 문장을 그대로 인용. current_grade 가 NONE이면 빈 문자열."
    )
    history: List[MichelinYearRecord] = Field(
        default_factory=list,
        description="정규 도시: 최근 5개년(2021~2025) 이력 / 특별판: 최근 2회 에디션 이력. "
                    "검색 결과에 명시적으로 근거가 없는 연도는 절대 채우지 말 것. "
                    "edition 은 반드시 4자리 연도로 시작할 것."
    )
    in_latest_special_edition: bool = Field(
        default=False, description="특별판일 때만 사용: 마지막 특별판에 등재되었다는 근거가 있는지"
    )
    michelin_url: Optional[str] = Field(default=None, description="공식 링크 (검색 결과 중 guide.michelin.com이 있으면 그 URL)")
    source_urls: List[str] = Field(default_factory=list, description="판정에 실제로 사용한 검색 결과 출처 URL 목록")
    reason: str = Field(description="판정 상세 근거. 근거가 부족하면 그 사실을 명시할 것")


class MichelinInfo(BaseModel):
    """최종 결과. is_michelin / latest_grade / is_active 는 current_grade 와 history 에서 계산한 파생값."""
    is_michelin: bool
    edition_type: EditionType
    latest_grade: MichelinGrade
    is_active: bool
    history: List[MichelinYearRecord] = Field(default_factory=list)
    michelin_url: Optional[str] = None
    source_urls: List[str] = Field(default_factory=list)
    reason: str = ""


# 정규 에디션에서 "현재 등재 중"으로 볼 최소 연도. 새 가이드가 나오면 갱신한다.
ACTIVE_FROM_YEAR = 2025


def _edition_year(edition: str) -> Optional[int]:
    m = re.match(r"\s*(\d{4})", edition)
    return int(m.group(1)) if m else None


# 근거 문장이 해당 등급을 실제로 말하는지 확인하는 패턴 (LLM이 사전 지식으로 등급을 채우는 것을 막는다)
_GRADE_PATTERNS = {
    MichelinGrade.THREE_STARS: r"\b(three|3)[\s-]+(michelin[\s-]+)?stars?\b",
    MichelinGrade.TWO_STARS: r"\b(two|2)[\s-]+(michelin[\s-]+)?stars?\b",
    MichelinGrade.ONE_STAR: r"\b(one|1)[\s-]+(michelin[\s-]+)?star\b",
    MichelinGrade.BIB_GOURMAND: r"bib[\s-]+gourmand",
    MichelinGrade.SELECTED: r"\bselected\b|the plate",
}
# 미슐랭 공식 사이트의 등급별 목록 페이지 URL (예: .../selection/germany/restaurants/3-stars-michelin)
_LIST_PAGE_GRADES = [
    (r"/3-stars?-michelin", MichelinGrade.THREE_STARS),
    (r"/2-stars?-michelin", MichelinGrade.TWO_STARS),
    (r"/1-stars?-michelin", MichelinGrade.ONE_STAR),
    (r"/bib-gourmand", MichelinGrade.BIB_GOURMAND),
]


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").lower()


def grade_from_list_pages(results: List[Dict[str, str]], official_name: str) -> Optional[MichelinGrade]:
    """공식 등급별 목록 페이지에 이 식당명이 있으면 그 등급을 확정한다. 여러 개면 가장 높은 등급."""
    name = _norm(official_name)
    name_re = re.compile(r"(?<![a-z0-9])" + re.escape(name) + r"(?![a-z0-9])") if name else None
    found = []
    for r in results:
        url = r.get("url") or ""
        text = _norm(r.get("raw_content") or r.get("content") or "")
        if name_re is None or not name_re.search(text):
            continue
        # 1스타+2스타를 함께 거른 필터 페이지처럼 URL에 등급 구간이 둘 이상이면 등급을 확정할 수 없으므로 건너뛴다.
        grades = [grade for pattern, grade in _LIST_PAGE_GRADES if re.search(pattern, url)]
        if len(grades) == 1:
            found.append(grades[0])
    order = [MichelinGrade.THREE_STARS, MichelinGrade.TWO_STARS, MichelinGrade.ONE_STAR, MichelinGrade.BIB_GOURMAND]
    return next((g for g in order if g in found), None)


def _verified_grade(llm: MichelinLLMResult, context: str) -> MichelinGrade:
    """LLM 등급을 근거 문장으로 검증한다. 문장이 검색 결과 원문에 없거나 등급 표현이 없으면 UNKNOWN."""
    ev = _norm(llm.current_evidence)
    pattern = _GRADE_PATTERNS.get(llm.current_grade)
    if ev and ev in _norm(context) and pattern and re.search(pattern, ev, re.I):
        return llm.current_grade
    return MichelinGrade.UNKNOWN


def build_michelin_info(llm: MichelinLLMResult, context: str = "",
                        list_grade: Optional[MichelinGrade] = None) -> MichelinInfo:
    """current_grade(근거 검증)·목록 페이지 등급·history 에서 등재 여부·최신 등급·현재 유효 여부를 계산한다.

    history 는 연도 근거가 있는 항목만 남긴다(연도를 알 수 없거나 등급이 NONE/UNKNOWN이면 버림).
    current_grade 는 연도 없이 '현재 등재 중'이라는 근거만 있는 경우를 위한 것이라 history 에는 넣지 않는다.
    """
    dated = []
    for h in llm.history:
        year = _edition_year(h.edition)
        if year is not None and h.grade not in (MichelinGrade.NONE, MichelinGrade.UNKNOWN):
            dated.append((year, h))
    dated.sort(key=lambda x: x[0])
    history = [h for _, h in dated]

    llm_claims_current = llm.current_grade != MichelinGrade.NONE and bool(llm.current_evidence.strip())
    has_current = llm_claims_current or list_grade is not None

    if not history and not has_current:
        return MichelinInfo(
            is_michelin=False, edition_type=EditionType.NONE, latest_grade=MichelinGrade.NONE,
            is_active=False, michelin_url=None, source_urls=[], reason=llm.reason,
        )

    edition_type = llm.edition_type if llm.edition_type != EditionType.NONE else EditionType.REGULAR
    reason = llm.reason
    if has_current:
        if list_grade is not None:
            latest_grade = list_grade
            reason = f"{reason} [공식 등급별 목록 페이지에서 확인: {list_grade.value}]"
        else:
            latest_grade = _verified_grade(llm, context)
            reason = f"{reason} [현재 등재 근거] {llm.current_evidence.strip()}"
        is_active = True
    else:
        latest_year, latest = dated[-1]
        latest_grade = latest.grade
        if edition_type == EditionType.SPECIAL:
            is_active = llm.in_latest_special_edition
        else:
            is_active = latest_year >= ACTIVE_FROM_YEAR
    return MichelinInfo(
        is_michelin=True, edition_type=edition_type, latest_grade=latest_grade, is_active=is_active,
        history=history, michelin_url=llm.michelin_url, source_urls=llm.source_urls, reason=reason,
    )


def michelin_fallback(reason: str) -> MichelinInfo:
    return MichelinInfo(
        is_michelin=False, edition_type=EditionType.NONE, latest_grade=MichelinGrade.NONE,
        is_active=False, reason=reason,
    )


class StoreInfo(BaseModel):
    korean_name: str = Field(description="영상 본문에 언급된 한국어 상호명 (예: 멘야 사이미, 밍글스)")
    address: str = Field(description="가게 주소 또는 도시/지역명")
    country_code: str = Field(default="KR", description="ISO 2자리 국가코드 (대문자: KR, JP, US, FR 등)")
    extra_info: Optional[str] = Field(default=None, description="메뉴, 타임스탬프 등")


class StoreExtractionResult(BaseModel):
    has_store_info: bool = Field(description="가게 정보 포함 여부")
    stores: List[StoreInfo] = Field(default_factory=list, description="추출된 가게 목록")


# -------------------------------------------------------------
# 1.5 숙소 판별 (Places 타입)
# -------------------------------------------------------------
# 식당이 호텔 같은 숙소로 잘못 매칭됐는지 사람이 확인하도록 note 에 표시하는 데만 쓴다.
# 음식 분류(대분류/세부/태그)는 CSV에 저장하지 않고 Gemini 영상 분석 결과를 쓴다.
def is_lodging_place(place: Dict[str, Any]) -> bool:
    primary = place.get("primaryType") or ""
    return "lodging" in (place.get("types") or []) or primary == "lodging" or primary.endswith("hotel")


# 숙소(호텔·료칸 등)로 매칭된 장소는 영상 제목으로 판단한다: 제목에 식당·식사·요리 관련 단어가 있으면 가져오고(식당 방문기),
# 없으면 호텔 후기·료칸 소개가 주 소재라고 보고 제외한다. 해외 호텔 후기 영상에 호텔 정보만 적혀 있는 경우가 많아서다.
FOOD_TITLE_WORDS = ("레스토랑", "식당", "식사", "요리", "밥", "디너", "맛집")
LODGING_NOTE = "숙소로 매칭됨"
LODGING_NEAR_M = 300   # 같은 영상에서 이 거리 안에 다른 식당 행이 있으면 숙소 행은 식당 정보에 딸려 적힌 것으로 보고 뺀다


def has_food_title(title: str) -> bool:
    return any(w in (title or "") for w in FOOD_TITLE_WORDS)


def _row_distance_m(a: Dict[str, Any], b: Dict[str, Any]) -> Optional[float]:
    try:
        la1, lo1, la2, lo2 = (math.radians(float(x)) for x in (
            a["google_latitude"], a["google_longitude"], b["google_latitude"], b["google_longitude"]))
    except (TypeError, ValueError):
        return None
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 2 * 6371000 * math.asin(math.sqrt(h))


def drop_redundant_lodging(rows: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[str]]:
    """같은 영상에서 숙소로 매칭된 행과 같은 주소이거나 근처(LODGING_NEAR_M)에 숙소가 아닌 식당 행이 있으면 그 숙소 행을 뺀다. (뺀 이름 목록도 돌려준다)"""
    kept, dropped = [], []
    for r in rows:
        if LODGING_NOTE in str(r.get("note", "")):
            near = []
            for o in rows:
                if o is r or LODGING_NOTE in str(o.get("note", "")):
                    continue
                d = _row_distance_m(r, o)
                same_address = bool(r.get("google_formatted_address")) and r.get("google_formatted_address") == o.get("google_formatted_address")
                if same_address or (d is not None and d <= LODGING_NEAR_M):   # 같은 주소이거나 근처(거리 0m 포함)
                    near.append(o)
            if near:
                dropped.append(r["korean_name"])
                continue
        kept.append(r)
    return kept, dropped


# -------------------------------------------------------------
# 2. [Step 1] yt-dlp: 유튜브 설명 가져오기
# -------------------------------------------------------------
def get_youtube_description(url: str) -> dict:
    # 2단계(channel_video_filter.py)가 저장한 캐시가 있으면 yt-dlp를 호출하지 않는다
    return get_video_meta(url)


# -------------------------------------------------------------
# 3. [Step 2] OpenAI: 한국 상호명, 주소, 국가코드 추출
# -------------------------------------------------------------
EXCLUDED_COUNTRY = "CN"   # 중국 본토만 제외(HK·MO·TW는 유지)


def clean_country_code(code: Optional[str]) -> str:
    """LLM 이 country_code 에 깨진 문자열을 넣는 경우가 있어(예: 'IT}]} ```...', 'KR},{', 'ES.') 앞의 알파벳 2글자만 남긴다. 없으면 빈 문자열."""
    m = re.match(r"\s*([A-Za-z]{2})(?![A-Za-z])", code or "")
    return m.group(1).upper() if m else ""


def extract_stores_from_description(title: str, description: str) -> StoreExtractionResult:
    if not description.strip() and not title.strip():
        return StoreExtractionResult(has_store_info=False, stores=[])

    prompt = f"""
다음 유튜브 영상의 제목과 설명에서 소개된 음식점의 '한국어 상호명(korean_name)', '주소(address)', '2자리 국가코드(country_code: KR, JP, US, FR 등)'를 추출하세요.
- 여러 가게가 있으면 각각 분리하여 목록으로 만드세요.
- 가게 정보가 없으면 has_store_info를 false로 설정하세요.
- 설명(소개란)에 상호명이 없으면 영상 제목에서 찾으세요(예: 제목의 '| 라미띠에').
- 유튜브 채널명(예: 비밀이야)과 건물·단지 이름은 상호명이 아닙니다. 식당 이름을 찾을 수 없으면 그 가게는 목록에서 제외하세요.
- country_code 는 반드시 대문자 알파벳 2글자만 쓰세요(예: KR). 다른 문자나 설명을 넣지 마세요.

[영상 제목]: {title}
[영상 설명]:
{description}
"""
    response = client.beta.chat.completions.parse(
        model="gpt-4o-mini",
        temperature=0,
        seed=42,
        messages=[
            {"role": "system", "content": "너는 상호명, 주소, 국가코드를 정확하게 구조화하여 추출하는 전문가야."},
            {"role": "user", "content": prompt}
        ],
        response_format=StoreExtractionResult,
    )
    parsed = response.choices[0].message.parsed
    if parsed is None:
        return StoreExtractionResult(has_store_info=False, stores=[])
    for store in parsed.stores:
        store.country_code = clean_country_code(store.country_code)
    return parsed


# -------------------------------------------------------------
# 4. [Step 3] Google Places API (New): 해당 국가의 '공식 현지 상호명' 획득
# -------------------------------------------------------------
def extract_cid(maps_url: Optional[str]) -> str:
    """googleMapsUri(예: https://maps.google.com/?cid=123...)에서 cid 숫자만 추출. 없으면 빈 문자열."""
    match = re.search(r"[?&]cid=(\d+)", maps_url or "")
    return match.group(1) if match else ""


PLACES_CANDIDATES = 5  # 후보를 여러 개 받아 LLM이 고른다 (1개만 받으면 인접한 다른 장소가 그대로 채택됨)


class PlaceChoice(BaseModel):
    chosen_index: Optional[int] = Field(
        default=None, description="후보 번호(0부터). 어느 후보도 같은 장소가 아니면 null"
    )
    reason: str = Field(description="선택 또는 거부 이유 (한 문장)")


def choose_place(korean_name: str, address: Optional[str], country_code: str,
                 places: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Places 후보 중 영상에서 소개한 식당과 같은 장소를 고른다. 없으면 None."""
    lines = []
    for i, p in enumerate(places):
        lines.append(
            f"[{i}] 이름: {p.get('displayName', {}).get('text')} / 주소: {p.get('formattedAddress')} / "
            f"유형: {p.get('primaryTypeDisplayName', {}).get('text') or p.get('primaryType')} / "
            f"평점 수: {p.get('userRatingCount')}"
        )
    prompt = f"""
유튜브 영상 소개란에서 추출한 식당과 같은 장소를 아래 Google Places 후보에서 고르세요.

[추출한 상호명(한국어 표기)]: {korean_name}
[추출한 주소]: {address or '없음'}
[국가]: {country_code}

[후보]
{chr(10).join(lines)}

판단 기준:
- 한국어 상호명은 외국어 이름의 발음 표기입니다. 현지어·영문 이름과 발음이 대응하면 같은 이름으로 보세요.
- 주소가 같거나 가까워도 이름이 다르면 다른 장소입니다. 같은 건물의 호텔과 그 안의 식당은 다른 장소입니다.
- 같은 거리라도 번지수가 다르면 다른 장소입니다. 이름이 발음상 대응하지 않는 후보는 고르지 마세요.
- 소개된 것이 식당인데 후보가 숙소(호텔 등)뿐이면 선택하지 마세요. 상호명 자체가 호텔이면 호텔도 가능합니다.
- 지점명이 있으면(예: 잠실방이점) 그 지점을 고르세요.
- 확신할 수 없으면 null 로 두세요. 틀린 장소를 고르는 것보다 매칭 실패가 낫습니다.
"""
    response = client.beta.chat.completions.parse(
        model="gpt-4o-mini",
        temperature=0,
        seed=42,
        messages=[
            {"role": "system", "content": "너는 식당 이름과 주소를 지도 데이터와 정확히 대조하는 전문가야."},
            {"role": "user", "content": prompt},
        ],
        response_format=PlaceChoice,
    )
    choice = response.choices[0].message.parsed
    if choice is None or choice.chosen_index is None or not (0 <= choice.chosen_index < len(places)):
        print(f"    ⚠️ Google 후보 {len(places)}개 모두 불일치: {choice.reason if choice else '파싱 실패'}")
        return None
    return places[choice.chosen_index]


class LocalName(BaseModel):
    local_name: Optional[str] = Field(
        default=None, description="현지어 또는 영문 원래 상호명. 확신이 없으면 null"
    )


def guess_local_name(korean_name: str, address: Optional[str], country_code: str) -> Optional[str]:
    """한국어 발음 표기에서 현지어/영문 원래 이름을 추정한다 (예: 오스테리아 델 친기알레 비앙코 -> Osteria del Cinghiale Bianco)."""
    prompt = f"""
아래 한국어 식당 이름의 현지어 또는 영문 원래 상호명을 적으세요. 지도 검색에 쓰입니다.
[한국어 표기]: {korean_name}
[주소]: {address or '없음'}
[국가]: {country_code}
확신이 없으면 null 로 두세요. 지어내지 마세요.
"""
    try:
        response = client.beta.chat.completions.parse(
            model="gpt-4o-mini",
            temperature=0,
            seed=42,
            messages=[
                {"role": "system", "content": "너는 외국 식당의 한국어 표기를 원래 이름으로 복원하는 전문가야."},
                {"role": "user", "content": prompt},
            ],
            response_format=LocalName,
        )
        parsed = response.choices[0].message.parsed
        return parsed.local_name.strip() if parsed and parsed.local_name else None
    except Exception:
        return None


def places_text_search(query: str, country_code: str) -> List[Dict[str, Any]]:
    url = "https://places.googleapis.com/v1/places:searchText"
    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": GOOGLE_PLACES_API_KEY,
        "X-Goog-FieldMask": (
            "places.id,places.displayName,places.formattedAddress,"
            "places.rating,places.userRatingCount,places.location,"
            "places.googleMapsUri,places.primaryType,places.primaryTypeDisplayName,places.types,"  # 유형: 후보 선택(choose_place)과 숙소 판별용
            "places.businessStatus,"
            "places.nationalPhoneNumber,places.internationalPhoneNumber,places.websiteUri,"
            "places.regularOpeningHours,"
            # 가격대용. Text Search 가 이미 Enterprise 등급이라 추가 비용 없이 같이 받는다.
            # 지역(addressComponents)은 여기서 받지 않는다: 이 검색은 languageCode 가 없어 영문 지명이 오는데
            # (공식명 비교 때문에 바꿀 수 없음) 지역은 한국어(languageCode=ko)가 필요해 enrich_regions.py 가 Details 로 받는다.
            "places.priceLevel,places.priceRange"
            # 아래는 Enterprise+Atmosphere 등급이라 비용이 더 나가는 필드들. 필요하면 주석 해제.
            # ",places.servesVegetarianFood,places.takeout,places.delivery,places.dineIn,"
            # "places.reservable,places.outdoorSeating,places.goodForChildren,places.allowsDogs,"
            # "places.servesBreakfast,places.servesLunch,places.servesDinner,places.servesBrunch,"
            # "places.servesCoffee,places.servesDessert,places.servesCocktails"
        )
    }
    payload = {"textQuery": query, "maxResultCount": PLACES_CANDIDATES}
    if country_code:
        payload["regionCode"] = country_code.strip().upper()

    try:
        response = requests.post(url, headers=headers, json=payload, timeout=10)
    except requests.RequestException as e:
        print(f"    ⚠️ Google Places 요청 실패: {e}")
        return []

    if response.status_code != 200:
        # Places API (New)가 활성화 안 되어 있거나 키 제한 문제일 수 있어 원인 확인용으로 출력
        print(f"    ⚠️ Google Places 응답 오류 ({response.status_code}): {response.text[:300]}")
        return []
    return response.json().get("places", [])


def search_restaurant_google(korean_name: str, address: Optional[str] = None, country_code: str = "KR") -> Optional[Dict[str, Any]]:
    if not GOOGLE_PLACES_API_KEY:
        return None

    queries = [f"{korean_name} {address}".strip() if address else korean_name]
    if country_code.strip().upper() != "KR":
        # 외국 식당은 한국어 발음 표기로는 Google이 못 찾는 경우가 많아 현지어 이름으로도 검색한다.
        local = guess_local_name(korean_name, address, country_code)
        if local and local.lower() != korean_name.lower():
            queries.append(f"{local} {address}".strip() if address else local)

    places: List[Dict[str, Any]] = []
    seen_ids = set()
    for q in queries:
        for p in places_text_search(q, country_code):
            if p.get("id") not in seen_ids:
                seen_ids.add(p.get("id"))
                places.append(p)
    if not places:
        return None

    place = choose_place(korean_name, address, country_code, places)
    if place is None:
        return None
    return place_to_info(place)


def place_to_info(place: Dict[str, Any]) -> Dict[str, Any]:
    """Places 응답의 장소 1개를 파이프라인에서 쓰는 dict 로 바꾼다."""
    regular_hours = place.get("regularOpeningHours", {}) or {}
    return {
        "place_id": place.get("id"),
        "official_local_name": place.get("displayName", {}).get("text"),
        "formatted_address": place.get("formattedAddress"),
        "rating": place.get("rating"),
        "user_rating_count": place.get("userRatingCount"),
        "lat": place.get("location", {}).get("latitude"),
        "lng": place.get("location", {}).get("longitude"),
        "cid": extract_cid(place.get("googleMapsUri")),
        "maps_url": place.get("googleMapsUri", ""),
        "business_status": place.get("businessStatus", ""),
        "phone": place.get("internationalPhoneNumber") or place.get("nationalPhoneNumber", ""),
        "website": place.get("websiteUri", ""),
        # weekdayDescriptions: ["Monday: 11:00 AM – 9:00 PM", ...] 형태의 사람이 읽기 좋은 문자열 리스트
        "opening_hours": " | ".join(regular_hours.get("weekdayDescriptions", [])),
        "is_lodging": is_lodging_place(place),
        "price_data": {"priceLevel": place.get("priceLevel"), "priceRange": place.get("priceRange")},
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


def check_michelin_status_tavily(official_name: str, korean_name: str, address: Optional[str] = None, country_code: str = "KR") -> MichelinInfo:
    if not TAVILY_API_KEY:
        return michelin_fallback("TAVILY_API_KEY 없음")

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
        return michelin_fallback(f"Tavily 검색 에러: {e}")

    searched_urls = {r["url"] for r in all_results if r.get("url")}

    def _best_text(r: Dict[str, str]) -> str:
        # raw_content(전체 텍스트)가 있으면 그쪽을 우선 쓰고, 없으면 짧은 스니펫(content)로 대체
        raw = (r.get("raw_content") or "").strip()
        if raw:
            # 페이지 앞부분은 네비게이션인 경우가 많아, 식당명이 처음 나오는 위치 근처부터 잘라 쓴다.
            idx = raw.lower().find(official_name.lower())
            start = max(0, idx - 300) if idx >= 0 else 0
            return raw[start:start + RAW_CONTENT_TRUNCATE]
        return r.get("content", "")

    context = "\n\n".join(
        f"출처: {r.get('title')}\nURL: {r.get('url')}\n내용: {_best_text(r)}" for r in all_results
    )

    prompt = f"""
당신은 전 세계 미슐랭 가이드 레스토랑 데이터 분석 전문가입니다.
아래 검색 결과만 근거로, 식당 [{official_name} / 한국명: {korean_name}] (주소: {address or '미지정'}, 국가: {country_code})의
미슐랭 등재 정보를 두 단계로 판별하세요.

[1단계: 현재 등재 등급 -> current_grade, current_evidence]
검색 결과가 이 식당을 미슐랭 가이드에 등재된 식당으로 소개하면(예: "retained its Three Michelin Stars",
"a MICHELIN Guide restaurant", 공식 사이트의 '3 Stars' 같은 등급별 목록에 이 식당이 나옴, 상세 페이지에 등급 표시)
그 등급을 current_grade 에 넣고, 근거가 된 문장을 current_evidence 에 그대로 인용하세요. 연도를 몰라도 됩니다.
등급 없이 'Selected'(가이드 수록)만 확인되면 SELECTED, 빕 구르망이면 BIB_GOURMAND 입니다.
과거에만 등재되었다거나 제외·폐점을 언급하면 NONE 입니다.
인용할 문장을 찾을 수 없으면 NONE 으로 두세요.

[2단계: 연도별 이력 -> history]
검색 결과에 연도와 등급이 함께 명시된 경우에만 history 에 넣으세요. edition 은 4자리 연도로 시작합니다
(예: "2025", "2017 홋카이도 특별판"). 연도가 명시되지 않았다면 지어내지 말고 history 는 비워 두세요.
- 정규 도시 (서울, 도쿄, 파리, 뉴욕 등): 확인되는 연도만 (최대 2021~2025)
- 비정기 특별판 지역 (삿포로/홋카이도, 규슈 등): 확인되는 특별판만 넣고, 마지막 특별판 등재 근거가 있으면
  in_latest_special_edition = True

[공통 규칙]
- 이 식당(이름·주소·도시)에 대한 내용만 근거로 쓰세요. 같은 이름의 다른 지점·다른 도시 식당 페이지는
  근거로 쓰지 말고 source_urls 에도 넣지 마세요.
- 어떤 등급도 찾지 못하면 edition_type = NONE, current_grade = NONE, history 비움, reason 에 "근거 부족"을 적으세요.
- 등재 여부·최신 등급·유효 여부는 코드가 계산하므로 따로 답하지 않습니다.

[검색 데이터]:
{context if context else "검색 결과 없음"}
"""

    response = client.beta.chat.completions.parse(
        model="gpt-4o-mini",
        temperature=0,
        seed=42,
        messages=[
            {"role": "system", "content": "너는 미슐랭 가이드 공식 데이터 분석 AI야. 근거 없는 추측은 하지 않아."},
            {"role": "user", "content": prompt}
        ],
        response_format=MichelinLLMResult,
    )
    llm_result = response.choices[0].message.parsed
    if llm_result is None:
        return michelin_fallback("LLM 파싱 실패")
    result = build_michelin_info(
        llm_result, context=context, list_grade=grade_from_list_pages(all_results, official_name)
    )

    # 검색 결과 전체로 보강하지 않는다(다른 지역 식당 페이지가 섞임). LLM이 고른 URL 중 실제 검색 결과에 있던 것만 남긴다.
    result.source_urls = [u for u in result.source_urls if u in searched_urls]
    return result


# -------------------------------------------------------------
# 5-1. [Step 4 기본] 미슐랭 판별: Parse(guide.michelin.com) API
# -------------------------------------------------------------
# API는 "현재" 등급(distinction)만 주고 연도별 이력은 주지 않는다. 그래서 연도별 이력은 저장하지 않는다.
# 같은 이름의 다른 도시 지점이 검색되므로 이름뿐 아니라 도시(또는 거리 주소)까지 일치하는 후보만 채택한다.
MICHELIN_API_LIMIT = 5            # 검색 1회당 가져올 후보 수. 호출마다 크레딧을 쓴다.
MICHELIN_MIN_INTERVAL = 12.5      # 무료 플랜 분당 5회 제한을 지키는 호출 간격(초). 워커 간에 공유한다.
MICHELIN_TAVILY_FALLBACK = False  # True 면 API에서 일치 후보가 없을 때 기존 Tavily 경로로 한 번 더 확인한다(--michelin-fallback).

_DISTINCTION_TO_GRADE = {
    "3-stars-michelin": MichelinGrade.THREE_STARS,
    "2-stars-michelin": MichelinGrade.TWO_STARS,
    "1-star-michelin": MichelinGrade.ONE_STAR,
    "bib-gourmand": MichelinGrade.BIB_GOURMAND,
    "the-plate-michelin": MichelinGrade.SELECTED,
}
SKIP_MICHELIN = False             # True 면 미슐랭을 조회하지 않고 note 에 표시만 한다. 나중에 fill_michelin.py 로 채운다(--skip-michelin)
MICHELIN_PENDING_NOTE = "미슐랭 미조회; "
MICHELIN_MAX_CALLS = None         # 이번 실행에서 Michelin API를 호출할 최대 횟수(크레딧 보호). None 이면 제한 없음 (--michelin-max-calls)
_michelin_client = None
_michelin_lock = threading.Lock()
_michelin_last_call = 0.0
_michelin_calls = 0


def _name_key(text: Optional[str]) -> str:
    """이름 비교용 정규화: 악센트 제거, 소문자, 문자·숫자만 남긴다."""
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"[^0-9a-z぀-ヿ㐀-鿿가-힣]", "", text.lower())


def _name_matches(a: Optional[str], b: Optional[str]) -> bool:
    ka, kb = _name_key(a), _name_key(b)
    if not ka or not kb:
        return False
    if ka == kb:
        return True
    shorter, longer = sorted((ka, kb), key=len)
    return len(shorter) >= 4 and shorter in longer   # "Born and Bred" <-> "Born and Bred Busan"


def _street_matches(street: Optional[str], address: Optional[str]) -> bool:
    """미슐랭 거리 주소의 숫자 토큰이 모두, 영문 토큰이 절반 이상 Places 주소에 있으면 같은 곳으로 본다."""
    s_tokens = re.findall(r"[a-z0-9]+", _name_key_ascii(street))
    a_tokens = set(re.findall(r"[a-z0-9]+", _name_key_ascii(address)))
    digits = [t for t in s_tokens if t.isdigit()]
    words = [t for t in s_tokens if not t.isdigit() and len(t) >= 3]
    if not digits or not all(d in a_tokens for d in digits):
        return False
    return not words or sum(w in a_tokens for w in words) / len(words) >= 0.5


def _name_key_ascii(text: Optional[str]) -> str:
    text = unicodedata.normalize("NFKD", text or "")
    return "".join(c for c in text if not unicodedata.combining(c)).lower()


def _location_matches(r, address: Optional[str]) -> bool:
    """미슐랭의 도시나 지역 이름이 Places 주소에 들어 있으면 같은 지역으로 본다."""
    addr = _name_key(address)
    for place in (r.city.name, r.region.name):
        if _name_key(place) and _name_key(place) in addr:
            return True
    return False


def pick_michelin_candidate(candidates: list, official_name: str, korean_name: str,
                            address: Optional[str], country_code: str):
    """검색 결과 중 같은 식당으로 볼 수 있는 후보를 고른다. 없으면 None. 거리 일치 > 이름 완전 일치 > 검색 순위 순."""
    best, best_score = None, -1
    for r in candidates:
        name_ok = _name_matches(r.name, official_name) or _name_matches(r.name, korean_name)
        loc_ok = _location_matches(r, address)
        street_ok = _street_matches(getattr(r, "street", None), address)
        same_country = bool(r.country and r.country.code and r.country.code.upper() == country_code.strip().upper())
        if not ((name_ok and loc_ok) or (street_ok and loc_ok) or (name_ok and street_ok)
                or (name_ok and same_country and _name_key(r.name) == _name_key(official_name))):
            continue
        score = (2 if street_ok else 0) + (1 if _name_key(r.name) == _name_key(official_name) else 0)
        if score > best_score:
            best, best_score = r, score
    return best


def _michelin_search(query: str):
    """Parse API 검색(분당 호출 수 제한을 지킨다). 오류는 그대로 올려서 호출부가 기록하게 한다."""
    global _michelin_client, _michelin_last_call, _michelin_calls
    if not os.getenv("MICHELIN_GUIDE_API_KEY"):
        raise RuntimeError("MICHELIN_GUIDE_API_KEY 없음")
    with _michelin_lock:
        if MICHELIN_MAX_CALLS is not None and _michelin_calls >= MICHELIN_MAX_CALLS:
            raise RuntimeError(f"Michelin API 호출 한도({MICHELIN_MAX_CALLS}회) 도달: 크레딧 보호를 위해 조회하지 않음")
        _michelin_calls += 1
        if _michelin_client is None:
            os.environ.setdefault("PARSE_API_KEY", os.environ["MICHELIN_GUIDE_API_KEY"])
            from parse_apis.guide_michelin_com_api import MichelinGuide
            _michelin_client = MichelinGuide()
        wait = MICHELIN_MIN_INTERVAL - (time.time() - _michelin_last_call)
        if wait > 0:
            time.sleep(wait)
        try:
            return list(_michelin_client.restaurants.search(query=query, limit=MICHELIN_API_LIMIT))
        finally:
            _michelin_last_call = time.time()
            meta = getattr(_michelin_client, "last_meta", None)
            remaining = getattr(meta, "credits_remaining", None)
            if remaining is not None:
                print(f"    (Michelin API 남은 크레딧: {remaining})")


def check_michelin_status_api(official_name: str, korean_name: str, address: Optional[str] = None,
                              country_code: str = "KR") -> MichelinInfo:
    candidates = _michelin_search(official_name)
    picked = pick_michelin_candidate(candidates, official_name, korean_name, address, country_code)
    if picked is None:
        return MichelinInfo(
            is_michelin=False, edition_type=EditionType.NONE, latest_grade=MichelinGrade.NONE, is_active=False,
            reason=f"Michelin API 검색 결과 {len(candidates)}건 중 이름·도시가 일치하는 식당 없음",
        )
    slug = picked.distinction.slug if picked.distinction else None
    grade = _DISTINCTION_TO_GRADE.get(slug, MichelinGrade.UNKNOWN)
    url = f"https://guide.michelin.com/en/{picked.region.slug}/{picked.city.slug}/restaurant/{picked.slug}"
    return MichelinInfo(
        is_michelin=True, edition_type=EditionType.REGULAR, latest_grade=grade, is_active=True,
        michelin_url=url, source_urls=[url],
        reason=f"Michelin API: {picked.name} / {picked.city.name} / distinction={slug}",
    )


def check_michelin_status(official_name: str, korean_name: str, address: Optional[str] = None,
                          country_code: str = "KR") -> MichelinInfo:
    """Parse API로 현재 등급을 확정한다. 일치 후보가 없을 때만, 옵션이 켜져 있으면 기존 Tavily 경로로 한 번 더 본다."""
    info = check_michelin_status_api(official_name, korean_name, address, country_code)
    if not info.is_michelin and MICHELIN_TAVILY_FALLBACK:
        return check_michelin_status_tavily(official_name, korean_name, address, country_code)
    return info


# -------------------------------------------------------------
# 6. 배치 파이프라인 실행
# -------------------------------------------------------------
FIELDNAMES = [
    "video_url", "video_title",
    "korean_name", "address", "country_code",
    "google_official_name", "google_formatted_address",
    "google_rating", "google_user_rating_count", "google_cid", "google_maps_url", "google_place_id",
    "google_latitude", "google_longitude",
    "google_business_status", "google_phone", "google_website", "google_opening_hours",
    "is_michelin", "latest_grade",
    "source_urls", "note",
]


def apply_michelin(row: Dict[str, Any], m_info: "MichelinInfo") -> None:
    """미슐랭 판별 결과를 CSV 행에 채운다. process_video 와 fill_michelin.py 가 같이 쓴다."""
    row["is_michelin"] = m_info.is_michelin
    row["latest_grade"] = m_info.latest_grade.value
    row["source_urls"] = " | ".join(m_info.source_urls)


def fill_google_fields(row: Dict[str, Any], g_info: Dict[str, Any]) -> None:
    """Google Places 결과를 CSV 행에 채운다. process_video 와 수동 수락(매칭 보정)이 같이 쓴다."""
    row["google_official_name"] = g_info.get("official_local_name", "")
    row["google_formatted_address"] = g_info.get("formatted_address", "")
    row["google_rating"] = g_info.get("rating", "")
    row["google_user_rating_count"] = g_info.get("user_rating_count", "")
    row["google_cid"] = g_info.get("cid", "")
    row["google_maps_url"] = g_info.get("maps_url", "")
    row["google_place_id"] = g_info.get("place_id") or ""
    row["google_latitude"] = g_info["lat"] if g_info.get("lat") is not None else ""
    row["google_longitude"] = g_info["lng"] if g_info.get("lng") is not None else ""
    row["google_business_status"] = g_info.get("business_status", "")
    row["google_phone"] = g_info.get("phone", "")
    row["google_website"] = g_info.get("website", "")
    row["google_opening_hours"] = g_info.get("opening_hours", "")
    # 가격은 FIELDNAMES(22컬럼)에 없고 main 이 별도 CSV(restaurant_prices_sample.csv)에 기록한다
    row["_price_data"] = g_info.get("price_data") or {}
    if g_info.get("is_lodging"):
        row["note"] += LODGING_NOTE + " 확인 필요: 식당이 아닌 호텔 등으로 매칭됐을 수 있음; "


def process_video(url: str, v_idx: int, total: int) -> Optional[List[Dict[str, Any]]]:
    """영상 1개를 처리해 CSV 행 목록을 반환한다. 일시적 오류(영상 조회/상호명 추출 실패)면 None (기록하지 않아 재실행 시 재시도)."""
    tag = f"[{v_idx}/{total}]"
    print(f"{tag} 시작: {url}")

    try:
        video_data = get_youtube_description(url)
    except Exception as e:
        print(f"{tag} ⚠️ 영상 정보 조회 실패(기록하지 않음, 재실행 시 재시도): {e}")
        return None

    if not video_data.get('cached'):
        time.sleep(random.uniform(*YTDLP_DELAY))
    print(f"{tag} 제목: {video_data['title']}")

    try:
        extracted = extract_stores_from_description(video_data['title'], video_data['description'])
    except Exception as e:
        print(f"{tag} ⚠️ 상호명 추출 실패(기록하지 않음, 재실행 시 재시도): {e}")
        return None

    if not extracted.has_store_info or not extracted.stores:
        print(f"{tag} ℹ️ 가게 정보 없음")
        return [{**{k: "" for k in FIELDNAMES}, "video_url": url,
                 "video_title": video_data['title'], "note": "가게 정보 없음"}]

    rows = []
    excluded: List[str] = []   # 숙소 중심 영상이라 제외한 가게
    excluded_cn: List[str] = []   # 중국 소재라 제외한 가게
    for store in extracted.stores:
        # 중국 본토(CN)는 대상에서 뺀다. 홍콩(HK)·마카오(MO)·대만(TW)은 유지한다. Places 호출 전에 걸러 비용도 아낀다.
        if store.country_code == EXCLUDED_COUNTRY:
            excluded_cn.append(store.korean_name)
            print(f"{tag} 식당: {store.korean_name} ({store.country_code}) ⏭️ 중국 소재라 제외")
            continue
        row = {k: "" for k in FIELDNAMES}
        row["video_url"] = url
        row["video_title"] = video_data['title']
        row["korean_name"] = store.korean_name
        row["address"] = store.address
        row["country_code"] = store.country_code

        print(f"{tag} 식당: {store.korean_name} ({store.country_code}) / {store.address}")

        g_info = None
        try:
            g_info = search_restaurant_google(store.korean_name, store.address, store.country_code)
        except Exception as e:
            row["note"] += f"Google 조회 실패: {e}; "

        if g_info and g_info.get("is_lodging") and not has_food_title(video_data["title"]):
            excluded.append(store.korean_name)
            print(f"{tag}   ⏭️ 숙소 중심 영상이라 제외: {store.korean_name} -> {g_info.get('official_local_name')}")
            continue

        official_name = store.korean_name
        if g_info:
            official_name = g_info["official_local_name"] or store.korean_name
            fill_google_fields(row, g_info)
            print(f"{tag}   구글 공식명: {official_name} / {g_info.get('formatted_address')}")
        else:
            row["note"] += "Google 매칭 실패(또는 후보 불일치): 사람이 확인 필요; "
            print(f"{tag}   ⚠️ Google Places 매칭 실패 ({store.korean_name})")

        if SKIP_MICHELIN:
            row["note"] += MICHELIN_PENDING_NOTE
            print(f"{tag}   미슐랭: 미조회 (fill_michelin.py 로 나중에 채움)")
        else:
            try:
                m_info = check_michelin_status(
                    official_name=official_name,
                    korean_name=store.korean_name,
                    address=g_info["formatted_address"] if g_info else store.address,
                    country_code=store.country_code,
                )
            except Exception as e:
                row["note"] += f"미슐랭 조회 실패: {e}; "
                m_info = michelin_fallback(str(e))
            apply_michelin(row, m_info)
            print(f"{tag}   미슐랭: {m_info.latest_grade.value} (등재={m_info.is_michelin}, 현재유효={m_info.is_active})")

        rows.append(row)
        time.sleep(random.uniform(*STORE_DELAY))

    rows, redundant = drop_redundant_lodging(rows)
    for name in redundant:
        print(f"{tag}   ⏭️ 같은 영상의 식당 근처 숙소 행이라 제외: {name}")
    excluded += redundant
    if not rows:   # 모든 가게가 제외되면 영상 자체는 처리한 것으로 남기도록 안내 행을 한 줄 둔다
        notes = []
        if excluded:
            notes.append("숙소 중심 영상이라 제외(제목 기준): " + ", ".join(excluded))
        if excluded_cn:
            notes.append("중국 소재라 제외: " + ", ".join(excluded_cn))
        rows = [{**{k: "" for k in FIELDNAMES}, "video_url": url, "video_title": video_data["title"],
                 "note": "; ".join(notes)}]
    return rows


class PriceWriter:
    """Text Search 로 함께 받은 가격대를 restaurant_prices_sample.csv 에 건별로 덧붙인다.

    fetch_price_level.py 와 같은 컬럼이라 load_prices.py 를 그대로 쓴다. 이미 기록된 google_place_id 는 다시 쓰지 않는다.
    --output 이 기본 경로가 아니면 같은 위치에 `<이름>_prices.csv` 로 쓴다(테스트 실행이 실제 파일을 건드리지 않게).
    """

    def __init__(self, output_file: str):
        if os.path.abspath(output_file) == os.path.abspath(OUTPUT_FILE):
            path = PRICES_FILE
        else:
            path = Path(os.path.splitext(output_file)[0] + "_prices.csv")
        path = Path(path)
        exists = path.exists() and path.stat().st_size > 0
        self._done = set()
        if exists:
            with open(path, "r", encoding="utf-8-sig", newline="") as f:
                reader = csv.DictReader(f)
                if reader.fieldnames != PRICE_FIELDS:
                    raise SystemExit(f"오류: {path} 의 컬럼이 현재 스키마와 다릅니다. 파일을 옮기고 다시 실행해 주세요.")
                self._done = {r["google_place_id"] for r in reader}
        self._file = open(path, "a" if exists else "w", encoding="utf-8-sig", newline="")
        self._writer = csv.DictWriter(self._file, fieldnames=PRICE_FIELDS)
        if not exists:
            self._writer.writeheader()

    def write(self, rows: List[Dict[str, Any]]) -> None:
        for row in rows:
            pid = row.get("google_place_id")
            if not pid or "_price_data" not in row or pid in self._done:
                continue
            self._writer.writerow({
                "google_cid": row["google_cid"], "google_place_id": pid, "korean_name": row["korean_name"],
                "country_code": row["country_code"], **price_fields(row["_price_data"]), "status": "ok"})
            self._done.add(pid)
        self._file.flush()

    def close(self) -> None:
        self._file.close()


def main(output_file: str = OUTPUT_FILE, limit: Optional[int] = None, workers: int = 3,
         video_ids: Optional[List[str]] = None):
    if video_ids:
        urls = [f"https://www.youtube.com/watch?v={v}" for v in video_ids]
    else:
        if not os.path.exists(INPUT_FILE):
            print(f"오류: {INPUT_FILE} 파일이 없습니다.")
            return

        with open(INPUT_FILE, "r", encoding="utf-8") as f:
            urls = [line.strip() for line in f if line.strip()]

    if limit:
        urls = urls[:limit]

    # 이어받기: 출력 파일에 이미 기록된 영상은 건너뛰고 뒤에 이어서 쓴다 (영상 단위).
    # 처음부터 다시 하려면 출력 파일을 지우거나 --output 으로 다른 경로를 지정한다.
    done_urls = set()
    resume = os.path.exists(output_file) and os.path.getsize(output_file) > 0
    if resume:
        with open(output_file, "r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            if reader.fieldnames != FIELDNAMES:
                print(f"오류: {output_file} 의 컬럼이 현재 스키마와 다릅니다. 다른 --output 을 쓰거나 파일을 옮겨 주세요.")
                return
            done_urls = {row["video_url"] for row in reader}

    todo = [(i, u) for i, u in enumerate(urls, start=1) if u not in done_urls]
    print(f"대상 {len(urls)}개 중 이미 처리된 {len(urls) - len(todo)}개는 건너뛰고, {len(todo)}개를 워커 {workers}개로 처리합니다.")

    # 워커 스레드가 동시에 openai/httpx 를 처음 불러오면 순환 import 오류('partially initialized module httpx')가 나므로
    # 메인 스레드에서 OpenAI 호출을 한 번 해 모듈을 미리 로드한다. 실패해도 무시한다.
    try:
        client.beta.chat.completions.parse(model="gpt-4o-mini", messages=[{"role": "user", "content": "ok"}],
                                           response_format=LocalName, max_completion_tokens=16)
    except Exception:
        pass

    failed = 0
    started = time.time()
    with open(output_file, "a" if resume else "w", encoding="utf-8-sig", newline="") as out_f:
        writer = csv.DictWriter(out_f, fieldnames=FIELDNAMES, extrasaction="ignore")
        if not resume:
            writer.writeheader()
        side = PriceWriter(output_file)

        # 영상 단위로 병렬 처리한다. CSV 쓰기는 이 메인 스레드에서만 하므로 파일이 섞이지 않는다.
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futures = {ex.submit(process_video, u, i, len(urls)): u for i, u in todo}
            for done_count, fut in enumerate(as_completed(futures), start=1):
                try:
                    rows = fut.result()
                except Exception as e:
                    print(f"⚠️ 예기치 못한 오류 ({futures[fut]}): {e}")
                    rows = None
                if rows is None:
                    failed += 1
                    continue
                writer.writerows(rows)
                side.write(rows)
                out_f.flush()  # 중간에 중단돼도 여기까지는 파일에 남도록
                print(f"--- 진행 {done_count}/{len(todo)} 완료, 경과 {int(time.time() - started)}초")

    side.close()
    if failed:
        print(f"재시도 필요한 영상 {failed}개 (일시적 오류): 같은 명령을 다시 실행하면 이어서 처리합니다.")
    print(f"처리 완료! 결과 -> '{output_file}'")

    # CSV는 한글/엑셀 조합에서 인코딩 문제가 종종 있어서, xlsx로도 같이 저장 (엑셀에서 보기엔 이쪽이 안전함)
    try:
        import pandas as pd
        # cid는 19자리 정수라 숫자로 읽으면 엑셀이 15자리까지만 살리고 나머지를 0으로 바꿈 -> 문자열로 유지
        df = pd.read_csv(output_file, encoding="utf-8-sig", dtype={"google_cid": str})
        xlsx_path = output_file.replace(".csv", ".xlsx")
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
    import argparse
    ap = argparse.ArgumentParser(description="영상 소개란에서 식당 정보를 추출하고 Places/미슐랭을 조회한다.")
    ap.add_argument("--output", default=OUTPUT_FILE, help="결과 CSV 경로 (기본: data/restaurants_info.csv)")
    ap.add_argument("--limit", type=int, default=None, help="앞에서부터 N개 영상만 처리 (샘플 검증용)")
    ap.add_argument("--workers", type=int, default=3, help="병렬 처리할 영상 수 (기본 3, API 속도 제한에 주의)")
    ap.add_argument("--video-id", action="append", default=[], help="입력 목록 대신 지정한 영상 ID만 처리 (여러 번 지정 가능)")
    ap.add_argument("--michelin-fallback", action="store_true",
                    help="Michelin API에서 일치 식당이 없을 때 기존 Tavily 검색으로 한 번 더 확인 (호출·비용 증가)")
    ap.add_argument("--skip-michelin", action="store_true",
                    help="미슐랭을 조회하지 않는다(Parse API 하루 100회 제한 때문). 나중에 fill_michelin.py 로 하루 한도만큼씩 채운다")
    ap.add_argument("--michelin-max-calls", type=int, default=None,
                    help="이번 실행에서 Michelin API를 호출할 최대 횟수. 넘으면 그 식당의 미슐랭 조회는 실패로 기록(note)하고 건너뜀")
    args = ap.parse_args()
    MICHELIN_TAVILY_FALLBACK = args.michelin_fallback
    MICHELIN_MAX_CALLS = args.michelin_max_calls
    SKIP_MICHELIN = args.skip_michelin
    main(output_file=args.output, limit=args.limit, workers=args.workers, video_ids=args.video_id or None)