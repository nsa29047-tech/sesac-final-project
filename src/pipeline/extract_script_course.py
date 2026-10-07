"""
식당별 자막 구간에서 방문 후기 형태(코스 순서, 요리별 감상, 음료, 총평, 키 포인트, 셰프)로 식당 정보를 추출한다.

팀원 챗봇(main.py)의 후기 생성 방식에서 접근법만 가져왔다.
- 질문 시점에 검색한 대본 조각이 아니라 가게의 자막 구간 전체를 한 번에 본다(조각 검색은 요리 누락과 설명 누락이 생긴다).
- 요리를 나온 순서대로 나열하고, 요리마다 감상·시각을 한 항목에 묶고, 음료·총평·한 줄 결론을 같은 호출에서 받는다.
팀원 방식과 다른 점은 아래와 같다.
- 유튜버의 말투를 살리지 않고 정리된 평서문으로 쓴다. 감상은 자막에 근거가 있는 구체적 묘사만 쓰고, 평가만 있으면 null이다.
- 추출 시점에 만들어 DB(menus, video_restaurant_notes)에 저장한다(카드를 열 때마다 LLM을 부르지 않는다).

흐름(가게 1곳 기준)
1. 입력: 영상이 여러 식당이면 chunk_transcripts.video_parts()의 식당 구간 자막만 쓴다(원문 청크와 같은 구간).
2. 전체 호출 1회 + 6분 구간(30초 겹침) 추출 -> LLM 중복 제거로 합친다. 긴 자막을 한 번에 넣으면 눈에 띄는 요리만 고르는 누락이 생겨서다(v5/v7와 같은 방식).
   수동 자막이면 제작자가 단 한 줄 소제목("닭날개", "당근 수프")을 코드로 찾아 "요리 이름 후보"로 프롬프트에 넣는다.
3. 후처리: 시각 보정, 요리 순서 재계산, 범주 이름·품절 요리 제외, 평가어 제거(규칙 + LLM 정리 패스), 자막 근거 검수, 이름을 자막의 원어 표기로(풀이는 설명에).
4. 가게 단위 호출: 코스 여부(근거 줄 확인), 음식 카테고리(별도 호출), 셰프(extract_chef_info.py 의 프롬프트·검증).

결과는 data/video_notes/gpt-4o-mini-script-course/{video_id}__{google_cid}.json 에 기존 추출 결과와 같은 형식으로 저장하므로
load_notes.py --model gpt-4o-mini-script-course 로 적재할 수 있다. 이미 있는 결과는 다시 호출하지 않는다(--force 로 덮어씀).

실행 예:
  uv run python src/pipeline/extract_script_course.py --video-id=3vYwR8V8IaU --video-id=BCgIOkje9oQ
  uv run python src/pipeline/check_course_gold.py     # 지적받은 오류가 고쳐졌는지 점검
"""

import argparse
import json
import re
from pathlib import Path
from typing import List, Literal, Optional

from pydantic import BaseModel, Field

from chunk_transcripts import video_parts
from compare_prompt_v3 import DEDUPE_PROMPT, WINDOW_RULES, DedupeOut, key_of, same_item, user_message, windows_of
from extract_chef_info import chefs_for_store
from extract_script_notes import PRICE_PER_M, TIME_RULES, ScriptNotes, client, fix_times, hms_to_sec, load_stores
from extract_script_v7 import V7Menu, apply_dedupe, clean_texts, fmt_items, resolve_names, strip_generic, tidy_items, verify_texts
from extract_transcript_notes import Drink
from get_transcripts_since import OUTPUT_DIR as TRANSCRIPT_DIR, format_timestamp

ROOT = Path(__file__).resolve().parents[2]
TAG = "gpt-4o-mini-script-course"
MODEL = "gpt-4o-mini"
OUTPUT_DIR = ROOT / "data" / "video_notes" / TAG
WINDOW_MIN_SPAN_SEC = 540  # 가게 구간이 이보다 짧으면 구간 분할 없이 전체 호출 1회만 한다


class CourseMenu(V7Menu):
    order: int = Field(0, description="이 가게에서 요리가 나온 순서(1부터). 코스가 아니어도 먹은 순서대로 번호를 매긴다")
    extra_order: bool = Field(False, description="코스와 별도로 추가 주문한 요리면 true. 코스에 포함돼 나왔거나 코스가 아니면 false")


class CourseNotes(ScriptNotes):
    menus: List[CourseMenu] = Field(default_factory=list)
    verdict: Optional[str] = Field(None, description="한 줄 결론(추천 대상, 재방문 의사 등). 자막에 없으면 null")


class WindowItemsC(BaseModel):
    menus: List[CourseMenu] = Field(default_factory=list)
    drinks: List[Drink] = Field(default_factory=list)


SYSTEM_PROMPT = """너는 미식 유튜브 영상의 시각 자막에서 한 식당의 방문 후기를 구조화해 정리하는 편집자야. 자막은 [대상 가게]를 소개하는 구간이다. 처음부터 끝까지 읽고 [대상 가게]에서 먹은 것만 정리한다.

[기본 규칙]
- 자막에 나온 내용만 쓴다. 없는 정보는 null 또는 빈 목록으로 두고, 추측하거나 일반 상식으로 채우지 않는다.
- 자동 생성 자막의 오타는 문맥과 [대상 가게]로 보정한다. 보정이 안 되는 글자는 쓰지 않는다.
- 같은 영상의 다른 가게 정보를 섞지 않는다. 식당 역사, 가격 이야기, 잡담은 메뉴가 아니다.
- 자막에서 괄호 ( )로 감싼 줄은 제작자가 단 설명이다. 요리 설명, 평가, 비교("~ 같은 느낌")의 근거로 쓴다.
- [요리 이름 후보]가 주어지면 그 중 이 가게에서 실제로 먹은 요리는 빠짐없이 menus에 넣는다. 같은 요리의 다른 표기(예: 줄임말과 전체 이름)는 하나로 합치고, 먹지 않았거나 요리가 아닌 후보는 넣지 않는다. 후보에 없어도 먹은 요리는 넣는다.
- [소제목 후보]가 주어지면 제작자가 단 한 줄 제목이다. 요리 이름이면 빠짐없이 menus에 넣고, 그 아래 이어지는 자막을 그 요리의 설명으로 쓴다. 요리 이름이 아니면 무시한다.

[메뉴]
- menus에는 이 가게에서 먹은 요리를 먹은 순서대로 빠짐없이 넣고 order에 1부터 번호를 매긴다. 아뮤즈부쉬, 빵, 디저트, 프티푸르, 곁들임, 반찬도 구체적인 요리나 재료로 말했으면 각각 넣는다. "스프", "디저트", "고기", "생선"처럼 범주만 말했으면 넣지 않는다.
- 같은 접시에 함께 서빙됐더라도 이름이 따로 있는 요리는 항목을 따로 만든다. 한 항목의 설명에는 그 요리의 재료와 평가만 쓰고 다른 요리의 재료나 평가를 섞지 않는다.
- 먹지 않은 요리는 넣지 않는다: 메뉴판을 읽기만 한 요리, "오늘은 없다"거나 품절이라 못 먹은 요리, 주문하려다 실패한 요리, 직원이 추천만 한 요리, 다른 가게에서 먹은 요리.
- 가게나 유튜버의 일반적인 이야기(예: 반찬이 예전과 달라졌다, 요즘 유행이다)는 요리가 아니다.
- 와인, 샴페인, 사케, 위스키, 맥주, 칵테일, 차, 커피, 주스는 menus가 아니라 drinks에 넣는다. 병 이름이나 빈티지 표기가 있어도 음료다. 음료도 빠짐없이 넣는다.
- extra_order: 코스와 별도로 추가 주문한 요리만 true이고 나머지는 false다.

[메뉴 이름]
- name에는 자막에서 그 요리를 부를 때 쓴 이름을 쓴다. 외국어 요리명이면 자막의 한글 발음 표기를 쓰고 한국어 일반 명칭으로 바꿔 쓰지 않는다. 같은 요리를 여러 이름으로 불러도 항목은 하나만 만든다.
- 자막에 한국어 풀이나 일반 명칭이 함께 나오면(괄호 설명 포함) alt_name에 그 풀이를 쓴다. [요리 이름 후보]에 같은 요리의 한국어 풀이(예: 원어 표기와 나란히 있는 번역)가 있으면 그것도 alt_name에 쓴다. 자막이나 후보에 없는 풀이는 일반 지식으로 채우지 않고 null로 둔다.

[요리별 칸 쓰는 법]
- cooking_features: 재료·조리법·소스·곁들임·제공 방식만 쓴다. 메뉴 이름을 풀어 쓴 것에 불과하면 null이다.
- taste_review: 그 요리에 대해 자막에서 말한 맛·식감·향·온도의 구체적인 묘사를 1~2문장으로 쓴다. 같은 접시에 대한 말을 앞뒤 자막에서 모아 한 항목에 담되, 다른 요리에 대한 말은 섞지 않는다. 구체적인 묘사가 자막에 없으면 반드시 null이다. 길이를 채우려고 형용사를 덧붙이지 않는다.
- 한 마리·한 덩이를 부위별로 나눠 요리를 낸다는 설명(예: "○○ 부위는 A 요리, △△ 부위는 B 요리")은 해당 요리의 cooking_features에 부위와 함께 쓴다.
- 요리를 다른 음식에 빗댄 말("~ 같은 느낌", "~를 생각하며 만든 요리")과 소스·곁들임의 맛 특징("약간 매콤하게", "죽 같은 질감")은 cooking_features에 쓴다.
- tips: 자막에 직접 나온 먹는 방법·주문 요령만 쓴다. 추가 요금이나 대체 옵션 안내("추가 시 ○○로 대체")는 price 에 금액과 함께 쓴다.
- 구어체·감탄사·말버릇·반말·의문형("~잖아", "~죠?", "~네", "진짜", "대박")과 "엄청", "너무", "딱", "완전" 같은 강조 부사는 빼고 정리된 평서문으로 쓴다. 자막 문장을 그대로 옮기지 않는다. 원문 인용은 evidence에만 둔다.
- "맛있다", "훌륭하다", "좋다", "최고다", "완벽하다", "인상적이다", "조화가 좋다"처럼 평가만 있는 문장, "~가 좋다", "계속 찾게 되는 맛", "처음 먹어보는 맛" 같은 감상만 있는 문장은 쓰지 않는다. 구체적인 묘사 없이 이런 말뿐이면 taste_review는 null이다.
- 한 칸에 평가와 구체적인 묘사가 같이 있으면 구체적인 묘사만 남긴다(예: "매콤하고 진짜 맛있다" -> "매콤하다.").

[음료]
- drinks의 review에는 시음 소감 중 구체적인 묘사(향, 숙성, 산도, 온도, 페어링한 요리)와, 자막에 나온 그 음료의 설명(산지, 품종, 특징, 개봉 후 향이 변하는 정도, 마시는 요령)을 쓴다. 평가만 있으면 null이다.

[가게 전체]
- final_review는 유튜버의 총평을 2~3문장의 정리된 평서문으로 쓴다. 평가만 반복하지 말고 근거가 되는 구체적인 이유(메뉴, 서비스, 분위기)를 함께 쓴다. 자막에 총평이 없으면 null이다.
- verdict는 한 줄 결론(어떤 사람에게 추천하는지, 재방문 의사)이며 자막에 없으면 null이다.
- key_points에는 이 식당의 차별점, 유명 메뉴, 추천·비추천 주문 요령(무엇을 시키고 무엇은 피하라는 말), 예약·웨이팅·결제·언어 같은 방문 팁을 자막에 나온 것만 한 줄에 하나씩 정리된 평서문으로 쓴다.
- atmosphere에는 자막에서 확인되는 인테리어, 분위기, 손님층, 주문·운영 방식을 쓴다.
- embedding_text는 식당 컨셉, 대표 메뉴와 구체적인 맛 표현, 분위기, 방문 팁을 포함한 3~5문장의 정리된 한국어 문단이다.

예시(다른 영상의 예시이며 이 영상의 내용이 아니다):
- 자막 "와 이거 껍질 진짜 바삭하네 안은 촉촉하고" -> taste_review "껍질이 바삭하고 속이 촉촉하다."
- 자막 "이거 진짜 맛있다 최고다" -> taste_review null
- 자막 "소스에 찍어 먹으면 더 맛있어요" -> taste_review null, tips "소스에 찍어 먹는다."
- 자막 "(튀김은 실패가 적다) 이건 꼭 시키세요" -> key_points "튀김은 실패가 적어 꼭 시킨다."
""" + TIME_RULES


NAMES_MODEL = "gpt-4o"  # 이름만 나열하는 단순한 일이지만 gpt-4o-mini 는 설명이 짧은 요리(소제목 한 줄짜리)를 자주 빠뜨려서 이 단계만 gpt-4o 를 쓴다
PRICE_4O = (2.5, 10.0)


class DishNames(BaseModel):
    dishes: List[str] = Field(default_factory=list, description="이 가게에서 먹은 요리 이름(먹은 순서). 음료는 제외")


NAMES_PROMPT = """너는 식당 방문 영상의 자막에서 [대상 가게]에서 먹은 요리의 이름만 모두 나열하는 편집자야. 설명은 쓰지 않는다.
- 자막에 한 줄짜리 제목처럼 적힌 요리 이름과 그 아래에 이어지는 자막이 그 요리 이야기다. 제목처럼 보이는 줄마다 요리인지 판단한다.
- 같은 접시에 같이 나와도 별개의 요리(예: 토스트와 수프)면 따로 적는다. 한 요리의 재료·소스·곁들임·가니시(예: 아이스크림, 소스, 퓌레)는 그 요리에 포함된 것이라 따로 적지 않는다. 자막에서 부른 이름대로 적고, 외국어 요리명은 자막의 한글 표기를 쓴다.
- 범주 한 단어(고기, 생선, 메인 요리)와 먹지 않은 요리(품절, 메뉴판만 읽음, 추천만 받음)는 제외한다. 음료는 제외한다.
- "디저트"처럼 범주로만 소개하고 이어서 구체적인 요리나 재료가 나오면(예: "디저트입니다" 뒤에 "치즈케이크와 베리 소스") 범주 대신 그 구체적인 요리 이름을 적는다.
  이름 없이 "메인"이라고만 소개된 접시는 주재료를 묶어 하나로 적고(예: "소고기 안심, 우족편"), 그 접시에 곁들인 다른 요리가 자막에서 이름과 함께 따로 소개되면(예: "○○ 겉절이입니다") 그것도 따로 적는다."""


def name_candidates(store: dict, lines: List[dict], usage4o: dict) -> List[str]:
    """요리 이름만 나열한다. 긴 구간은 6분(30초 겹침)씩 나눠 구간마다 부르고, 같은 이름(표기 차이 포함)은 하나로 합친다."""
    span = lines[-1]["start"] - lines[0]["start"]
    chunks = [text for _, text in windows_of(lines)] if span > WINDOW_MIN_SPAN_SEC else \
        ["\n".join(f"[{format_timestamp(l['start'])}] {l['text']}" for l in lines)]
    names: List[str] = []
    for text in chunks:
        r = client.beta.chat.completions.parse(
            model=NAMES_MODEL, temperature=0, seed=42, response_format=DishNames,
            messages=[{"role": "system", "content": NAMES_PROMPT},
                      {"role": "user", "content": f"[대상 가게]\n- {store['korean_name']} / {store['address']}\n\n[자막]\n{text}"}])
        usage4o["prompt_tokens"] += r.usage.prompt_tokens
        usage4o["output_tokens"] += r.usage.completion_tokens
        usage4o["calls"] += 1
        for d in r.choices[0].message.parsed.dishes:
            d = d.strip()
            if d and key_of(d) not in GENERIC_FOODS and not any(same_item(d, n) for n in names):
                names.append(d)
    return names


def names_header(names: List[str]) -> str:
    return ("[요리 이름 후보]\n" + "\n".join(f"- {n}" for n in names) + "\n\n") if names else ""


def call(system: str, user: str, schema, usage: dict, model: str = MODEL):
    r = client.beta.chat.completions.parse(
        model=model, temperature=0, seed=42, response_format=schema,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}])
    usage["prompt_tokens"] += r.usage.prompt_tokens
    usage["output_tokens"] += r.usage.completion_tokens
    usage["calls"] += 1
    return r.choices[0].message.parsed.model_dump()


# ---------------------------------------------------------------------------------------------------------------------
# 소제목 후보: 제작자 자막(수동)에는 요리가 나오는 장면 앞에 "닭날개", "당근 수프"처럼 이름만 적은 짧은 줄이 있다.
# 코드가 이런 줄을 찾아 힌트로 주면 모델이 설명이 짧은 요리를 빠뜨리는 일이 줄어든다.
# ---------------------------------------------------------------------------------------------------------------------
HEADING_END = re.compile(r"(다|요|죠|네|까|래|야|지|나|고|서|데|걸|니다|세요|어|아|군|구나|ㅋ+|ㅎ+|[.!?~…])$")
HEADING_SKIP = re.compile(r"^[(\[<*]|https?://|\d{2,}[-\s]\d{3,}|^\d+$|안녕|감사|구독|좋아요|영상|촬영|추천")


def heading_candidates(lines: List[dict], source: Optional[str]) -> List[dict]:
    if source != "manual_ko":
        return []
    out = []
    for l in lines:
        t = l["text"].strip()
        if 2 <= len(t) <= 28 and len(t.split()) <= 5 and not HEADING_SKIP.search(t) and not HEADING_END.search(t) and re.search(r"[가-힣A-Za-z]", t):
            out.append(l)
    return out[:80]


def heading_header(cands: List[dict]) -> str:
    if len(cands) < 2:
        return ""
    return "[소제목 후보]\n" + "\n".join(f"- [{format_timestamp(l['start'])}] {l['text'].strip()}" for l in cands) + "\n\n"


class SingleDish(BaseModel):
    found: bool = Field(description="[대상 요리]를 이 가게에서 먹은 별개의 요리로 항목을 만들 수 있으면 true. 먹지 않았거나 [이미 정리된 메뉴]의 요리와 같은 요리(다른 표기)이면 false")
    menu: Optional[CourseMenu] = None


FILL_PROMPT = SYSTEM_PROMPT.split("[기본 규칙]")[0] + """[이 호출에서 할 일]
[대상 요리] 하나가 주어진다. 자막에서 이 요리에 대한 이야기를 찾아 menus 항목 1개(menu)를 만든다.
- 이름은 자막에서 부른 이름(외국어 요리명이면 자막의 한글 발음 표기)이고, 풀이가 자막에 있으면 alt_name 에 쓴다.
- 설명(cooking_features, taste_review, tips)은 자막에 나온 이 요리에 대한 내용만 쓰고, 다른 요리의 내용은 섞지 않는다. 구어체와 평가어는 쓰지 않는다. 없는 칸은 null 이다.
- 먹지 않은 요리(품절, 메뉴판만 읽음, 추천만 받음)이거나 [이미 정리된 메뉴]에 같은 요리가 있으면 found 를 false 로 한다.
""" + TIME_RULES


MAX_FILLS = 12                # 가게당 보충 호출 상한(비용 상한, 호출당 약 $0.004). 이보다 많이 빠졌으면 이름 후보 단계가 과하게 뽑은 것이다
FILL_CONTEXT_SEC = (30, 120)  # 후보가 처음 언급된 시각 앞뒤로 보여 줄 자막 범위(초)


def mention_excerpt(name: str, lines: List[dict]) -> str:
    """후보 이름이 처음 나오는 자막 줄 주변만 잘라 낸다. 이름이 자막에 없으면(모델이 풀어 쓴 이름) 빈 문자열."""
    key = key_of(name)
    hit = next((l for l in lines if key and key in key_of(l["text"])), None)
    if hit is None:
        words = re.findall(r"[가-힣A-Za-z]{2,}", name)
        hit = next((l for l in lines if any(w in l["text"] for w in words)), None) if words else None
    if hit is None:
        return ""
    lo, hi = hit["start"] - FILL_CONTEXT_SEC[0], hit["start"] + FILL_CONTEXT_SEC[1]
    return "\n".join(f"[{format_timestamp(l['start'])}] {l['text']}" for l in lines if lo <= l["start"] <= hi)


def fill_missing(merged: dict, names: List[str], store: dict, lines: List[dict], usage4o: dict) -> List[str]:
    """이름 후보 중 menus 에 같은 요리가 없는 것만 하나씩 따로 물어 항목을 보충한다(전체 호출이 설명이 짧은 요리를 빠뜨리는 경우가 있어서).
    후보를 언급한 자막 주변만 보여 주고 가게당 MAX_FILLS 번까지만 부른다."""
    added, calls = [], 0
    for name in names:
        have = [m["name"] for m in merged["menus"]] + [m.get("alt_name") or "" for m in merged["menus"]]
        if any(same_item(name, h) for h in have if h) or calls >= MAX_FILLS:
            continue
        excerpt = mention_excerpt(name, lines)
        if not excerpt:
            continue
        calls += 1
        user = (f"[대상 가게]\n- {store['korean_name']} / {store['address']}\n\n[대상 요리]\n- {name}\n\n"
                f"[이미 정리된 메뉴]\n{fmt_items(merged['menus'])}\n\n[자막(이 요리가 언급되는 부분)]\n{excerpt}")
        out = call(FILL_PROMPT, user, SingleDish, usage4o, NAMES_MODEL)  # 후보를 놓친 경우라 어려운 판단이다. gpt-4o-mini 는 닭날개 같은 요리를 앞 요리의 일부로 보고 거절했다
        if out["found"] and out["menu"] and out["menu"]["name"].strip() and not any(same_item(out["menu"]["name"], h) for h in have if h):
            merged["menus"].append({**out["menu"], "from_fill": True})
            added.append(out["menu"]["name"])
    return added


class DrinkList(BaseModel):
    drinks: List[Drink] = Field(default_factory=list)


DRINKS_PROMPT = """너는 식당 방문 영상의 자막에서 [대상 가게]에서 마신 음료(와인, 샴페인, 사케, 위스키, 맥주, 칵테일, 차, 커피, 주스 등)를 모두 뽑는 편집자야.
- name: 자막에 나온 이름(병 이름, 빈티지 포함). 자막에 한글 표기가 있으면 한글 표기를 쓰고, 한글 표기가 없을 때만 원문 표기를 쓴다. 같은 음료는 한 항목만 만든다. 물과 기본 제공 음료는 넣지 않는다.
- review: 시음 소감 중 구체적인 묘사(향, 숙성, 산도, 온도)와, 자막(제작자가 괄호 ( )로 단 설명 포함)에 나온 그 음료의 설명(산지, 품종, 특징, 개봉 후 향이 변하는 정도, 마시는 요령, 페어링한 요리)을 1~2문장의 정리된 평서문으로 쓴다.
  그 음료를 가리키는 말에서만 가져오고 같은 구간에서 먹은 요리에 대한 말은 섞지 않는다. 구어체와 "맛있다", "좋다" 같은 평가만 있는 말은 쓰지 않고, 설명이 없으면 null 이다. 자막에 없는 내용은 일반 상식으로 채우지 않는다.
- 마시지 않고 언급만 한 음료(다른 가게, 일반론, 다음에 마실 계획)는 제외한다."""


def extract_drinks(store: dict, lines: List[dict], usage: dict) -> List[dict]:
    """음료는 전용 호출로 뽑는다. 요리 이름 후보와 소제목 힌트를 넣은 호출에서는 모델이 음료를 출력하지 않았고, 구간 호출에서도 거의 비어 있었다."""
    span = lines[-1]["start"] - lines[0]["start"]
    chunks = [text for _, text in windows_of(lines)] if span > WINDOW_MIN_SPAN_SEC else \
        ["\n".join(f"[{format_timestamp(l['start'])}] {l['text']}" for l in lines)]
    drinks: List[dict] = []
    for text in chunks:
        out = call(DRINKS_PROMPT, f"[대상 가게]\n- {store['korean_name']} / {store['address']}\n\n[자막]\n{text}", DrinkList, usage)
        for d in out["drinks"]:
            match = next((x for x in drinks if same_item(x["name"], d["name"])), None)
            if match is None:
                drinks.append(d)
            elif d.get("review") and (not match.get("review") or len(d["review"]) > len(match["review"])):
                match["review"] = d["review"]  # 같은 음료는 더 자세한 설명을 쓴다
    return drinks


# ---------------------------------------------------------------------------------------------------------------------
# 후처리
# ---------------------------------------------------------------------------------------------------------------------
GENERIC_FOODS = {"고기", "생선", "채소", "야채", "해산물", "반찬", "음식", "요리", "밥", "국", "면", "술", "안주", "메뉴", "음료"}
SOLDOUT = re.compile(r"품절|sold\s*out|떨어졌|재고")


def has_text(m: dict) -> bool:
    return bool(m.get("cooking_features") or m.get("taste_review") or m.get("tips"))


def merge_same_menus(menus: List[dict], log: dict) -> List[dict]:
    """이름이 같은 요리(철자·띄어쓰기 차이 포함)는 하나로 합치고 빈 칸을 채운다. 보충 호출이 이미 있는 요리를 한 번 더 만든 경우를 정리한다."""
    kept: List[dict] = []
    for m in menus:
        match = next((k for k in kept if same_item(k["name"], m["name"])), None)
        if match is None:
            kept.append(m)
        else:
            for key, value in m.items():
                if match.get(key) in (None, "", "none") and value not in (None, "", "none"):
                    match[key] = value
            log["merged_into_existing"].append(f"{m['name']} -> {match['name']}")
    return kept


def drop_non_menus(merged: dict, lines: List[dict]) -> List[str]:
    """범주 한 단어("고기", "생선")이거나, 설명이 하나도 없는데 근처 자막에 품절 표현이 있는 항목(주문했지만 못 먹은 요리)을 뺀다."""
    kept, dropped = [], []
    for m in merged["menus"]:
        key = key_of(m["name"])
        sec = hms_to_sec(m.get("mentioned_at"))
        sold_out = sec is not None and any(SOLDOUT.search(l["text"]) for l in lines if sec - 10 <= l["start"] <= sec + 25)
        if (key in GENERIC_FOODS and not has_text(m)) or (sold_out and not has_text(m)):
            dropped.append(m["name"])
        else:
            kept.append(m)
    merged["menus"] = kept
    return dropped


def renumber(menus: List[dict]) -> None:
    """order 를 먹은 시각 순으로 다시 매긴다. 구간 추출로 합친 항목은 order 가 없고, 모델이 적은 order 가 시각과 어긋나는 경우도 있어서다.
    시각이 없는 항목은 바로 앞 항목과 같은 시각으로 보고 원래 순서를 유지한다."""
    eff, last = [], 0
    for m in menus:
        sec = hms_to_sec(m.get("mentioned_at"))
        last = sec if sec is not None else last
        eff.append(last)
    for rank, (_, m) in enumerate(sorted(zip(eff, menus), key=lambda x: x[0]), 1):  # 정렬은 안정적이라 같은 시각이면 원래 순서
        m["order"] = rank
    menus.sort(key=lambda m: m["order"])


NULL_STRINGS = {"null", "none", "n/a", "-", "없음"}


def null_strings_to_none(merged: dict) -> None:
    """모델이 null 대신 문자열 "null" 등을 값으로 쓰는 경우가 있어 None 으로 바꾼다."""
    for m in merged["menus"]:
        for f in ("cooking_features", "taste_review", "tips", "alt_name"):
            if isinstance(m.get(f), str) and m[f].strip().lower() in NULL_STRINGS:
                m[f] = None
    for d in merged["drinks"]:
        if isinstance(d.get("review"), str) and d["review"].strip().lower() in NULL_STRINGS:
            d["review"] = None
    if isinstance(merged.get("category_detail"), str) and merged["category_detail"].strip().lower() in NULL_STRINGS:
        merged["category_detail"] = None


def finalize_names(merged: dict) -> None:
    """이름은 자막의 원어 표기(spoken_name 또는 모델이 쓴 name)로 두고, 번역·풀이(alt_name)는 설명 맨 앞에 붙인다.
    예: name "푸아그라 드 카나드", cooking_features "오리 푸아그라. 간단하게 제공"."""
    for m in merged["menus"]:
        raw, spoken, alt = m["name"], m.pop("spoken_name", None), (m.get("alt_name") or "").strip() or None
        if spoken:
            m["name"] = spoken
            # 풀이는 한국어 번역이다. 모델이 알파벳 원어를 alt_name 에 썼으면 원래 이름(번역)을 풀이로 쓴다
            alt = next((c for c in (alt, raw) if c and re.search(r"[가-힣]", c) and key_of(c) != key_of(spoken)), alt or raw)
        if alt and key_of(alt) != key_of(m["name"]) and key_of(alt) not in key_of(m.get("cooking_features") or ""):
            m["cooking_features"] = f"{alt.rstrip('.')}." + (f" {m['cooking_features']}" if m.get("cooking_features") else "")
        m["alt_name"] = alt if alt and key_of(alt) != key_of(m["name"]) else None
        m["raw_name"] = raw


# ---------------------------------------------------------------------------------------------------------------------
# 가게 단위 호출: 코스 여부, 음식 카테고리
# ---------------------------------------------------------------------------------------------------------------------
COURSE_WORDS = re.compile(r"코스|테이스팅|오마카세|세트")


class CourseCheck(BaseModel):
    is_course: bool = Field(description="이 가게에서 코스(테이스팅 메뉴, 코스 요리, 세트, 오마카세)로 식사했으면 true. 단품을 골라 주문했거나 코스가 아닌 이야기(다른 가게, 일반론)면 false")
    course_name: Optional[str] = Field(None, description="코스 이름. 자막에서 부른 이름(예: 디너 코스, 런치 코스)을 쓰고 이름이 없으면 '코스'. is_course 가 false면 null")
    line: Optional[int] = Field(None, description="판단의 근거가 된 줄 번호(사용자가 준 번호 그대로). is_course 가 false면 null")


COURSE_PROMPT = """너는 식당 방문 영상의 자막에서 이 가게의 식사가 코스였는지 판단하는 검수자야. [대상 가게]의 자막 중 '코스', '테이스팅', '오마카세', '세트'라는 말이 들어 있는 줄이 번호와 함께 주어진다.
- 이 가게에서 코스로 식사했다는 말(예: "코스요리 시작", "디너 코스로 먹는다")이 있으면 is_course 를 true로 하고 근거 줄 번호를 line 에 적는다.
- 코스가 아닌 이야기(다른 가게 이야기, "코스가 아니라 단품", 여행 코스, 일반적인 설명)뿐이면 false다.
- 근거가 불분명하면 false다. 자막에 없는 사실을 지어내지 않는다."""


def detect_course(lines: List[dict], store: dict, usage: dict) -> Optional[dict]:
    """코스 여부를 가게 단위로 한 번만 판단한다. 요리별로 모델에 맡기면 코스를 놓치거나 단품에 붙이는 일이 잦아서,
    코드가 '코스'류 단어가 있는 자막 줄만 골라 보여 주고 그 줄만 보고 답하게 한다. 근거 줄은 코드가 직접 가져온다."""
    cands = [l for l in lines if COURSE_WORDS.search(l["text"])][:12]
    if not cands:
        return None
    shown = "\n".join(f"{i}. [{format_timestamp(l['start'])}] {l['text']}" for i, l in enumerate(cands, 1))
    c = call(COURSE_PROMPT, f"[대상 가게]\n- {store['korean_name']} / {store['address']}\n\n[자막 줄]\n{shown}", CourseCheck, usage)
    if not (c["is_course"] and c["line"] and 1 <= c["line"] <= len(cands)):
        return None
    ev = cands[c["line"] - 1]
    return {"name": (c["course_name"] or "코스").strip(), "evidence": ev["text"], "evidence_sec": int(ev["start"])}


def apply_course(merged: dict, course: Optional[dict]) -> None:
    """가게가 코스였으면 코스 밖 추가 주문(extra_order)이 아닌 요리 전부에 코스 이름을 붙인다. 코스가 둘 이상인 가게는 아직 구분하지 않는다."""
    merged["course"] = course
    for m in merged["menus"]:
        m["course_label"] = course["name"] if course and not m.get("extra_order") else None
        m.pop("extra_order", None)


class Cuisine(BaseModel):
    broad: Literal["한식", "일식", "중식", "양식", "동남아식", "인도식", "중동식", "기타"] = Field(description="이 식당이 내는 음식의 대분류")
    detail: Optional[str] = Field(None, description="세부 분류를 짧은 구로. 근거가 부족하면 null")
    tags: List[str] = Field(default_factory=list, description="대표 음식 키워드 최대 5개. 술·와인·재료 일반명은 넣지 않는다")


CUISINE_PROMPT = """너는 식당이 내는 음식의 종류를 분류하는 편집자야. 영상 제목, 가게 이름·주소·국가, 이 가게에서 나온 메뉴 이름이 주어진다.
- broad: 식당이 있는 나라가 아니라 식당이 내는 음식으로 정한다. 한식, 일식, 중식, 양식(프랑스·이탈리아·스페인 등 유럽과 아메리카 요리 전체, 스테이크 포함), 동남아식, 인도식, 중동식, 기타 중에서 고른다.
  나라만 보고 정하지 않는다. 식당 컨셉·소개에 나온 말(예: "모던 ○○", "○○ 가정식")이 가장 중요한 근거이고, 그 다음이 영상 제목과 메뉴의 재료·조리법이다.
  재료나 조리법이 그 나라 음식이면(예: 장·젓갈·전·만두를 다른 식재료와 섞어 낸 현대적인 요리) 그 나라 음식으로 본다.
- detail: 음식 종류와 형태를 담은 짧은 구(예: 오마카세, 삼겹살, 라멘, 스테이크, 비스트로, 딤섬, 타파스 바). 메뉴 이름과 제목에서 근거가 보일 때만 쓰고 부족하면 null이다.
- tags: 대표 요리 키워드를 최대 5개."""


def classify_cuisine(store: dict, merged: dict, usage: dict) -> dict:
    menus = "\n".join(f"- {m['name']}" + (f": {m['cooking_features'][:50]}" if m.get("cooking_features") else "") for m in merged["menus"][:30]) or "(없음)"
    points = "\n".join(f"- {k}" for k in merged.get("key_points", [])[:4])
    user = (f"[영상 제목]\n{store['video_title']}\n\n[가게]\n- {store['korean_name']} / {store['address']} / 국가 {store.get('country_code', '')}\n\n"
            f"[식당 컨셉·소개]\n{merged.get('concept') or '(없음)'}\n{points}\n\n[나온 메뉴]\n{menus}")
    return call(CUISINE_PROMPT, user, Cuisine, usage)


# ---------------------------------------------------------------------------------------------------------------------
# 가게 1곳 처리
# ---------------------------------------------------------------------------------------------------------------------
def extract_store(vid: str, store: dict, others: List[dict], lines: List[dict], source: Optional[str], usage: dict, usage4o: dict) -> dict:
    starts = [int(l["start"]) for l in lines]
    transcript = "\n".join(f"[{format_timestamp(l['start'])}] {l['text']}" for l in lines)
    heads = heading_candidates(lines, source)
    names = name_candidates(store, lines, usage4o)
    merged = {"video_id": vid, "video_title": store["video_title"], "google_cid": store["google_cid"].strip(), "korean_name": store["korean_name"],
              "address": store["address"], "google_official_name": store["google_official_name"], "transcript_source": source,
              **call(SYSTEM_PROMPT, user_message(store, others, transcript, names_header(names) + heading_header(heads)), CourseNotes, usage)}
    merged["drinks"] = extract_drinks(store, lines, usage)

    # 구간 추출: 긴 구간은 6분(30초 겹침)씩 다시 뽑아 전체 호출이 놓친 요리를 보충한다
    cand_menus, cand_drinks, log = [], [], {"candidate_menus": 0, "candidate_drinks": 0, "merged_into_existing": [], "dropped_category": []}
    if lines[-1]["start"] - lines[0]["start"] > WINDOW_MIN_SPAN_SEC:
        for t, text in windows_of(lines):
            win_heads = [l for l in heads if t <= l["start"] < t + 360]
            header = f"(이 자막은 영상의 {t // 60}분~{(t + 360) // 60}분 구간이다)\n\n" + heading_header(win_heads)
            part = call(SYSTEM_PROMPT + WINDOW_RULES, user_message(store, others, text, header), WindowItemsC, usage)
            cand_menus += part["menus"]
        log["candidate_menus"] = len(cand_menus)
    if cand_menus:
        user = (f"[기존 메뉴]\n{fmt_items(merged['menus'])}\n\n[후보 메뉴]\n"
                + ("\n".join(f"{i}. {c['name']}" + (f" ({c['alt_name']})" if c.get("alt_name") else "") for i, c in enumerate(cand_menus, 1)) or "(없음)")
                + f"\n\n[기존 음료]\n{fmt_items(merged['drinks'])}\n\n[후보 음료]\n" + ("\n".join(f"{i}. {c['name']}" for i, c in enumerate(cand_drinks, 1)) or "(없음)"))
        out = call(DEDUPE_PROMPT, user, DedupeOut, usage)
        apply_dedupe(merged, "menus", cand_menus, out["menus"], log)

    log["filled_from_candidates"] = fill_missing(merged, names, store, lines, usage4o)
    merged["menus"] = merge_same_menus(merged["menus"], log)
    fix_times(merged, starts)
    null_strings_to_none(merged)
    stats = clean_texts(merged, usage)  # 구어체·평가어를 LLM 이 한 번 더 정리(입력은 추출된 문장뿐이라 새 내용을 만들 여지가 적다)
    null_strings_to_none(merged)
    for m in merged["menus"]:
        m["taste_review"] = strip_generic(m.get("taste_review"))
    for d in merged["drinks"]:
        d["review"] = strip_generic(d.get("review"))
    stats.update(verify_texts(merged, transcript, usage))
    stats.update(tidy_items(merged))
    stats["dropped_non_menu"] = drop_non_menus(merged, lines)
    if source == "manual_ko":  # 자동 생성 자막은 요리 이름에도 오타가 많아 자막 표기를 이름으로 쓰지 않는다
        resolve_names(merged, lines, usage)
    finalize_names(merged)
    renumber(merged["menus"])

    apply_course(merged, detect_course(lines, store, usage))
    cuisine = classify_cuisine(store, merged, usage)
    merged["category_broad"], merged["category_detail"], merged["cuisine_tags"] = cuisine["broad"], cuisine["detail"], cuisine["tags"]
    # 셰프는 메뉴 추출과 프롬프트·검증 규칙이 달라 별도 호출로 한다(한 호출에 필드를 더하면 메뉴 추출이 흔들렸다). 결과는 같은 파일에 둔다
    merged["chefs"] = chefs_for_store(store, others, lines, source, usage)
    if merged.get("verdict"):  # 한 줄 결론은 DB 컬럼이 없어 key_points 맨 앞에 둔다
        merged["key_points"] = [merged["verdict"]] + [k for k in merged["key_points"] if k != merged["verdict"]]
    merged["dedupe_log"], merged["clean_stats"] = log, stats
    merged["name_candidates"] = names
    return merged


def run(vid: str, force: bool) -> None:
    stores = load_stores().get(vid)
    if not stores:
        print(f"{vid}: 대상 가게 없음 (자막 대상이 아니거나 google_cid 없음)")
        return
    data = json.loads((TRANSCRIPT_DIR / f"{vid}.json").read_text(encoding="utf-8"))
    all_lines = [s for s in data["segments"] if s["text"].strip()]
    # 식당이 여러 곳이면 그 식당 구간의 자막만 입력으로 쓴다(chunk_transcripts.py 와 같은 구간). 같은 가게가 여러 구간이면 이어 붙인다
    lines_by_cid = {}
    for store, part in video_parts(vid, stores, all_lines):
        lines_by_cid.setdefault(store["google_cid"].strip(), []).extend(part)
    for store in stores:
        cid = store["google_cid"].strip()
        path = OUTPUT_DIR / f"{vid}__{cid}.json"
        if path.exists() and not force:
            print(f"{vid} / {store['korean_name']}: 이미 있음, 건너뜀")
            continue
        lines = lines_by_cid.get(cid, [])
        if not lines:
            print(f"{vid} / {store['korean_name']}: 구간 자막이 없어 건너뜀")
            continue
        usage = {"prompt_tokens": 0, "output_tokens": 0, "calls": 0}
        usage4o = {"prompt_tokens": 0, "output_tokens": 0, "calls": 0}
        merged = extract_store(vid, store, [s for s in stores if s is not store], lines, data.get("source"), usage, usage4o)
        cost = (usage["prompt_tokens"] * PRICE_PER_M[0] + usage["output_tokens"] * PRICE_PER_M[1]
                + usage4o["prompt_tokens"] * PRICE_4O[0] + usage4o["output_tokens"] * PRICE_4O[1]) / 1e6
        merged.update(model=TAG, usage=dict(usage, cost=round(cost, 6), names_model=NAMES_MODEL, names_usage=usage4o))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
        blank = sum(1 for m in merged["menus"] if not has_text(m))
        print(f"{vid} / {store['korean_name']}: 호출 {usage['calls']}+{usage4o['calls']}회 ${cost:.4f} | 메뉴 {len(merged['menus'])}(설명 없음 {blank}) 음료 {len(merged['drinks'])} | "
              f"구간 후보 합침 {len(merged['dedupe_log']['merged_into_existing'])} | 제외 {merged['clean_stats']['dropped_non_menu']}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="식당별 자막 구간에서 방문 후기 형태(코스 순서, 요리별 감상, 총평)로 추출한다.")
    ap.add_argument("--video-id", action="append", required=True, help="이 영상만 처리 (여러 번 지정 가능)")
    ap.add_argument("--force", action="store_true", help="이미 있는 결과도 다시 호출")
    args = ap.parse_args()
    for v in args.video_id:
        run(v, args.force)
