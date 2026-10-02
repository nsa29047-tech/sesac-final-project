"""
YouTube 영상 URL을 Gemini API에 직접 넘겨, 시각·음성 정보까지 종합해 식당 비정형 정보를 추출한다.
extract_transcript_notes.py(자막 기반)와 같은 스키마(TranscriptNotes)를 쓴다.

- 가게 정보는 extract_restaurant_info.py 가 만든 CSV(영상 x 식당 1행)를 기준으로 한다.
  같은 CSV의 google_cid 를 결과에 그대로 붙이므로 DB 적재 시 별도 이름 매칭이 필요 없다.
- 한 영상에 가게가 여러 곳이면 영상을 한 번만 분석하고, 응답의 가게별 항목(google_cid로 식별)을 나눠 저장한다.
  결과는 data/video_notes/{model}/{video_id}__{google_cid}.json 에 저장하고, 이미 있는 가게는 호출 대상에서 뺀다.
- Google Places 매칭에 실패한 가게(google_cid 없음)는 DB에 적재할 수 없으므로 건너뛴다.
- 무료 등급은 YouTube 영상을 하루 8시간까지만 받을 수 있고 분당 토큰 한도도 있다.
  429/5xx(503 과부하 등)는 대기 시간을 두 배씩 늘려가며 재시도한다.

실행 예:
  uv run python src/pipeline/extract_video_notes.py --stores-csv data/verify4_restaurants_info.csv
  uv run python src/pipeline/extract_video_notes.py --stores-csv data/restaurants_info.csv --video-id 3vYwR8V8IaU
"""

import argparse
import csv
import json
import os
import time
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import parse_qs, urlparse

from dotenv import load_dotenv
from google import genai
from google.genai import errors as genai_errors
from google.genai import types
from pydantic import BaseModel, Field

from extract_transcript_notes import Menu, TranscriptNotes

load_dotenv()

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_STORES_CSV = ROOT / "data" / "restaurants_info.csv"
OUTPUT_DIR = ROOT / "data" / "video_notes"
DEFAULT_MODEL = "gemini-3.5-flash-lite"
MAX_RETRIES = 5
RETRY_WAIT = 30  # 첫 재시도 대기 초. 이후 두 배씩 늘린다(30, 60, 120, 240초)
RETRY_CODES = {429, 500, 503, 504}  # 일시적 오류만 재시도

client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))


class VideoMenu(Menu):
    """영상 분석용 메뉴. 자막 방식 스키마(Menu)에 영상 속 첫 등장 시각을 더한다."""

    first_appearance: Optional[str] = Field(
        None,
        description="이 음식이 화면에 실제로 처음 보이는 시각(조리 장면이나 서빙된 음식). 메뉴판·자막·메뉴명 언급은 해당하지 않는다. "
        "MM:SS 형식, 1시간이 넘으면 H:MM:SS. 음식이 화면에 보이지 않으면 null",
    )


class VideoNotes(TranscriptNotes):
    menus: List[VideoMenu] = Field(default_factory=list)


class StoreNotes(VideoNotes):
    google_cid: str = Field(description="[대상 가게] 목록에서 이 정보가 속한 가게의 google_cid. 목록에 적힌 값을 그대로 쓴다")


class VideoResult(BaseModel):
    stores: List[StoreNotes] = Field(default_factory=list, description="[대상 가게]마다 항목 하나씩")


SYSTEM_PROMPT = """너는 미식 유튜브 영상에서 식당 정보를 구조화해 추출하는 분석가야.
영상의 음성뿐 아니라 화면(메뉴판 글씨, 가격, 음식 비주얼, 인테리어, 간판)도 함께 근거로 삼아라.

규칙:
- 영상(음성·화면)에서 확인되는 내용만 쓴다. 확인되지 않는 항목은 null 또는 빈 목록으로 두고 추측하지 않는다.
- 가격, 도수, 조리법 같은 수치와 사실은 영상에서 확인된 것만 쓴다. 메뉴판에서 읽은 가격은 화면 근거로 인정한다.
- 잡담, 인사, 촬영 뒷이야기, 구독 유도 등 식당과 무관한 내용은 제외한다.
- 한 영상에 여러 가게가 나올 수 있다. 사용자가 지정한 [대상 가게] 각각에 대해 stores 항목을 하나씩 만들고, 가게마다 해당 가게의 정보만 담아 다른 가게 정보와 섞지 않는다. 각 항목의 google_cid는 목록의 값을 그대로 쓴다.
  대상이 식당이면 그 식당의 음식, 서비스, 분위기만 다루고, 같은 건물의 호텔 등 다른 시설 소개는 제외한다.
  대상 가게가 영상에 나오지 않으면 그 가게 항목은 menus를 빈 목록으로 두고 나머지는 null로 둔다.
- 상호명(restaurant_name)은 [대상 가게]의 이름을 그대로 쓴다. 인사말의 채널명(예: 비밀이야)은 식당명이 아니다.
- 메뉴는 구체적인 요리명만 넣는다. 파스타, 생선, 와인 같은 범주나 주류는 메뉴에서 제외한다(주류는 drinks에).
- is_signature는 유튜버가 대표·추천이라고 분명히 말한 메뉴에만 true로 한다.
- category_broad, category_detail, cuisine_tags는 식당이 실제로 내는 음식을 근거로 정한다. 확신이 없으면 category_detail은 null, cuisine_tags는 비운다.
  cuisine_tags에는 요리와 조리 키워드만 넣고 술, 와인, 재료 일반명은 넣지 않는다.
- 메뉴마다 근거를 evidence에 남긴다. 발언이면 원문 그대로, 화면 근거면 '(화면) ...' 형식으로 쓰고 가능하면 MM:SS 시각을 붙인다.
- 메뉴마다 first_appearance에 그 음식이 화면에 실제로 처음 등장하는 시각을 적는다. 조리 장면이나 서빙된 접시처럼 음식 자체가 화면에 보이는 첫 순간이며, 영상 재생 시간 기준 MM:SS 형식이다(1시간 이상은 H:MM:SS).
  메뉴판 글씨, 자막, 사진 속 이미지, 유튜버가 메뉴명을 말하는 순간은 음식이 등장한 것이 아니므로 기준으로 쓰지 않는다. 음식이 화면에 보이지 않으면 추측하지 말고 null로 둔다.
  처음 등장한 시각 하나만 적고, 이후 다시 나오는 시각은 적지 않는다.
- 유튜버의 미식 표현(바삭함, 진한 육수 등)은 최대한 원래 표현을 살려 적는다.
- atmosphere에는 화면에서 보이는 인테리어, 좌석, 손님층, 주문·운영 방식을 포함한다.
"""


def video_id_of(url: str) -> str:
    return parse_qs(urlparse(url).query)["v"][0]


def load_stores(csv_path: Path) -> Dict[str, List[dict]]:
    """CSV를 영상별 가게 목록으로 묶는다. 가게 정보 없는 행(korean_name 비어 있음)은 제외."""
    by_video: Dict[str, List[dict]] = defaultdict(list)
    with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            if row["korean_name"].strip():
                by_video[video_id_of(row["video_url"])].append(row)
    return by_video


def describe(store: dict) -> str:
    parts = [store["korean_name"], store["address"]]
    return " / ".join(p for p in parts if p)


def call_with_retry(model: str, contents, config):
    """일시 오류(429/5xx)는 대기 시간을 두 배씩 늘려 재시도하고, 그 외 오류는 바로 올린다."""
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            return client.models.generate_content(model=model, contents=contents, config=config)
        except genai_errors.APIError as e:
            if e.code not in RETRY_CODES or attempt == MAX_RETRIES:
                raise
            wait = RETRY_WAIT * 2 ** (attempt - 1)
            print(f"  재시도 {attempt}/{MAX_RETRIES - 1}: {e.code} {str(e)[:100]} ({wait}초 대기)")
            time.sleep(wait)


def extract_notes(url: str, targets: List[dict], others: List[dict], model: str):
    targets_text = "\n".join(f"- google_cid={t['google_cid'].strip()} / {describe(t)}" for t in targets)
    others_text = "\n".join(f"- {describe(o)}" for o in others) or "(없음)"
    contents = types.Content(
        parts=[
            types.Part(file_data=types.FileData(file_uri=url)),
            types.Part(
                text=(
                    f"[대상 가게]\n{targets_text}\n\n"
                    f"[같은 영상의 다른 가게 (추출 대상 아님, 정보를 섞지 말 것)]\n{others_text}\n\n"
                    "이 영상에서 [대상 가게] 각각의 정보를 추출해줘."
                )
            ),
        ]
    )
    config = types.GenerateContentConfig(
        system_instruction=SYSTEM_PROMPT,
        temperature=0.1,
        response_mime_type="application/json",
        response_schema=VideoResult,
    )
    return call_with_retry(model, contents, config)


def output_path(model: str, video_id: str, cid: str) -> Path:
    return OUTPUT_DIR / model / f"{video_id}__{cid}.json"


def process_video(video_id: str, stores: List[dict], model: str) -> None:
    """영상 1개를 한 번 분석해 아직 처리 안 된 가게들의 결과를 가게별 JSON으로 저장한다."""
    targets, seen = [], set()
    for store in stores:
        label = f"{video_id} / {store['korean_name']}"
        cid = store["google_cid"].strip()
        if not cid:
            print(f"[skip] {label} (Google 매칭 실패, google_cid 없음: 적재 불가)")
        elif cid in seen:
            print(f"[skip] {label} (같은 영상에 google_cid 중복)")
        elif output_path(model, video_id, cid).exists():
            print(f"[skip] {label} (이미 처리됨)")
        else:
            seen.add(cid)
            targets.append(store)
    if not targets:
        return

    others = [s for s in stores if s not in targets]
    url = f"https://www.youtube.com/watch?v={video_id}"
    try:
        started = time.time()
        response = extract_notes(url, targets, others, model)
        result: VideoResult = response.parsed
        if result is None:
            raise ValueError("구조화 응답 파싱 실패")
    except Exception as e:
        print(f"[fail] {video_id} ({len(targets)}곳 전체): {e}")
        return

    usage = response.usage_metadata
    usage_info = {
        "prompt_tokens": usage.prompt_token_count,
        "output_tokens": usage.candidates_token_count,
        "seconds": round(time.time() - started, 1),
        "stores_in_call": len(targets),  # 토큰/시간은 이 호출 전체 값이다(가게별 값 아님)
    }
    by_cid = {}
    for notes in result.stores:
        by_cid.setdefault(notes.google_cid.strip(), notes)  # 같은 cid가 또 나오면 첫 항목만

    for store in targets:
        label = f"{video_id} / {store['korean_name']}"
        cid = store["google_cid"].strip()
        notes = by_cid.get(cid)
        if notes is None:
            print(f"[fail] {label}: 응답에 해당 가게 항목 없음 (재실행하면 이 가게만 다시 호출)")
            continue
        data = {
            "video_id": video_id,
            "video_title": store["video_title"],
            "google_cid": cid,
            "korean_name": store["korean_name"],
            "address": store["address"],
            "google_official_name": store["google_official_name"],
            "model": model,
            "usage": usage_info,
            **notes.model_dump(exclude={"google_cid"}),
        }
        out_path = output_path(model, video_id, cid)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[ok]   {label} -> 메뉴 {len(notes.menus)}개, {notes.category_broad}/{notes.category_detail}")
    print(
        f"       호출 1회: 가게 {len(targets)}곳, 입력 {usage.prompt_token_count:,}토큰, "
        f"출력 {usage.candidates_token_count:,}토큰, {usage_info['seconds']}초"
    )


def main(stores_csv: Path, video_ids: List[str], model: str, limit: Optional[int]) -> None:
    by_video = load_stores(stores_csv)
    targets = video_ids or sorted(by_video)
    if limit:
        targets = targets[:limit]
    for vid in targets:
        stores = by_video.get(vid, [])
        if not stores:
            print(f"[skip] {vid} (CSV에 가게 정보 없음)")
            continue
        process_video(vid, stores, model)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Gemini로 유튜브 영상을 직접 분석해 식당 비정형 정보를 추출한다.")
    ap.add_argument("--stores-csv", type=Path, default=DEFAULT_STORES_CSV, help="가게 목록 CSV (extract_restaurant_info.py 결과)")
    ap.add_argument("--video-id", action="append", default=[], help="영상 ID (여러 번 지정 가능). 없으면 CSV의 전체 영상")
    ap.add_argument("--model", default=DEFAULT_MODEL, help=f"Gemini 모델명 (기본 {DEFAULT_MODEL})")
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args()
    main(args.stores_csv, args.video_id, args.model, args.limit)
