"""
YouTube 영상을 Gemini에 직접 넘겨(음성+화면) 식당 정보를 추출한다. 자동 생성 자막 영상용(자막 오타가 많아 자막 방식을 쓰지 않는다).

extract_script_split.py(자막 방식)와 같은 기준으로 같은 키 구조의 JSON을 만든다. 그래서 postprocess_split.py --tag,
load_notes.py --model, embed_chunks.py, load_chefs.py --notes-tag 를 그대로 쓴다.
- 메뉴 설명 칸은 cooking_features 하나(평가어·가격·구어체 없이 정리된 평서문). 코스 여부와 요리별 course_stage, 요리 순서(order)를 함께 뽑는다.
- 자막은 쓰지 않는다. 화면에 음식이 처음 보이는 시각은 first_appearance(MM:SS)이고, 영상 길이(yt-dlp, data/video_durations.json 캐시)를
  넘거나 형식이 틀리면 null 로 버린다. load_notes.py 가 이 값을 menus.mentioned_sec 하나에 저장한다(자막 방식의 mentioned_at 과 같은 칸).
- 셰프(오너·총괄)도 같은 호출에서 영상으로 뽑는다. 확신도는 코드가 정한다: 이름이 영상 제목·소개란에 있으면 high, 아니면 medium.
- 메뉴가 하나도 없는 가게는 결과를 저장하지 않고 data/video_notes/_excluded/{TAG}/ 에 사유와 함께 남긴다(다시 호출하지 않는다).
- 한 영상 = Gemini 호출 1회. 영상에 가게가 여러 곳이면 가게마다 항목을 하나씩 받는다(google_cid 로 구분).
- 결과: data/video_notes/{TAG}/{video_id}__{google_cid}.json. 이미 있는 가게는 건너뛰므로 중단 후 재실행하면 이어서 처리된다.
- 무료 등급은 YouTube 영상을 하루 8시간까지만 받는다. 429/5xx 는 대기 시간을 두 배씩 늘려 재시도하고, 연속 3번 실패하면 멈춘다.

실행 예 (대상 영상 ID 목록 파일 data/urls/auto_2026_ids.txt):
  uv run python src/pipeline/extract_video_split.py --ids-file data/urls/auto_2026_ids.txt --dry-run
  uv run python src/pipeline/extract_video_split.py --video-id=QIIvV21rErU --video-id=8swEJ-ZATnI     # ID가 -로 시작하면 =로 붙인다
  uv run python src/pipeline/extract_video_split.py --ids-file data/urls/auto_2026_ids.txt --limit 5   # 처리 안 된 영상 5개씩 이어서

후처리·적재 (TARGETS_FILE 은 postprocess_split 이 가게 정보를 찾는 데 필요하다):
  TARGETS_FILE=data/urls/auto_2026_ids.txt uv run python src/pipeline/postprocess_split.py --tag gemini-3.5-flash-lite-split-v1
  uv run python src/db/load_notes.py --model gemini-3.5-flash-lite-split-v1
"""

import argparse
import csv
import json
import os
import re
import time
from pathlib import Path
from typing import List, Literal, Optional

from dotenv import load_dotenv
from google import genai
from google.genai import errors as genai_errors
from google.genai import types
from pydantic import BaseModel, Field

from common import GARBAGE_NAMES, GENERIC_NAMES, GENERIC_SUFFIX, MAX_CONSECUTIVE_FAILS, ROOT, STORES_CSV, Drink, _chef_norm, describe, video_id_of
from extract_script_split import CourseStage, clean_menus

load_dotenv()

TAG = "gemini-3.5-flash-lite-split-v1"
MODEL = "gemini-3.5-flash-lite"
MAX_RETRIES = 5
RETRY_WAIT = 30  # 첫 재시도 대기 초. 이후 두 배씩(30, 60, 120, 240초)
RETRY_CODES = {429, 500, 503, 504}
DURATION_FILE = ROOT / "data" / "video_durations.json"  # 영상 길이(초) 캐시. 시각 검증에만 쓴다
DESC_DIR = ROOT / "data" / "descriptions"                # 영상 제목·소개란 캐시(셰프 확신도 계산에 쓴다)
END_MARGIN_SEC = 5

client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))


# -------------------------------------------------------------
# 1. 출력 스키마 (extract_script_split 의 StoreInfo + MenuInfo 와 같은 키, 시각만 first_appearance)
# -------------------------------------------------------------
class VideoMenu(BaseModel):
    name: str = Field(description="구체적인 요리명. 범주 이름(디저트, 메인, 고기)은 제외")
    cooking_features: Optional[str] = Field(
        None,
        description="이 요리에 대한 설명 전부를 요약한 정리된 평서문 1~4문장: 재료, 조리법, 소스, 제공 방식, 맛·식감·향 묘사, 다른 음식에 빗댄 말, "
        "유튜버·직원이 말한 평가 중 구체적인 이유가 있는 것. 감탄만 있는 말은 제외. 설명이 영상에 없으면 null",
    )
    evidence: str = Field(description="이 메뉴 정보의 근거가 된 발언 원문 1~2개. 화면 근거만 있으면 '(화면) ...' 형식. 시각은 붙이지 않는다")
    first_appearance: Optional[str] = Field(
        None,
        description="이 음식이 화면에 실제로 처음 보이는 시각(조리 장면이나 서빙된 접시). 메뉴판·자막·메뉴명 언급은 해당하지 않는다. "
        "MM:SS 형식, 1시간이 넘으면 H:MM:SS. 음식이 화면에 보이지 않으면 null",
    )
    order: int = Field(description="이 가게에서 요리가 나온 순서(1부터)")
    course_stage: Optional[CourseStage] = Field(
        None, description="코스 요리일 때 이 요리의 코스 단계. 말했거나 요리 성격으로 분명할 때만 적고, 단품이거나 판단이 안 되면 null"
    )


class VideoChef(BaseModel):
    name: str = Field(description="셰프 이름. '셰프님' 같은 칭호는 빼고 한국어 표기로 적는다. 영상에서 말하거나 화면에 나온 이름만, 영상 제목·[대상 가게] 표기를 우선한다")
    role: Literal["owner_chef", "head_chef"] = Field(description="owner_chef: 오너 셰프·대표 / head_chef: 총괄·헤드 셰프")
    background: Optional[str] = Field(
        None, description="영상에서 소개한 경력·수상·방송 출연·요리 철학을 1~2문장으로. '영상에서 ~라고 소개' 형태가 아니라 내용만. 없으면 null"
    )
    evidence: str = Field(description="이 셰프가 이 가게의 대표 셰프라고 판단한 근거 발언 원문 1~2개(화면이면 '(화면) ...'). 시각은 붙이지 않는다")


class StoreVideoNotes(BaseModel):
    google_cid: str = Field(description="[대상 가게] 목록에서 이 정보가 속한 가게의 google_cid. 목록에 적힌 값을 그대로 쓴다")
    restaurant_name: Optional[str] = Field(None, description="[대상 가게]에 적힌 이름")
    location_hint: Optional[str] = Field(None, description="영상에서 언급된 동네, 랜드마크 등")
    concept: Optional[str] = Field(None, description="식당 종류/컨셉 (예: 클래식 비스트로, 짬뽕 전문점)")
    category_broad: Literal["한식", "일식", "중식", "양식", "동남아식", "인도식", "중동식", "기타"] = Field(
        "기타", description="음식 대분류. 양식은 유럽·아메리카 요리 전체. 해산물 전문점처럼 나라로 나눌 수 없으면 기타"
    )
    category_detail: Optional[str] = Field(None, description="세부 음식 카테고리를 딱 하나, 짧은 구로 (예: 프렌치, 완탕면). 쉼표로 여러 개를 쓰지 않는다")
    cuisine_tags: List[str] = Field(default_factory=list, description="음식·조리 키워드 3~5개. 술, 와인, 재료 일반명 제외")
    atmosphere: Optional[str] = Field(None, description="공간, 좌석, 뷰, 인테리어, 손님층, 서비스, 주문·운영 방식 1~2문장")
    key_points: List[str] = Field(default_factory=list, description="이 식당만의 차별점, 방문 팁(예약, 웨이팅, 결제, 언어)")
    final_review: Optional[str] = Field(None, description="유튜버가 마무리에서 한 평가 요약")
    embedding_text: str = Field(description="검색용 요약문. 컨셉, 분위기, 방문 팁을 담은 3~5문장의 자연스러운 한국어 문단. 메뉴 목록은 나열하지 않는다")
    is_course: bool = Field(description="이 가게에서 코스(테이스팅 메뉴, 코스 요리, 세트, 오마카세)로 식사했으면 true. 단품을 골라 주문했으면 false")
    course_name: Optional[str] = Field(None, description="코스 이름. 영상에서 부른 이름(디너 코스 등), 이름이 없으면 '코스'. is_course 가 false면 null")
    course_evidence: Optional[str] = Field(None, description="코스라고 판단한 근거 발언 원문. is_course 가 false면 null")
    menus: List[VideoMenu] = Field(default_factory=list)
    drinks: List[Drink] = Field(default_factory=list)
    chefs: List[VideoChef] = Field(default_factory=list, description="이 가게의 오너·총괄 셰프. 없거나 누구인지 모르면 빈 목록")


class VideoResult(BaseModel):
    stores: List[StoreVideoNotes] = Field(default_factory=list, description="[대상 가게]마다 항목 하나씩")


# -------------------------------------------------------------
# 2. 프롬프트 (extract_script_split 의 PROMPT_A/B 기준을 영상(음성+화면)에 맞춰 합쳤다)
# -------------------------------------------------------------
SYSTEM_PROMPT = """너는 미식 유튜브 영상에서 식당 정보를 구조화해 추출하는 분석가야. 영상의 음성뿐 아니라 화면(메뉴판 글씨, 음식 비주얼, 인테리어, 간판)도 근거로 삼는다.

[공통]
- 영상은 한 식당 또는 여러 식당을 소개한다. 사용자가 지정한 [대상 가게] 각각에 대해 stores 항목을 하나씩 만들고, 가게마다 해당 가게의 정보만 담아 다른 가게 정보와 섞지 않는다. 각 항목의 google_cid는 목록의 값을 그대로 쓴다.
  대상이 식당이면 그 식당의 음식, 서비스, 분위기만 다루고 같은 건물의 호텔 등 다른 시설 소개는 제외한다.
  대상 가게가 영상에 나오지 않으면 그 항목은 menus와 drinks를 빈 목록으로, is_course는 false, 나머지는 null로 둔다.
- 상호명(restaurant_name)은 [대상 가게]의 이름을 그대로 쓴다. 인사말의 채널명(예: 비밀이야)은 식당명이 아니다.
- 영상(음성·화면)에서 확인되는 내용만 쓴다. 가격, 도수, 조리법, 재료, 영업·예약 정보는 확인되지 않으면 null 또는 빈 목록으로 두고 추측하지 않는다.
- 잡담, 인사, 촬영 뒷이야기, 구독 유도는 제외한다.

[식당 정보]
- category_broad는 식당이 실제로 내는 음식의 나라·계통으로 정한다. 영상이 한국어여도 한식이 아니다.
  한식=한국 요리 / 일식=일본 요리(스시, 라멘, 이자카야 등) / 중식=중국·홍콩·대만 요리(광둥, 딤섬, 완탕면 등)
  양식=유럽·아메리카 요리 전체(프랑스, 이탈리아, 스페인, 스테이크, 비스트로 등) / 동남아식 / 인도식 / 중동식 / 기타
  유럽 도시의 식당이나 파스타·스테이크·와인이 중심이면 양식이다. 홍콩 식당이면서 광둥 요리면 중식이다. 나라로 나눌 수 없는 식당(해산물 전문점, 베이커리 등)은 기타.
- category_detail은 식당 소개와 나온 요리로 판단 가능하면 반드시 짧은 구 하나로 채운다(쉼표로 여러 개 쓰지 않는다. 다른 요리는 cuisine_tags에). 미슐랭 등급은 쓰지 않는다. 정말 알 수 없을 때만 null.
- cuisine_tags는 영상에 나온 요리·조리 키워드로 3~5개를 채운다.
- atmosphere는 영상에서 보이거나 언급된 공간, 좌석, 뷰, 인테리어, 손님층, 서비스, 주문·운영 방식을 1~2문장으로 요약한다. 확인되는 것이 전혀 없을 때만 null.
- key_points는 이 식당만의 차별점과 방문 팁이다. final_review는 유튜버가 마무리에서 한 평가를 요약한다.
- embedding_text는 메뉴 목록을 나열하지 말고 식당의 컨셉, 분위기, 방문 팁을 3~5문장으로 쓴다.

[메뉴·음료]
- 메뉴는 구체적인 요리명만 넣는다. 파스타, 생선 같은 범주 이름은 넣지 않는다. 와인, 술, 음료는 메뉴가 아니라 drinks에 넣는다.
- 영상에서 [대상 가게]의 요리가 나오는 구간을 처음부터 끝까지 보고, 먹은 요리를 빠짐없이 적는다.
  같은 요리를 한글 이름과 외국어 이름으로 두 번 적지 않는다. 메뉴판만 읽고 먹지 않은 것, 오늘은 없다고 한 것은 제외한다.
- 요리마다 설명 칸은 cooking_features 하나뿐이다. 그 요리에 대한 설명을 전부 모아 요약해서 이 칸에 담는다.
  - 담을 것: 재료, 조리법, 소스, 곁들임, 제공 방식, 부위, 유래·특징, 맛·식감·향·온도의 구체적인 묘사, 다른 음식에 빗댄 말("~ 같은 느낌"),
    먹는 방법·추천 팁, 유튜버나 직원이 말한 평가 중 이유나 구체적인 내용이 붙은 것(예: "소스가 진해서 밥이 계속 들어간다").
  - 모으는 법: 요리 소개(직원 설명, 메뉴 소개), 먹는 장면의 감상, 이어지는 추가 설명을 모두 찾아서 합친다.
    같은 접시에 대한 말이면 앞뒤로 떨어져 있어도 한 요리의 설명이다. 담을 내용이 있는데 빠뜨리지 않는다.
  - 빼는 것: "맛있다", "맛있어 보인다", "미쳤다", "완벽하다", "대박", "최고다" 같이 구체적인 내용 없이 평가·감탄만 있는 말. 이런 말뿐이면 null이다.
    한 문장에 감탄과 구체적인 내용이 같이 있으면 구체적인 내용만 남긴다.
  - 쓰는 법: 구어체·감탄사·말버릇·강조 부사("진짜", "엄청", "너무")를 빼고 정리된 평서문 1~4문장으로 쓴다. 발언을 그대로 옮기지 않는다. 원문은 evidence에만 둔다.
  - 가격, 가격 비교, 영업·예약 정보는 쓰지 않는다(메뉴판에서 읽은 가격도 쓰지 않는다).
- 요리마다 그 요리에 대한 말만 쓴다. 함께 나온 다른 요리의 설명이나 평가를 섞지 않는다.
- 설명이 전혀 없는 요리도 이름과 evidence가 있으면 메뉴에는 넣고 cooking_features는 null로 둔다.
- 밑반찬·곁들임 반찬(젓갈, 김치, 쌈 채소 등 기본으로 깔리는 것)은 메뉴에 넣지 않는다. 방문자가 알 만한 내용이면 key_points에 한 줄로 쓴다.
- 영상에 나오기만 하고 [대상 가게]의 음식을 실제로 먹는 장면이 없으면 menus는 빈 목록으로 둔다(지나가며 소개만 한 경우).
- 음료는 name과 review에 위와 같은 방식으로 요약한 설명(산지, 품종, 향, 숙성, 마시는 요령, 페어링한 요리, 구체적인 시음 묘사)을 적는다. 감탄뿐이면 null이다.
- order에는 요리가 나온 순서를 1부터 적는다(음료는 제외).
- evidence에는 근거 발언 원문만 적고 시각은 붙이지 않는다.

[화면 등장 시각]
- 요리마다 first_appearance에 그 음식이 화면에 실제로 처음 보이는 시각을 적는다. 조리 장면이나 서빙된 접시처럼 음식 자체가 화면에 보이는 첫 순간이며, 영상 재생 시간 기준 MM:SS 형식이다(1시간 이상은 H:MM:SS).
- 메뉴판 글씨, 자막, 사진 속 이미지, 유튜버가 메뉴명을 말하는 순간은 음식이 등장한 것이 아니다. 음식이 화면에 보이지 않으면 추측하지 말고 null로 둔다.
- 처음 등장한 시각 하나만 적고, 이후 다시 나오는 시각은 적지 않는다.

[셰프]
- chefs에는 [대상 가게]의 오너 셰프, 총괄·헤드 셰프, 대표 셰프만 넣는다.
- 유튜버와 함께 간 다른 셰프, 지인, 다른 식당의 셰프, 비교로 언급된 셰프, 과거 인물, 채널 운영자(유튜버)와 인사말에 나오는 채널명은 제외한다.
- 이름은 영상에서 말하거나 화면 자막·간판에 실제로 나온 것만 쓴다. 일반 상식으로 이름을 보충하거나 추측하지 않는다. 이름을 알 수 없으면 chefs를 빈 목록으로 둔다.
- 경력·수상·방송 출연은 영상에서 소개한 내용만 background에 쓴다. 없으면 null.

[코스]
- is_course는 이 가게에서 코스(테이스팅 메뉴, 코스 요리, 세트, 오마카세)로 식사했다는 근거가 영상에 분명할 때만 true다. 단품 주문, 코스가 아닌 이야기(다른 가게, 일반론)는 false. 근거가 불분명하면 false.
- is_course가 true이면 코스에 포함돼 나온 요리 전부에 course_stage를 적는다. false이면 모든 요리의 course_stage는 null이다.
- 단계는 아래 중 하나다. 코스는 보통 이 순서로 나온다.
  아뮤즈부쉬(식사 시작 전 한입 요리) → 식전빵 → 전채(애피타이저, 차가운 요리, 첫 접시) → 수프 → 생선(해산물 메인 이전의 생선·해산물 요리) →
  메인(고기·메인 요리) → 치즈 → 프리디저트(디저트 전 입가심) → 디저트 → 프티푸르(식후 한입 과자).
- 영상에서 단계를 직접 말했으면 그대로 따르고, 말하지 않았으면 나온 순서와 요리 성격으로 가장 가까운 단계를 적는다. 단계의 순서가 뒤바뀌지 않게 한다. 같은 단계에 접시가 여러 개면 반복해서 써도 된다.
- 코스와 별도로 추가 주문한 요리와 코스 밖에서 먹은 요리는 null이다.
"""


# -------------------------------------------------------------
# 3. 호출
# -------------------------------------------------------------
def call_with_retry(contents, config):
    """일시 오류(429/5xx)는 대기 시간을 두 배씩 늘려 재시도하고, 그 외 오류는 바로 올린다."""
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            return client.models.generate_content(model=MODEL, contents=contents, config=config)
        except genai_errors.APIError as e:
            if e.code not in RETRY_CODES or attempt == MAX_RETRIES:
                raise
            wait = RETRY_WAIT * 2 ** (attempt - 1)
            print(f"  재시도 {attempt}/{MAX_RETRIES - 1}: {e.code} {str(e)[:100]} ({wait}초 대기)")
            time.sleep(wait)


def extract_notes(vid: str, targets: List[dict], others: List[dict]):
    targets_text = "\n".join(f"- google_cid={t['google_cid'].strip()} / {describe(t)}" for t in targets)
    others_text = "\n".join(f"- {describe(o)}" for o in others) or "(없음)"
    contents = types.Content(parts=[
        types.Part(file_data=types.FileData(file_uri=f"https://www.youtube.com/watch?v={vid}")),
        types.Part(text=(
            f"[대상 가게]\n{targets_text}\n\n"
            f"[같은 영상의 다른 가게 (추출 대상 아님, 정보를 섞지 말 것)]\n{others_text}\n\n"
            "이 영상에서 [대상 가게] 각각의 정보를 추출해줘."
        )),
    ])
    config = types.GenerateContentConfig(
        system_instruction=SYSTEM_PROMPT, temperature=0.1, response_mime_type="application/json", response_schema=VideoResult,
    )
    return call_with_retry(contents, config)


# -------------------------------------------------------------
# 4. 검증·정리
# -------------------------------------------------------------
def fix_appearance(notes: dict, limit_sec: Optional[int]) -> int:
    """first_appearance 를 'MM:SS' / 'H:MM:SS' 로 맞추고, 형식이 틀리거나 영상 길이를 넘으면 null 로 버린다. 버린 수를 돌려준다."""
    dropped = 0
    for m in notes["menus"]:
        text = m.get("first_appearance")
        if not text:
            m["first_appearance"] = None
            continue
        match = re.fullmatch(r"\s*(?:(\d{1,2}):)?(\d{1,2}):(\d{2})\s*", text)
        sec = int(match[1] or 0) * 3600 + int(match[2]) * 60 + int(match[3]) if match else None
        if sec is None or int(match[3]) > 59 or (limit_sec is not None and sec > limit_sec):
            m["first_appearance"] = None
            dropped += 1
        else:
            m["first_appearance"] = f"{sec // 3600}:{sec % 3600 // 60:02d}:{sec % 60:02d}" if sec >= 3600 else f"{sec // 60:02d}:{sec % 60:02d}"
    return dropped


def video_seconds(vid: str) -> Optional[int]:
    """영상 길이(초). yt-dlp 로 한 번만 받아 캐시한다(API 비용 없음). 실패하면 None 이라 길이 검증은 건너뛴다."""
    cache = json.loads(DURATION_FILE.read_text(encoding="utf-8")) if DURATION_FILE.exists() else {}
    if vid not in cache:
        try:
            import yt_dlp
            with yt_dlp.YoutubeDL({"skip_download": True, "quiet": True, "no_warnings": True}) as ydl:
                cache[vid] = ydl.extract_info(f"https://www.youtube.com/watch?v={vid}", download=False).get("duration")
            DURATION_FILE.write_text(json.dumps(cache), encoding="utf-8")
        except Exception as e:
            print(f"  [경고] {vid}: 영상 길이를 못 받아 시각 길이 검증을 건너뜁니다: {str(e)[:80]}")
            return None
    return cache[vid]


def title_and_description(vid: str, store: dict) -> str:
    """셰프 이름이 제목·소개란에 있는지 보는 데 쓰는 텍스트(영상 제목 + 캐시된 소개란)."""
    path = DESC_DIR / f"{vid}.json"
    desc = json.loads(path.read_text(encoding="utf-8")).get("description", "") if path.exists() else ""
    return _chef_norm(store["video_title"] + " " + desc)


def assess_chefs(chefs: List[dict], vid: str, store: dict) -> List[dict]:
    """영상에서 뽑은 셰프를 걸러 확신도를 붙인다(load_chefs.py 가 읽는 키). 가짜 이름·직함은 버린다.
    high: 이름이 영상 제목·소개란에 있다 / medium: 영상 속 발언·화면에서만 확인(코드로 더 검증할 근거가 없다)."""
    ref = title_and_description(vid, store)
    kept = []
    for chef in chefs:
        name = chef["name"].strip()
        key = _chef_norm(name)
        if name.lower() in GARBAGE_NAMES or len(key) < 2 or len(key) > 12:
            continue
        if name.replace(" ", "") in GENERIC_NAMES or key in GENERIC_NAMES or name.endswith(GENERIC_SUFFIX):
            continue
        in_title = key in ref
        kept.append({**chef, "name": name, "confidence": "high" if in_title else "medium", "name_in_title": in_title})
    return kept


def result_path(tag: str, vid: str, cid: str) -> Path:
    return ROOT / "data" / "video_notes" / tag / f"{vid}__{cid}.json"


def excluded_path(tag: str, vid: str, cid: str) -> Path:
    return ROOT / "data" / "video_notes" / "_excluded" / tag / f"{vid}__{cid}.json"


def is_done(vid: str, store: dict) -> bool:
    cid = store["google_cid"].strip()
    return result_path(TAG, vid, cid).exists() or excluded_path(TAG, vid, cid).exists()


def load_video_stores(ids: Optional[List[str]]) -> dict:
    """CSV 를 영상별 가게 목록으로 묶는다(google_cid 있는 가게만, 영상 내 중복 cid 제거). ids 가 있으면 그 영상만."""
    by_video, seen = {}, set()
    with open(STORES_CSV, "r", encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            vid, cid = video_id_of(row["video_url"]), row["google_cid"].strip()
            if (ids and vid not in ids) or not row["korean_name"].strip() or not cid or (vid, cid) in seen:
                continue
            seen.add((vid, cid))
            by_video.setdefault(vid, []).append(row)
    return by_video


def process_video(vid: str, stores: List[dict]) -> str:
    targets = [s for s in stores if not is_done(vid, s)]
    if not targets:
        return "skip"
    others = [s for s in stores if s not in targets]
    started = time.time()
    try:
        response = extract_notes(vid, targets, others)
        result: Optional[VideoResult] = response.parsed
        if result is None:
            raise ValueError("구조화 응답 파싱 실패")
    except Exception as e:
        print(f"[fail] {vid} ({len(targets)}곳 전체): {str(e)[:200]}")
        return "fail"
    usage = response.usage_metadata
    video_usage = {"prompt_tokens": usage.prompt_token_count, "output_tokens": usage.candidates_token_count,
                   "seconds": round(time.time() - started, 1), "stores_in_call": len(targets)}
    by_cid = {}
    for notes in result.stores:
        by_cid.setdefault(notes.google_cid.strip(), notes)

    total = video_seconds(vid)
    limit_sec = total + END_MARGIN_SEC if total else None
    for store in targets:
        label = f"{vid} / {store['korean_name']}"
        cid = store["google_cid"].strip()
        found = by_cid.get(cid)
        if found is None:
            print(f"[fail] {label}: 응답에 해당 가게 항목 없음 (재실행하면 이 가게만 다시 호출)")
            continue
        notes = found.model_dump(exclude={"google_cid", "is_course", "course_name", "course_evidence"})
        course = ({"name": (found.course_name or "코스").strip(), "evidence": found.course_evidence, "evidence_sec": None}
                  if found.is_course else None)
        if not course:  # 코스가 아니면 단계를 모두 지운다
            for m in notes["menus"]:
                m["course_stage"] = None
        notes["dropped_non_menu"] = clean_menus(notes)
        dropped_time = fix_appearance(notes, limit_sec)
        chefs = assess_chefs(notes.pop("chefs"), vid, store)
        if not notes["menus"]:   # 메뉴가 하나도 없으면 DB 에 적재할 정보가 없다. 사유를 남기고 다시 호출하지 않는다
            path = excluded_path(TAG, vid, cid)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"video_id": vid, "google_cid": cid, "korean_name": store["korean_name"], "reason": "메뉴 0개",
                                        "concept": notes["concept"], "drinks": len(notes["drinks"])}, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"[제외] {label}: 메뉴 0개 (컨셉: {notes['concept']})")
            continue
        data = {
            "video_id": vid, "video_title": store["video_title"], "google_cid": cid, "korean_name": store["korean_name"],
            "address": store["address"], "google_official_name": store["google_official_name"],
            "model": TAG, "transcript_source": "video",
            "usage": {"gemini": video_usage},
            **notes,
            "course": course,
            "chefs": chefs,
        }
        path = result_path(TAG, vid, cid)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        timed = sum(1 for m in notes["menus"] if m["first_appearance"])
        print(f"[ok]   {label}: 메뉴 {len(notes['menus'])}개 (등장 시각 {timed}개, 검증 탈락 {dropped_time}개), 음료 {len(notes['drinks'])}개, "
              f"{notes['category_broad']}/{notes['category_detail']}, 제외 {notes['dropped_non_menu']}, "
              f"코스 {'O' if course else 'X'}(단계 {sum(1 for m in notes['menus'] if m['course_stage'])}개), 셰프 {[c['name'] for c in data['chefs']]}")
    print(f"       호출 1회: 가게 {len(targets)}곳, 입력 {usage.prompt_token_count:,}토큰, 출력 {usage.candidates_token_count:,}토큰, {video_usage['seconds']}초")
    return "ok"


def pending_videos(by_video: dict) -> List[str]:
    return [vid for vid, stores in sorted(by_video.items()) if any(not is_done(vid, s) for s in stores)]


def main(video_ids: List[str], ids_file: Optional[Path], limit: Optional[int], dry_run: bool) -> None:
    ids = set(video_ids)
    if ids_file:
        ids |= {l.strip() for l in ids_file.read_text(encoding="utf-8").splitlines() if l.strip()}
    by_video = load_video_stores(ids or None)
    missing = sorted(ids - set(by_video))
    if missing:
        print(f"CSV에 가게 정보가 없어 건너뜀: {missing}")
    todo = pending_videos(by_video)
    targets = todo[:limit] if limit else todo
    minutes = 0
    for vid in targets:
        minutes += (video_seconds(vid) or 0) / 60
    print(f"처리 안 된 영상 {len(todo)}개 중 이번에 {len(targets)}개 (가게 {sum(len(by_video[v]) for v in targets)}곳, 영상 길이 합계 약 {minutes:.0f}분, 저장 폴더 data/video_notes/{TAG})")
    if dry_run:
        return
    fails = 0
    for vid in targets:
        status = process_video(vid, by_video[vid])
        fails = fails + 1 if status == "fail" else 0 if status == "ok" else fails
        if fails >= MAX_CONSECUTIVE_FAILS:
            print(f"연속 {fails}번 실패해서 멈춥니다(쿼터 소진·서버 과부하 가능). 잠시 뒤 같은 명령을 다시 실행하면 이어서 처리합니다.")
            break
    print(f"남은 영상 {len(pending_videos(by_video))}개")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Gemini로 유튜브 영상을 직접 분석해 식당 정보를 추출한다(자동 생성 자막 영상용, 자막은 쓰지 않는다).")
    ap.add_argument("--video-id", action="append", default=[], help="이 영상만 처리 (여러 번 지정 가능, -로 시작하는 ID는 --video-id=ID)")
    ap.add_argument("--ids-file", type=Path, default=None, help="대상 영상 ID 목록 파일(한 줄에 하나)")
    ap.add_argument("--limit", type=int, default=None, help="이번 실행에서 처리할(아직 처리 안 된) 영상 수")
    ap.add_argument("--dry-run", action="store_true", help="API 호출 없이 대상 건수와 영상 길이만 출력")
    ap.add_argument("--tag", default=None, help=f"결과 저장 폴더와 model 값. 기본 {TAG}")
    args = ap.parse_args()
    if args.tag:
        TAG = args.tag
    if not args.video_id and not args.ids_file:
        ap.error("--video-id 또는 --ids-file 을 지정하세요(전체 CSV를 돌리지 않는다)")
    main(args.video_id, args.ids_file, args.limit, args.dry_run)
