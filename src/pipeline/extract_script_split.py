"""
시각 자막에서 가게 정보(A)와 메뉴·음료 정보(B)를 두 번 나눠 추출한다. (A/B 분리 실험용)

- 기존 extract_script_notes.py 는 가게 1곳당 1회 호출로 모든 필드를 한 번에 뽑는다. 여기서는 같은 입력(자막 전문)으로
  두 번 호출한다. 가게당 호출이 2회라 입력 토큰이 약 2배다. 출력은 합계가 비슷하다.
  - A(가게 단위): 컨셉, 대분류·소분류, 태그, 분위기, 키포인트, 총평, 검색용 요약. 길이와 상관없이 몇 줄이라 전체 맥락이 필요하다.
  - B(메뉴 단위): 메뉴(특징·후기·팁·가격·근거·시각·코스 단계)와 음료. 요리 수만큼 늘어나서 누락과 부정확이 몰리는 부분이다.
- A와 B는 서로 입력에 의존하지 않아 기본으로 동시에 호출한다(--sequential 로 순차 호출). 자막을 user 메시지 맨 앞에 두어
  OpenAI 자동 프롬프트 캐시(앞부분이 같은 호출)가 적용될 수 있게 했다. 동시 호출이면 캐시가 안 걸릴 수 있다.
- 셰프(C)와 코스 여부(D)는 기존 검증된 함수를 그대로 쓴다. 한 호출에 필드를 더하면 추출이 흔들렸기 때문에 별도 호출이다.
  - C: extract_chef_info.chefs_for_store. 결과 중 대표 셰프(owner_chef, head_chef)만 남긴다. background 에 경력·수상·철학 같은 관련 언급을 담는다.
  - D: extract_script_course.detect_course. '코스' 단어가 있는 자막 줄을 코드가 골라 보여 주고 코스 여부와 근거 줄을 받는다.
- 코스 카테고리: B 가 요리마다 course_stage(아뮤즈부쉬, 식전빵, 전채, 수프, 생선, 메인, 치즈, 프리디저트, 디저트, 프티푸르)를 적는다.
  분류할 수 없거나 코스가 아니면 null 이다. D 가 코스가 아니라고 판단한 가게는 course_stage 를 모두 null 로 지운다.
- 메뉴 시각(mentioned_at)은 extract_script_notes.py 의 fix_times 로 실제 자막 시각에 맞추고 맞지 않으면 null 로 버린다.
- 결과는 data/video_notes/{TAG}/{video_id}__{google_cid}.json 에 건별 저장한다. 키 구조는 기존 결과와 같아서
  src/db/load_notes.py --model {TAG} 로 적재할 수 있다. 이미 있는 가게는 건너뛰므로 중단 후 재실행하면 이어서 처리된다.
  usage 에는 A, B 호출별 토큰·시간과 합계가 들어 있어 기존 방식과 비용·시간을 비교할 수 있다.
- 비교: --compare-tag 로 기존 결과 폴더(기본 gpt-4o-mini-script-v2)와 메뉴 수, 빈 필드 비율, 토큰, 비용을 나란히 출력한다.

실행 예:
  uv run python src/pipeline/extract_script_split.py --dry-run
  uv run python src/pipeline/extract_script_split.py --video-id 3vYwR8V8IaU          # 영상 지정
  uv run python src/pipeline/extract_script_split.py --limit 4                       # 처리 안 된 가게 4곳만
  uv run python src/pipeline/extract_script_split.py --compare-only --video-id 3vYwR8V8IaU
"""

import argparse
import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import List, Literal, Optional

from pydantic import BaseModel, Field

import extract_script_notes as base
from extract_script_notes import (
    MODEL,
    PRICE_PER_M,
    ROOT,
    TIME_RULES,
    TRANSCRIPT_DIR,
    describe,
    fix_times,
    load_stores,
)
from chunk_transcripts import video_parts
from extract_chef_info import chefs_for_store
from extract_script_course import detect_course
from compare_prompt_v3 import key_of
from extract_script_v7 import strip_generic
from extract_transcript_notes import Drink
from get_transcripts_since import format_timestamp

TAG = "gpt-4o-mini-script-split-v2"
BASELINE_TAG = "gpt-4o-mini-script-v2"
MAX_CONSECUTIVE_FAILS = 3


# -------------------------------------------------------------
# 1. 출력 스키마 (A + B 를 합치면 기존 ScriptNotes 와 같은 키가 된다)
# -------------------------------------------------------------
class StoreInfo(BaseModel):
    restaurant_name: Optional[str] = Field(None, description="[대상 가게]에 적힌 이름")
    location_hint: Optional[str] = Field(None, description="자막에 언급된 동네, 랜드마크 등")
    concept: Optional[str] = Field(None, description="식당 종류/컨셉 (예: 클래식 비스트로, 짬뽕 전문점)")
    category_broad: Literal["한식", "일식", "중식", "양식", "동남아식", "인도식", "중동식", "기타"] = Field(
        "기타", description="음식 대분류. 양식은 유럽·아메리카 요리 전체. 근거가 없으면 기타"
    )
    category_detail: Optional[str] = Field(None, description="세부 음식 카테고리를 짧은 구로 (예: 프랑스 가정식, 완탕면, 파인다이닝)")
    cuisine_tags: List[str] = Field(default_factory=list, description="음식·조리 키워드 3~5개. 술, 와인, 재료 일반명 제외")
    atmosphere: Optional[str] = Field(None, description="공간, 좌석, 뷰, 손님층, 서비스, 주문·운영 방식 1~2문장")
    key_points: List[str] = Field(default_factory=list, description="이 식당만의 차별점, 방문 팁(예약, 웨이팅, 결제, 언어)")
    final_review: Optional[str] = Field(None, description="유튜버가 마무리에서 한 평가 요약")
    embedding_text: str = Field(description="검색용 요약문. 컨셉, 분위기, 방문 팁을 담은 3~5문장의 자연스러운 한국어 문단")


CourseStage = Literal["아뮤즈부쉬", "식전빵", "전채", "수프", "생선", "메인", "치즈", "프리디저트", "디저트", "프티푸르"]


class SplitMenu(BaseModel):
    name: str = Field(description="구체적인 요리명 (자막의 오타를 문맥으로 보정). 범주 이름(디저트, 메인, 고기)은 제외")
    cooking_features: Optional[str] = Field(
        None,
        description="이 요리에 대한 설명 전부를 요약한 정리된 평서문 1~4문장: 재료, 조리법, 소스, 제공 방식, 맛·식감·향 묘사, 다른 음식에 빗댄 말, "
        "유튜버·직원이 말한 평가 중 구체적인 이유가 있는 것. 감탄만 있는 말은 제외. 설명이 자막에 없으면 null",
    )
    evidence: str = Field(description="이 메뉴 정보의 근거가 된 자막 문장 1~2개(원문 그대로, 시각은 붙이지 않는다)")
    mentioned_at: Optional[str] = Field(
        None,
        description="이 음식이 실제로 나오거나 먹는 장면으로 판단되는 자막의 시각. 자막 줄 앞의 [HH:MM:SS]를 그대로 복사한다. 근거가 없으면 null",
    )
    time_basis: Literal["served_cue", "mention_only", "none"] = Field(
        "none",
        description="served_cue: '나왔어요', '먹어볼게요' 같은 서빙·시식 단서가 있는 자막의 시각 / mention_only: 단서 없이 처음 언급된 시각 / none: 시각 없음",
    )
    order: int = Field(0, description="이 가게에서 요리가 나온 순서(1부터)")
    course_stage: Optional[CourseStage] = Field(
        None,
        description="코스 요리일 때 이 요리의 코스 단계. 자막에서 단계를 말했거나 요리 성격으로 분명히 판단될 때만 적고, 단품이거나 판단이 안 되면 null",
    )


class MenuInfo(BaseModel):
    menus: List[SplitMenu] = Field(default_factory=list)
    drinks: List[Drink] = Field(default_factory=list)


# -------------------------------------------------------------
# 2. 프롬프트
# -------------------------------------------------------------
COMMON_RULES = """
[공통]
- 자막은 한 식당 또는 여러 식당을 소개한다. 사용자가 지정한 [대상 가게]의 정보만 추출하고, 다른 가게 정보와 섞지 않는다.
- 상호명은 자막이 아니라 [대상 가게]를 따른다. 인사말의 채널명(예: 비밀이야)은 식당명이 아니다.
- 자막에는 음성 인식 오타가 있다. 상호와 메뉴명은 [대상 가게] 정보와 문맥으로 보정하되 없는 내용을 만들지 않는다.
- 자막에 나온 사실만 쓴다. 가격, 도수, 조리법, 재료, 영업·예약 정보는 없으면 null 또는 빈 목록으로 두고 추측하지 않는다.
- 잡담, 인사, 촬영 뒷이야기, 구독 유도는 제외한다.
"""

PROMPT_A = (
    "너는 미식 유튜브 영상의 자막(STT 스크립트)에서 식당 단위 정보를 구조화해 추출하는 분석가야. "
    "이 호출에서는 메뉴와 음료는 뽑지 않는다(다른 호출이 맡는다). 식당의 컨셉, 분류, 분위기, 방문 정보만 뽑는다.\n"
    + COMMON_RULES
    + """
[식당 정보]
- category_broad는 식당이 실제로 내는 음식의 나라·계통으로 정한다. 자막이 한국어여도 한식이 아니다.
  한식=한국 요리 / 일식=일본 요리(스시, 라멘, 이자카야 등) / 중식=중국·홍콩·대만 요리(광둥, 딤섬, 완탕면 등)
  양식=유럽·아메리카 요리 전체(프랑스, 이탈리아, 스페인, 스테이크, 비스트로 등) / 동남아식 / 인도식 / 중동식 / 기타
  파리·밀라노 등 유럽 도시의 식당이나 파스타·스테이크·와인이 중심이면 양식이다. 홍콩 식당이면서 광둥 요리면 중식이다.
- category_detail은 식당 소개와 나온 요리로 판단 가능하면 반드시 짧은 구로 채운다. 정말 알 수 없을 때만 null.
- cuisine_tags는 자막에 나온 요리·조리 키워드로 3~5개를 채운다.
- atmosphere는 자막에서 언급된 공간, 좌석, 뷰, 손님층, 서비스, 주문·운영 방식을 1~2문장으로 요약한다. 언급이 전혀 없을 때만 null.
- key_points는 이 식당만의 차별점과 방문 팁이다. final_review는 유튜버가 마무리에서 한 평가를 요약한다.
- embedding_text는 메뉴 목록을 나열하지 말고 식당의 컨셉, 분위기, 방문 팁을 3~5문장으로 쓴다.
"""
)

PROMPT_B = (
    "너는 미식 유튜브 영상의 자막(STT 스크립트)에서 [대상 가게]가 낸 메뉴와 음료를 빠짐없이 구조화해 추출하는 분석가야. "
    "이 호출에서는 식당의 분위기나 분류는 뽑지 않는다(다른 호출이 맡는다). 메뉴와 음료만 뽑는다.\n"
    + COMMON_RULES
    + """
[메뉴·음료]
- 메뉴는 구체적인 요리명만 넣는다. 파스타, 생선 같은 범주 이름은 넣지 않는다. 와인, 술, 음료는 메뉴가 아니라 drinks에 넣는다.
- 자막에서 [대상 가게]의 요리가 언급되거나 나오는 구간을 처음부터 끝까지 훑어, 먹은 요리를 빠짐없이 적는다.
  같은 요리를 한글 이름과 외국어 이름으로 두 번 적지 않는다. 메뉴판만 읽고 먹지 않은 것, 오늘은 없다고 한 것은 제외한다.
- 요리마다 칸은 cooking_features 하나뿐이다. 그 요리에 대한 설명을 전부 모아 요약해서 이 칸에 담는다.
  - 담을 것: 재료, 조리법, 소스, 곁들임, 제공 방식, 부위, 유래·특징, 맛·식감·향·온도의 구체적인 묘사, 다른 음식에 빗댄 말("~ 같은 느낌"),
    먹는 방법·추천 팁, 유튜버나 직원이 말한 평가 중 이유나 구체적인 내용이 붙은 것(예: "소스가 진해서 밥이 계속 들어간다").
  - 모으는 법: 요리 이름이 처음 나오는 소개 자막(직원 설명, 메뉴 소개), 먹는 장면의 감상, 이어지는 추가 설명을 모두 찾아서 합친다.
    같은 접시에 대한 말이면 앞뒤로 떨어져 있어도 한 요리의 설명이다. 담을 내용이 있는데 빠뜨리지 않는다.
  - 빼는 것: "맛있다", "맛있어 보인다", "미쳤다", "완벽하다", "대박", "최고다" 같이 구체적인 내용 없이 평가·감탄만 있는 말. 이런 말뿐이면 cooking_features는 null이다.
    한 문장에 감탄과 구체적인 내용이 같이 있으면 구체적인 내용만 남긴다.
  - 쓰는 법: 구어체·감탄사·말버릇·강조 부사("진짜", "엄청", "너무")를 빼고 정리된 평서문 1~4문장으로 쓴다. 자막 문장을 그대로 옮기지 않는다. 원문은 evidence에만 둔다.
  - 가격, 가격 비교, 영업·예약 정보는 쓰지 않는다.
- 요리마다 그 요리에 대한 말만 쓴다. 함께 나온 다른 요리의 설명이나 평가를 섞지 않는다.
- 설명이 전혀 없는 요리도 이름과 evidence가 있으면 메뉴에는 넣고 cooking_features는 null로 둔다.
- 음료는 name과 review에 위와 같은 방식으로 요약한 설명(산지, 품종, 향, 숙성, 마시는 요령, 페어링한 요리, 구체적인 시음 묘사)을 적는다. 감탄뿐이면 null이다.
- order에는 요리가 나온 순서를 1부터 적는다.

[코스 단계]
- 사용자 메시지의 [코스 여부]가 "코스"이면 코스에 포함돼 나온 요리 전부에 course_stage를 적는다. "코스 아님"이면 모든 요리의 course_stage는 null이다.
- 단계는 아래 중 하나다. 코스는 보통 이 순서로 나온다.
  아뮤즈부쉬(식사 시작 전 한입 요리) → 식전빵 → 전채(애피타이저, 차가운 요리, 첫 접시) → 수프 → 생선(해산물 메인 이전의 생선·해산물 요리) →
  메인(고기·메인 요리) → 치즈 → 프리디저트(디저트 전 입가심) → 디저트 → 프티푸르(식후 한입 과자).
- 자막에서 단계를 직접 말했으면("아뮤즈부쉬입니다", "디저트로 넘어갑니다") 그대로 따르고, 말하지 않았으면 나온 순서와 요리 성격으로 가장 가까운 단계를 적는다.
  단계의 순서가 뒤바뀌지 않게 한다(메인 뒤에 전채가 나오지 않는다). 같은 단계에 접시가 여러 개면 같은 단계를 반복해서 써도 된다.
- 코스와 별도로 추가 주문한 요리와 코스 밖에서 먹은 요리는 null이다.
"""
    + TIME_RULES
)


# -------------------------------------------------------------
# 3. 호출
# -------------------------------------------------------------
def call(system: str, store: dict, others: List[dict], transcript: str, schema, extra: str = ""):
    """자막을 user 메시지 맨 앞에 두고(프롬프트 캐시), 가게 지정은 뒤에 붙인다. (결과, usage dict) 반환."""
    others_text = "\n".join(f"- {describe(o)}" for o in others) or "(없음)"
    started = time.time()
    response = base.client.beta.chat.completions.parse(
        model=MODEL,
        temperature=0,
        seed=42,
        messages=[
            {"role": "system", "content": system},
            {
                "role": "user",
                "content": (
                    f"[시각 자막]\n{transcript}\n\n"
                    f"[대상 가게]\n- {describe(store)}\n\n"
                    f"{extra}"
                    f"[같은 영상의 다른 가게 (추출 대상 아님, 정보를 섞지 말 것)]\n{others_text}\n\n"
                    "위 시각 자막에서 [대상 가게]의 정보만 추출해줘."
                ),
            },
        ],
        response_format=schema,
    )
    details = getattr(response.usage, "prompt_tokens_details", None)
    usage = {
        "prompt_tokens": response.usage.prompt_tokens,
        "cached_tokens": getattr(details, "cached_tokens", 0) or 0,
        "output_tokens": response.usage.completion_tokens,
        "seconds": round(time.time() - started, 1),
    }
    return response.choices[0].message.parsed.model_dump(), usage


LEAD_ROLES = ("owner_chef", "head_chef")
CATEGORY_NAMES = {"고기", "생선", "채소", "야채", "해산물", "반찬", "음식", "요리", "밥", "국", "면", "술", "안주", "메뉴", "음료",
                  "디저트", "메인", "메인요리", "전채", "애피타이저", "수프", "스프", "코스", "사이드", "후식", "식전빵"}


def clean_menus(notes: dict) -> List[str]:
    """모델이 남긴 평가어 문장을 규칙으로 한 번 더 걷어내고, 설명 없는 범주 이름("디저트")을 메뉴에서 뺀다. 뺀 이름을 돌려준다."""
    for m in notes["menus"]:
        m["cooking_features"] = strip_generic(m.get("cooking_features"))
    for d in notes["drinks"]:
        d["review"] = strip_generic(d.get("review"))
    dropped = [m["name"] for m in notes["menus"] if key_of(m["name"]) in CATEGORY_NAMES and not m["cooking_features"]]
    notes["menus"] = [m for m in notes["menus"] if m["name"] not in dropped]
    for rank, m in enumerate(sorted(notes["menus"], key=lambda m: m["order"]), 1):
        m["order"] = rank
    return dropped
BY_VIDEO: dict = {}  # main() 이 채우는 영상별 가게 목록(video_parts 가 구간을 나눌 때 쓴다)


def cost_of(usage: dict) -> float:
    """캐시된 입력은 50% 할인으로 계산한다."""
    uncached = usage["prompt_tokens"] - usage.get("cached_tokens", 0)
    return (uncached * PRICE_PER_M[0] + usage.get("cached_tokens", 0) * PRICE_PER_M[0] * 0.5
            + usage["output_tokens"] * PRICE_PER_M[1]) / 1e6


def result_path(tag: str, vid: str, cid: str) -> Path:
    return ROOT / "data" / "video_notes" / tag / f"{vid}__{cid}.json"


def process(vid: str, store: dict, others: List[dict], sequential: bool) -> str:
    cid = store["google_cid"].strip()
    started = time.time()
    transcript_data = json.loads((TRANSCRIPT_DIR / f"{vid}.json").read_text(encoding="utf-8"))
    all_lines = [seg for seg in transcript_data["segments"] if seg["text"].strip()]
    source = transcript_data.get("source")
    try:
        # 모든 호출이 식당별 자막 구간만 본다(영상에 가게가 여러 곳이면 구간을 나눈다. 다른 가게 구간의 요리가 섞이는 것을 막는다)
        lines = next((part for st, part in video_parts(vid, BY_VIDEO[vid], all_lines) if st is store), all_lines)
        starts = [int(seg["start"]) for seg in lines]
        transcript = "\n".join(f"[{format_timestamp(l['start'])}] {l['text']}" for l in lines)
        usage_c = {"prompt_tokens": 0, "output_tokens": 0, "calls": 0, "seconds": 0}
        usage_d = {"prompt_tokens": 0, "output_tokens": 0, "calls": 0, "seconds": 0}

        # 코스 여부를 먼저 판단해 B 에 알려 준다(코스면 요리 전부의 단계를 분류하도록)
        t = time.time()
        course = detect_course(lines, store, usage_d)
        usage_d["seconds"] = round(time.time() - t, 1)
        course_note = (f"[코스 여부]\n코스 (코스 이름: {course['name']})\n\n" if course else "[코스 여부]\n코스 아님\n\n")

        def run_chefs():
            t = time.time()
            chefs = chefs_for_store(store, others, lines, source, usage_c)
            usage_c["seconds"] = round(time.time() - t, 1)
            return chefs

        if sequential:
            (info, usage_a) = call(PROMPT_A, store, others, transcript, StoreInfo)
            (menu_info, usage_b) = call(PROMPT_B, store, others, transcript, MenuInfo, course_note)
            chefs = run_chefs()
        else:
            with ThreadPoolExecutor(max_workers=3) as pool:
                fa = pool.submit(call, PROMPT_A, store, others, transcript, StoreInfo)
                fb = pool.submit(call, PROMPT_B, store, others, transcript, MenuInfo, course_note)
                fc = pool.submit(run_chefs)
                (info, usage_a), (menu_info, usage_b) = fa.result(), fb.result()
                chefs = fc.result()
    except Exception as e:
        print(f"[fail] {vid} / {store['korean_name']}: {str(e)[:120]}")
        return "fail"
    notes = {**info, **menu_info}
    notes["course"] = course
    if not course:  # 코스가 아니라고 판단된 가게는 단계를 모두 지운다
        for m in notes["menus"]:
            m["course_stage"] = None
    notes["dropped_non_menu"] = clean_menus(notes)
    notes["chefs"] = [c for c in chefs if c["role"] in LEAD_ROLES]  # 대표 셰프(오너·총괄)만 남긴다
    notes["chefs_dropped"] = [{"name": c["name"], "role": c["role"]} for c in chefs if c["role"] not in LEAD_ROLES]
    dropped = fix_times(notes, starts)
    usage = {
        "a": usage_a,
        "b": usage_b,
        "chef": usage_c,
        "course": usage_d,
        "prompt_tokens": sum(u["prompt_tokens"] for u in (usage_a, usage_b, usage_c, usage_d)),
        "output_tokens": sum(u["output_tokens"] for u in (usage_a, usage_b, usage_c, usage_d)),
        "cost_usd": round(sum(cost_of(u) for u in (usage_a, usage_b, usage_c, usage_d)), 5),
        "seconds": round(time.time() - started, 1),  # 가게 1곳을 끝내는 데 걸린 실제 시간(동시 호출이면 느린 쪽)
        "stores_in_call": 1,
        "mode": "sequential" if sequential else "parallel",
    }
    data = {
        "video_id": vid,
        "video_title": store["video_title"],
        "google_cid": cid,
        "korean_name": store["korean_name"],
        "address": store["address"],
        "google_official_name": store["google_official_name"],
        "model": TAG,
        "transcript_source": transcript_data.get("source"),
        "usage": usage,
        **notes,
    }
    path = result_path(TAG, vid, cid)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    timed = sum(1 for m in notes["menus"] if m["mentioned_at"])
    print(f"[ok]   {vid} / {store['korean_name']}: 메뉴 {len(notes['menus'])}개 (시각 {timed}개, 검증 탈락 {dropped}개), "
          f"음료 {len(notes['drinks'])}개, {notes['category_broad']}/{notes['category_detail']}, "
          f"제외 {notes['dropped_non_menu']}, 코스 {'O' if course else 'X'}(단계 {sum(1 for m in notes['menus'] if m['course_stage'])}개), 셰프 {[c['name'] for c in notes['chefs']]}, "
          f"{usage['seconds']}초, ${usage['cost_usd']:.4f}")
    return "ok"


# -------------------------------------------------------------
# 4. 비교
# -------------------------------------------------------------
def text_of(m: dict) -> str:
    """기존 방식(특징·후기·팁 칸 분리)과 분리 방식(특징 칸 하나)을 같은 기준으로 비교하려고 설명 칸을 모두 이어 붙인다."""
    return " ".join(x for x in (m.get("cooking_features"), m.get("taste_review"), m.get("tips")) if x)


def summarize(tag: str, keys: List[tuple]) -> dict:
    """tag 폴더에서 keys((vid, cid)) 에 해당하는 결과의 평균 지표를 낸다."""
    rows = []
    for vid, cid in keys:
        path = result_path(tag, vid, cid)
        if path.exists():
            rows.append(json.loads(path.read_text(encoding="utf-8")))
    if not rows:
        return {}
    menus = [m for r in rows for m in r["menus"]]
    n = len(rows)

    def filled(field):
        return sum(1 for m in menus if m.get(field)) / len(menus) if menus else 0

    usage_cost = []
    for r in rows:
        u = r.get("usage") or {}
        usage_cost.append(u["cost_usd"] if "cost_usd" in u else
                          (u.get("prompt_tokens", 0) * PRICE_PER_M[0] + u.get("output_tokens", 0) * PRICE_PER_M[1]) / 1e6)
    return {
        "가게 수": n,
        "메뉴 수(평균)": round(len(menus) / n, 1),
        "음료 수(평균)": round(sum(len(r["drinks"]) for r in rows) / n, 1),
        "설명 채움": f"{sum(1 for m in menus if text_of(m)) / len(menus):.0%}" if menus else "0%",
        "설명 길이(평균 글자)": round(sum(len(text_of(m)) for m in menus) / len(menus)) if menus else 0,
        "시각 채움": f"{filled('mentioned_at'):.0%}",
        "분위기 채움": f"{sum(1 for r in rows if r.get('atmosphere')) / n:.0%}",
        "코스 가게": sum(1 for r in rows if r.get("course")),
        "코스단계 채움": f"{filled('course_stage'):.0%}",
        "대표셰프 수": sum(len(r.get("chefs") or []) for r in rows),
        "소분류 채움": f"{sum(1 for r in rows if r.get('category_detail')) / n:.0%}",
        "키포인트(평균)": round(sum(len(r['key_points']) for r in rows) / n, 1),
        "입력 토큰(평균)": round(sum((r.get('usage') or {}).get('prompt_tokens', 0) for r in rows) / n),
        "출력 토큰(평균)": round(sum((r.get('usage') or {}).get('output_tokens', 0) for r in rows) / n),
        "가게당 비용($)": round(sum(usage_cost) / n, 4),
        "가게당 시간(초)": round(sum((r.get('usage') or {}).get('seconds', 0) for r in rows) / n, 1),
    }


def print_comparison(keys: List[tuple], compare_tag: str) -> None:
    base_stats, new_stats = summarize(compare_tag, keys), summarize(TAG, keys)
    if not new_stats:
        print("비교할 분리 방식 결과가 없습니다.")
        return
    # 같은 가게끼리 비교하도록 두 폴더에 모두 있는 가게만 쓴다.
    both = [k for k in keys if result_path(compare_tag, *k).exists() and result_path(TAG, *k).exists()]
    base_stats, new_stats = summarize(compare_tag, both), summarize(TAG, both)
    if not base_stats:
        print(f"기존 결과({compare_tag})와 겹치는 가게가 없습니다.")
        return
    print(f"\n=== 비교 (두 방식 모두 있는 가게 {len(both)}곳) ===")
    print(f"{'지표':<16}{'기존 ' + compare_tag:<34}{'분리 ' + TAG}")
    for k in new_stats:
        print(f"{k:<16}{str(base_stats.get(k)):<34}{new_stats[k]}")
    print("\n메뉴 이름 차이(가게별): 기존에만 / 분리에만")
    for vid, cid in both:
        old = {m["name"] for m in json.loads(result_path(compare_tag, vid, cid).read_text(encoding="utf-8"))["menus"]}
        new_data = json.loads(result_path(TAG, vid, cid).read_text(encoding="utf-8"))
        new = {m["name"] for m in new_data["menus"]}
        print(f"- {vid} / {new_data['korean_name']}: 기존에만 {sorted(old - new)} / 분리에만 {sorted(new - old)}")


# -------------------------------------------------------------
# 5. 실행
# -------------------------------------------------------------
def pending_items(by_video, video_ids):
    items, all_keys = [], []
    for vid, stores in sorted(by_video.items()):
        if video_ids and vid not in video_ids:
            continue
        if not (TRANSCRIPT_DIR / f"{vid}.json").exists():
            print(f"[skip] {vid}: 시각 자막(.json) 없음")
            continue
        for store in stores:
            cid = store["google_cid"].strip()
            all_keys.append((vid, cid))
            if not result_path(TAG, vid, cid).exists():
                items.append((vid, store, [s for s in stores if s is not store]))
    return items, all_keys


def main(video_ids, limit, dry_run, sequential, compare_tag, compare_only) -> None:
    by_video = load_stores()
    BY_VIDEO.update(by_video)
    items, all_keys = pending_items(by_video, video_ids)
    if compare_only:
        print_comparison(all_keys, compare_tag)
        return
    print(f"처리 안 된 가게 {len(items)}곳 (저장 폴더: data/video_notes/{TAG})")
    if limit:
        items = items[:limit]
    # 기존 대략값(입력 6천, 출력 700 토큰)을 따르되 A+B 합계 기준: 입력 2배, 출력 1.2배
    est = len(items) * (16000 * PRICE_PER_M[0] + 1100 * PRICE_PER_M[1]) / 1e6
    print(f"이번 실행: {len(items)}곳, 예상 비용 약 ${est:.2f} (gpt-4o-mini, 가게당 호출 3~4회: 코스 판단, A, B, 셰프)")
    if dry_run:
        return
    fails, done = 0, 0
    for vid, store, others in items:
        status = process(vid, store, others, sequential)
        done += status == "ok"
        fails = fails + 1 if status == "fail" else 0
        if fails >= MAX_CONSECUTIVE_FAILS:
            print(f"연속 {fails}번 실패해서 멈춥니다. 같은 명령을 다시 실행하면 이어서 처리합니다.")
            break
    print(f"완료 {done}곳")
    print_comparison(all_keys, compare_tag)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="자막에서 가게 정보(A)와 메뉴·음료(B)를 나눠 추출한다. (A/B 분리 실험)")
    ap.add_argument("--video-id", action="append", default=[], help="이 영상만 처리 (여러 번 지정 가능)")
    ap.add_argument("--limit", type=int, default=None, help="이번 실행에서 처리할(아직 처리 안 된) 가게 수")
    ap.add_argument("--dry-run", action="store_true", help="API 호출 없이 대상과 예상 비용만 출력")
    ap.add_argument("--sequential", action="store_true", help="A, B 를 동시에 부르지 않고 순서대로 부른다(프롬프트 캐시가 걸릴 수 있다)")
    ap.add_argument("--tag", default=None, help=f"결과 저장 폴더와 model 값. 기본 {TAG}")
    ap.add_argument("--compare-tag", default=BASELINE_TAG, help=f"비교할 기존 결과 폴더. 기본 {BASELINE_TAG}")
    ap.add_argument("--compare-only", action="store_true", help="호출 없이 저장된 결과만 비교한다")
    args = ap.parse_args()
    if args.tag:
        TAG = args.tag
    main(args.video_id, args.limit, args.dry_run, args.sequential, args.compare_tag, args.compare_only)
