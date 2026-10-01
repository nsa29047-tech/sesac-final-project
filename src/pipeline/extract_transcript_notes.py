"""
data/transcripts/{video_id}.txt 자막에서 식당 관련 비정형 정보(메뉴, 분위기, 총평 등)를 구조화해 추출한다.

- 영상 1개 = 식당 1곳을 가정하고 자막 전체를 한 번에 LLM에 넣는다.
- 상호명은 자막(STT 오타가 많음)이 아니라 영상 소개란에서 추출한 가게 정보(extract_restaurant_info.py의
  get_youtube_description / extract_stores_from_description)를 기준으로 삼는다.
- 결과는 영상별로 data/transcript_notes/{video_id}.json 에 즉시 저장한다.
  이미 결과 파일이 있으면 건너뛰므로 중단 후 재실행하면 이어서 처리된다.
- 자막에 없는 내용은 채우지 않도록 프롬프트에 명시했고, 메뉴마다 근거 문장(evidence)을 남긴다.

실행 예:
  uv run python src/pipeline/extract_transcript_notes.py --limit 3        # 샘플 3개
  uv run python src/pipeline/extract_transcript_notes.py --video-id 3vYwR8V8IaU
  uv run python src/pipeline/extract_transcript_notes.py                   # 전체(이어서 처리)
"""

import argparse
import json
import os
from pathlib import Path
from typing import List, Literal, Optional

from dotenv import load_dotenv
from openai import OpenAI
from pydantic import BaseModel, Field

from extract_restaurant_info import extract_stores_from_description, get_youtube_description

load_dotenv()

ROOT = Path(__file__).resolve().parents[2]
TRANSCRIPT_DIR = ROOT / "data" / "transcripts"
OUTPUT_DIR = ROOT / "data" / "transcript_notes"
MODEL = "gpt-4o-mini"

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))


# -------------------------------------------------------------
# 1. 출력 스키마
# -------------------------------------------------------------
class Menu(BaseModel):
    name: str = Field(description="구체적인 요리명 (자막의 오타를 문맥으로 보정). 파스타, 생선 같은 범주는 제외")
    cooking_features: Optional[str] = Field(None, description="조리법, 식재료, 소스 등 자막에 언급된 디테일")
    taste_review: Optional[str] = Field(None, description="맛·식감에 대한 유튜버의 표현")
    tips: Optional[str] = Field(None, description="먹는 방법이나 추천 팁")
    price: Optional[str] = Field(None, description="자막에 언급된 가격. 없으면 null")
    is_signature: bool = Field(False, description="유튜버가 대표·추천 메뉴라고 명시적으로 말한 경우에만 true")
    evidence: str = Field(description="이 메뉴 정보의 근거가 된 자막 문장 1~2개(원문 그대로)")


class Drink(BaseModel):
    name: str
    review: Optional[str] = Field(None, description="페어링이나 시음 평")


class TranscriptNotes(BaseModel):
    restaurant_name: Optional[str] = Field(None, description="제공된 소개란 가게 후보 중 이 영상의 주인공 식당 이름. 후보가 없으면 null")
    location_hint: Optional[str] = Field(None, description="자막에 언급된 동네, 랜드마크 등")
    concept: Optional[str] = Field(None, description="식당 종류/컨셉 (예: 클래식 비스트로, 짬뽕 전문점)")
    category_broad: Literal["한식", "일식", "중식", "양식", "동남아식", "인도식", "중동식", "기타"] = Field(
        "기타", description="음식 대분류. 양식은 유럽·아메리카 요리 전체(프랑스, 이탈리아, 스테이크 등). 근거가 없으면 기타"
    )
    category_detail: Optional[str] = Field(
        None, description="세부 음식 카테고리를 짧은 구로 (예: 오마카세, 파인다이닝, 삼겹살, 라멘, 스테이크, 비스트로). 근거가 없으면 null"
    )
    cuisine_tags: List[str] = Field(
        default_factory=list,
        description="이 식당을 검색할 때 쓸 음식·조리 키워드 최대 5개 (예: 티본 스테이크, 스시, 양갈비, 솥밥). 영상에서 확인되는 요리·조리 키워드만(술, 와인 제외)",
    )
    atmosphere: Optional[str] = Field(None, description="인테리어, 분위기, 손님층, 주문·운영 방식 등")
    menus: List[Menu] = Field(default_factory=list)
    drinks: List[Drink] = Field(default_factory=list)
    key_points: List[str] = Field(default_factory=list, description="이 식당만의 차별점, 방문 팁(예약, 웨이팅, 언어 등)")
    final_review: Optional[str] = Field(None, description="유튜버의 총평 요약")
    embedding_text: str = Field(
        description="검색용 요약문. 식당 컨셉, 대표 메뉴와 맛 표현, 분위기, 방문 팁을 포함한 3~5문장의 자연스러운 한국어 문단"
    )


SYSTEM_PROMPT = """너는 미식 유튜브 영상의 자막(STT 스크립트)에서 식당 정보를 구조화해 추출하는 분석가야.

규칙:
- 자막에는 음성 인식 오타가 있을 수 있다. 문맥으로 자연스럽게 보정하되 없는 내용을 만들지 마라.
- 자막에 명시적으로 나온 내용만 쓴다. 언급되지 않은 항목은 null 또는 빈 목록으로 둔다. 추측, 일반 상식으로 채우기 금지.
- 가격, 도수, 조리법 같은 수치와 사실은 자막에 나온 것만 쓴다.
- 잡담, 인사, 촬영 뒷이야기, 구독 유도 등 식당과 무관한 내용은 제외한다.
- 자막은 한 식당을 소개하는 영상이다. 다른 식당이 잠깐 비교로 언급되어도 이 영상의 주인공 식당 정보만 추출한다.
- 상호명은 자막이 아니라 사용자가 제공한 '소개란 가게 정보'를 따른다. 자막 인사말의 채널명(예: 비밀이야)은 식당명이 아니다.
- 메뉴는 구체적인 요리명만 넣는다. 파스타, 생선, 와인 같은 범주나 주류는 메뉴에서 제외한다(주류는 drinks에).
- is_signature는 유튜버가 대표·추천이라고 분명히 말한 메뉴에만 true로 한다.
- category_broad, category_detail, cuisine_tags는 식당이 실제로 내는 음식을 근거로 정한다. 확신이 없으면 category_detail은 null, cuisine_tags는 비운다.
- 메뉴마다 근거가 된 자막 문장을 evidence에 원문 그대로 남긴다.
- 유튜버의 미식 표현(바삭함, 진한 육수 등)은 최대한 원래 표현을 살려 적는다.
"""


# -------------------------------------------------------------
# 2. 추출
# -------------------------------------------------------------
def format_stores(stores) -> str:
    if not stores:
        return "(소개란에서 확인된 가게 정보 없음)"
    return "\n".join(f"- {st.korean_name} / {st.address}" for st in stores)


def extract_notes(text: str, stores) -> TranscriptNotes:
    response = client.beta.chat.completions.parse(
        model=MODEL,
        temperature=0.1,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    f"[소개란 가게 정보]\n{format_stores(stores)}\n\n"
                    f"다음 자막에서 이 영상의 식당 정보를 추출해줘:\n\n{text}"
                ),
            },
        ],
        response_format=TranscriptNotes,
    )
    return response.choices[0].message.parsed


def process_one(path: Path) -> None:
    out_path = OUTPUT_DIR / f"{path.stem}.json"
    if out_path.exists():
        print(f"[skip] {path.stem} (이미 처리됨)")
        return

    text = path.read_text(encoding="utf-8").strip()
    if not text:
        print(f"[skip] {path.stem} (빈 자막)")
        return

    try:
        url = f"https://www.youtube.com/watch?v={path.stem}"
        info = get_youtube_description(url)
        stores = extract_stores_from_description(info["title"], info["description"]).stores
        notes = extract_notes(text, stores)
    except Exception as e:
        print(f"[fail] {path.stem}: {e}")
        return

    data = {
        "video_id": path.stem,
        "video_title": info["title"],
        "description_stores": [st.model_dump() for st in stores],
        **notes.model_dump(),
    }
    out_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[ok]   {path.stem} -> {notes.restaurant_name} (메뉴 {len(notes.menus)}개, 자막 {len(text):,}자)")


def main(limit: Optional[int], video_id: Optional[str]) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    if video_id:
        files = [TRANSCRIPT_DIR / f"{video_id}.txt"]
    else:
        files = sorted(TRANSCRIPT_DIR.glob("*.txt"))
        if limit:
            files = files[:limit]

    for path in files:
        process_one(path)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="자막에서 식당 비정형 정보를 구조화해 추출한다.")
    ap.add_argument("--limit", type=int, default=None, help="앞에서부터 N개만 처리")
    ap.add_argument("--video-id", default=None, help="특정 영상 하나만 처리")
    args = ap.parse_args()
    main(args.limit, args.video_id)
