"""
시각 자막에서 식당 정보를 3단계로 추출한다(v6). 한 번에 "전체를 읽고 요리별로 정리"시키면 작은 모델은 없는 말을 꾸며 쓰고
큰 모델은 비워 두는 문제가 있어, 일을 나눈다.

1단계(요리 찾기): 자막 줄마다 번호를 붙여 주고 요리·음료별로 이름, 먹었는지 여부, 처음 언급된 줄, 나왔다/먹는다는 단서가 있는 줄, 이야기가 끝나는 줄만 뽑는다.
    한글·외국어 이름은 하나로 합치고, 메뉴판만 읽었거나 "오늘은 없다"고 한 요리는 제외한다. 시각은 모델이 아니라 줄 번호로 코드가 계산하므로 지어낼 수 없다.
2단계(요리별 정리): 요리마다 근거 줄 구간만 발췌해서 주고, 그 줄에 나온 말만으로 조리 특징·맛 평가·팁을 정리한다. 다른 요리의 말이 섞이지 않는다.
3단계(가게 정보): 자막 전체와 확인된 요리 목록으로 분류·분위기·key_points(주문 요령, 가격대, 결제, 언어)·총평·검색용 요약을 만든다.

결과는 data/video_notes/{모델}-script-v6/{video_id}__{google_cid}.json 에 기존 추출 결과(v2)와 같은 형식으로 저장하므로
load_notes.py --model {폴더명} 으로 그대로 적재할 수 있다. 이미 있는 결과는 다시 호출하지 않는다(--force 로 덮어씀).

실행 예:
  uv run python src/pipeline/extract_script_two_stage.py --video-id 3vYwR8V8IaU
  uv run python src/pipeline/extract_script_two_stage.py --video-id 3vYwR8V8IaU --model gpt-4o   # 모델을 바꿔 시험
"""

import argparse
import json
import re
from pathlib import Path
from typing import List, Literal, Optional

from pydantic import BaseModel, Field

from compare_extraction_methods import TRANSCRIPT_PROMPT_V2
from extract_script_notes import client, describe, load_stores
from extract_transcript_notes import TranscriptNotes
from get_transcripts_since import OUTPUT_DIR as TRANSCRIPT_DIR, format_timestamp

ROOT = Path(__file__).resolve().parents[2]
NOTES_DIR = ROOT / "data" / "video_notes"
PRICES = {"gpt-4o-mini": (0.15, 0.60), "gpt-4o": (2.50, 10.00)}  # (입력, 출력) USD / 1M 토큰
EXCERPT_PAD = 2        # 근거 구간 앞뒤로 더 보여 주는 줄 수
EXCERPT_MAX = 40       # 한 요리의 근거 구간 최대 줄 수


# -------------------------------------------------------------
# 스키마
# -------------------------------------------------------------
class DishSpan(BaseModel):
    name: str = Field(description="요리·음료 이름. 한글과 외국어가 같이 나오면 한글 이름 하나만 쓴다. '스프', '디저트' 같은 범주 이름은 안 된다")
    kind: Literal["food", "drink"] = Field(description="food: 먹는 것 / drink: 마시는 것(와인, 샴페인, 사케, 맥주, 차, 커피 등)")
    status: Literal["eaten", "recommended_only", "unavailable"] = Field(
        description="eaten: 이 가게에서 실제로 나와서 먹거나 마신 것(서비스 포함) / recommended_only: 메뉴판을 읽거나 추천만 했고 먹지 않음 / unavailable: 오늘은 없다고 함"
    )
    mention_line: int = Field(description="이 요리의 이름이 처음 나온 줄 번호(메뉴판 소개, 주문하는 줄 포함)")
    scene_start: Optional[int] = Field(
        None, description="이 요리가 테이블에 나오거나 먹기 시작하는 장면의 첫 줄 번호('나왔다', '먹어 볼게', '한입', 접시를 보여 주며 소개). "
        "주문하거나 메뉴판에서 이름만 부르는 줄이 아니다. eaten 이면 반드시 찾는다")
    scene_end: Optional[int] = Field(None, description="그 요리를 먹으며 하는 이야기가 끝나는 줄 번호(다른 요리나 다른 주제로 넘어가기 직전)")


class DishList(BaseModel):
    dishes: List[DishSpan] = Field(default_factory=list)


class DishDetail(BaseModel):
    index: int = Field(description="요리 번호(사용자가 준 번호 그대로)")
    cooking_features: Optional[str] = Field(None, description="구성·재료·조리·곁들임·제공 방식. 근거 줄에 나온 것만")
    taste_review: Optional[str] = Field(None, description="맛·식감·향·온도에 대한 평가. 근거 줄에서 이 요리에 대한 것만. 없으면 null")
    tips: Optional[str] = Field(None, description="이 요리를 주문하거나 먹는 방법으로 근거 줄에 직접 나온 것만. 없으면 null")
    price: Optional[str] = Field(None, description="이 요리의 가격이 근거 줄에 나온 경우만")
    evidence: str = Field(description="판단 근거가 된 줄의 원문 1~2줄(시각 없이 텍스트만)")


class DetailList(BaseModel):
    details: List[DishDetail] = Field(default_factory=list)


# -------------------------------------------------------------
# 프롬프트
# -------------------------------------------------------------
SPAN_PROMPT = """너는 미식 유튜브 영상 자막에서 식당의 요리·음료 목록을 찾는 분석가야. 자막은 "[줄번호] [HH:MM:SS] 텍스트" 형식이다.

사용자가 지정한 [대상 가게]에서 나온 요리와 음료를 모두 찾아 한 항목씩 답한다.
- 서비스로 나온 것, 곁들임(감자 퓌레 등), 코스의 각 접시, 디저트, 마신 와인·샴페인·차도 각각 별도 항목이다. 빠뜨리지 않는다.
- 같은 요리를 한글과 외국어로 여러 번 불러도 항목은 하나만 만들고, name에는 한글 이름 하나만 쓴다(예: "소시송"). 슬래시(/)로 이름을 이어 붙이지 않는다. 서비스로 먼저 나온 것과 주문한 모둠이 같은 종류면 하나로 합친다.
- 영상은 보통 먼저 메뉴판을 보며 이야기하고 주문한 뒤, 요리가 나오면 하나씩 먹는 순서다. 요리마다 scene_start는 "그 요리가 나와서 먹기 시작하는 장면"의 첫 줄이다. 메뉴판 소개나 주문 줄은 mention_line이다. 먹은 요리는 반드시 scene_start를 찾는다.
- 먹은 요리가 아니면(메뉴판을 읽으며 소개만 했거나 "맛있어", "유명해"라고 추천만 한 요리는 recommended_only, "오늘은 없다"고 한 요리는 unavailable) scene_start는 null이다. 먹은 요리는 recommended_only가 아니다. 곁들임이나 서비스도 먹었으면 eaten이다.
- 줄 번호는 입력에 적힌 번호를 그대로 쓴다. 계산하거나 지어내지 않는다.
- 와인, 샴페인, 사케, 위스키, 맥주, 칵테일, 차, 커피, 주스는 kind를 drink로 한다. 병 이름이나 빈티지 표기가 있어도 음료다.
- 자동 생성 자막의 오타는 문맥과 [대상 가게]로 보정한다. 가게 이야기가 아닌 구간(다른 식당 비교, 잡담)의 요리는 넣지 않는다.
- 요리가 아닌 설명(식당 역사, 가격 이야기, 주문 요령)은 항목이 아니다."""

DETAIL_PROMPT = """너는 미식 유튜브 영상 자막에서 요리별 정보를 정리하는 분석가야. 요리마다 번호가 붙은 자막 발췌가 주어진다.

규칙:
- 각 요리는 자기 발췌에 나온 말만 근거로 쓴다. 다른 요리의 발췌나 일반 상식, 추측으로 채우지 않는다. 근거가 없으면 null이다.
- 자막에 없는 형용사(부드럽다, 진하다, 풍부하다 등)를 만들어 붙이지 않는다. 발췌에 나온 표현과 의미가 같은 말만 쓴다.
- 구어체·감탄사·말버릇("진짜", "~더라고", "대박")은 빼고 명사형이나 평서문으로 정리한다. 발췌의 괄호 설명은 제작자가 쓴 요리 설명이니 근거로 쓴다.
- taste_review는 맛·식감·향·온도에 대한 평가만 쓴다. 외관·비주얼, 가격, 먹는 방법, 여러 요리를 한꺼번에 가리킨 말은 쓰지 않는다. 이유 없는 "맛있다" 한마디는 "맛있다는 평가"처럼 그대로 짧게 쓰거나 null로 둔다.
- 감탄이 어느 요리를 가리키는지 앞뒤 줄로 확인하고, 이 요리가 아니면 쓰지 않는다.
- cooking_features에는 구성, 재료, 조리법, 소스, 곁들임, 제공 방식(개수 단위, 뜨겁게 등)을 쓴다.
- tips에는 발췌에 직접 나온 주문 요령·먹는 방법만 쓴다(예: 모둠으로 시키면 좋다, 뜨거우니 조심). 없으면 null이다.
- 음료는 마신 소감과 설명을 taste_review에 쓴다. 자동 생성 자막의 오타는 문맥으로 보정하되, 보정이 안 되는 글자는 쓰지 않는다.
- 정보가 없는 칸은 문자열 "null"이나 "없음"이 아니라 JSON null로 둔다.
- 제공 상태(뜨겁게 나온다, 6개 단위로 나온다)는 cooking_features에 쓰고, 주의 사항("뜨거우니 조심")은 tips에 쓴다. 맛 칸에는 쓰지 않는다.

예시(발췌 -> 정리, 다른 가게의 예시이며 이 영상의 내용이 아니다):
- "(바삭하게 구워진 껍질)" -> taste_review: "껍질이 바삭하다."
- "나왔습니다 뜨거우니까 조심하세요" "(소스에 찍어서 냠)" -> cooking_features: "뜨겁게 나온다.", tips: "소스에 찍어 먹는다. 뜨거우니 주의한다."
- "와.. 진짜 이건 할 말이 없다" -> taste_review: "극찬하는 평가."
- "두 가지 고기가 같이 올라와 있네!" -> cooking_features: "두 가지 고기가 함께 올라간다."
- "이건 곱빼기로 시키면 진짜 👍" -> tips: "곱빼기 주문을 추천한다."
"""

OLD_RULE = "- 유튜버의 미식 표현(바삭함, 진한 육수 등)은 최대한 원래 표현을 살려 적는다."
assert OLD_RULE in TRANSCRIPT_PROMPT_V2
OVERVIEW_PROMPT = TRANSCRIPT_PROMPT_V2.replace(OLD_RULE, "- 유튜버의 표현은 핵심 어휘를 살리되 정리된 서술문으로 쓴다.") + """
[가게 정보 단계]
- 이 단계에서는 menus와 drinks를 빈 목록으로 둔다(다른 단계에서 처리한다). 사용자가 준 [확인된 요리 목록]은 embedding_text와 category_detail, cuisine_tags를 정할 때만 참고한다.
- key_points에는 자막에 직접 나온 주문 요령(몇 명이 가면 몇 개를 시키면 되는지), 가격대, 결제 수단, 언어 소통, 예약·위치 팁을 쓴다. 자막에 없는 일반론은 쓰지 않는다.
- embedding_text에는 대표 요리와 자막에 나온 맛 표현, 분위기, 방문 팁을 포함한다."""


# -------------------------------------------------------------
# 유틸
# -------------------------------------------------------------
EMPTY_WORDS = {"null", "none", "n/a", "없음", "-", ""}


def blank(value):
    """모델이 null 대신 문자열 "null", "없음" 등을 적은 경우 None 으로 바꾼다."""
    return None if value is None or str(value).strip().lower() in EMPTY_WORDS else value


def call(model: str, system: str, user: str, schema, usage: dict) -> dict:
    """호출 1회. 토큰과 비용은 모델별 단가로 계산해 usage 에 누적한다."""
    r = client.beta.chat.completions.parse(
        model=model, temperature=0, response_format=schema,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}])
    p_in, p_out = PRICES.get(model, PRICES["gpt-4o"])
    usage["prompt_tokens"] += r.usage.prompt_tokens
    usage["output_tokens"] += r.usage.completion_tokens
    usage["cost"] = round(usage.get("cost", 0) + (r.usage.prompt_tokens * p_in + r.usage.completion_tokens * p_out) / 1e6, 6)
    usage["calls"] += 1
    return r.choices[0].message.parsed.model_dump()


def numbered(segments: List[dict]) -> str:
    return "\n".join(f"[{i}] [{format_timestamp(s['start'])}] {s['text']}" for i, s in enumerate(segments))


def excerpt(segments: List[dict], span: dict) -> str:
    """요리의 근거 구간. scene_start 가 있으면 먹는 장면(start~end)만, 없으면 이름이 나온 줄 주변만 보여 준다."""
    if span["scene_start"] is not None:
        lo, hi = span["scene_start"], span["scene_end"]  # 앞뒤로 더 붙이지 않는다(앞 요리의 말이 섞인다)
    else:
        lo, hi = span["mention_line"] - EXCERPT_PAD, span["mention_line"] + EXCERPT_PAD
    lo, hi = max(0, lo), min(len(segments) - 1, hi, max(0, lo) + EXCERPT_MAX)
    return "\n".join(f"[{format_timestamp(segments[i]['start'])}] {segments[i]['text']}" for i in range(lo, hi + 1))


def short_name(name: str) -> str:
    """"소시송 / Saucisson" 처럼 이어 붙은 이름에서 한글이 있는 첫 부분만 남긴다."""
    parts = [p.strip() for p in re.split(r"\s*/\s*", name) if p.strip()]
    return next((p for p in parts if re.search(r"[가-힣]", p)), parts[0] if parts else name)


def clean_spans(spans: List[dict], n_lines: int) -> List[dict]:
    """이름을 정리하고, 줄 번호가 범위를 벗어난 항목을 고치거나 버리고, 같은 이름은 하나만 남긴다.
    먹는 장면의 끝은 다음 요리 장면이 시작되기 직전을 넘지 않게 해서 요리마다 발췌가 겹치지 않게 한다."""
    out, seen = [], set()
    for s in spans:
        s["name"] = short_name(s["name"])
        key = re.sub(r"[^0-9A-Za-z가-힣]", "", s["name"])
        if not key or key in seen or not 0 <= s["mention_line"] < n_lines:
            continue
        seen.add(key)
        if s["scene_start"] is not None and not 0 <= s["scene_start"] < n_lines:
            s["scene_start"] = None
        out.append(s)
    starts = sorted(s["scene_start"] for s in out if s["scene_start"] is not None)
    for s in out:
        if s["scene_start"] is None:
            continue
        later = [x for x in starts if x > s["scene_start"]]
        end = s["scene_end"] if s["scene_end"] is not None else s["scene_start"] + 8
        s["scene_end"] = min(max(end, s["scene_start"]), (later[0] - 1) if later else n_lines - 1, n_lines - 1)
    return out


def run(vid: str, model: str, stage1_model: Optional[str], force: bool) -> None:
    stage1_model = stage1_model or model
    tag = f"{model}-script-v6" + (f"-s1-{stage1_model}" if stage1_model != model else "")
    stores = load_stores()[vid]
    store, others = stores[0], stores[1:]
    cid = store["google_cid"].strip()
    path = NOTES_DIR / tag / f"{vid}__{cid}.json"
    if path.exists() and not force:
        print(f"{vid} {tag} 결과가 이미 있어 호출하지 않습니다(--force 로 다시 실행)")
        return
    data = json.loads((TRANSCRIPT_DIR / f"{vid}.json").read_text(encoding="utf-8"))
    segments = data["segments"]
    usage = {"prompt_tokens": 0, "output_tokens": 0, "calls": 0}
    header = (f"[영상 제목]\n{store['video_title']}\n\n[대상 가게]\n- {describe(store)}\n\n"
              f"[같은 영상의 다른 가게 (이 가게들의 요리는 제외)]\n" + ("\n".join(f"- {describe(o)}" for o in others) or "(없음)") + "\n\n")

    # 1단계: 요리·음료와 근거 줄 번호
    spans = clean_spans(call(stage1_model, SPAN_PROMPT, header + "자막:\n" + numbered(segments), DishList, usage)["dishes"], len(segments))
    kept = [s for s in spans if s["status"] == "eaten"]
    skipped = [{"name": s["name"], "status": s["status"]} for s in spans if s["status"] != "eaten"]

    # 2단계: 요리별 근거 구간만 보고 정리
    details = {}
    if kept:
        blocks = "\n\n".join(f"[요리 {i}] {s['name']} ({'음료' if s['kind'] == 'drink' else '음식'})\n{excerpt(segments, s)}" for i, s in enumerate(kept, 1))
        for d in call(model, DETAIL_PROMPT, header + "요리별 자막 발췌:\n\n" + blocks, DetailList, usage)["details"]:
            details[d["index"]] = {k: (blank(v) if k != "index" else v) for k, v in d.items()}

    menus, drinks = [], []
    for i, s in enumerate(kept, 1):
        d = details.get(i, {})
        line = s["scene_start"] if s["scene_start"] is not None else s["mention_line"]
        if s["kind"] == "drink":
            drinks.append({"name": s["name"], "review": d.get("taste_review") or d.get("cooking_features")})
        else:
            menus.append({"name": s["name"], "cooking_features": d.get("cooking_features"), "taste_review": d.get("taste_review"),
                          "tips": d.get("tips"), "price": d.get("price"), "evidence": d.get("evidence") or segments[line]["text"],
                          "mentioned_at": format_timestamp(segments[line]["start"]),
                          "time_basis": "served_cue" if s["scene_start"] is not None else "mention_only"})

    # 3단계: 가게 전체 정보
    dish_list = "\n".join(f"- {m['name']}: {m['taste_review'] or m['cooking_features'] or '-'}" for m in menus) or "(없음)"
    overview = call(model, OVERVIEW_PROMPT, header + f"[확인된 요리 목록]\n{dish_list}\n\n다음 시각 자막에서 [대상 가게]의 정보만 추출해줘:\n\n"
                    + "\n".join(f"[{format_timestamp(s['start'])}] {s['text']}" for s in segments), TranscriptNotes, usage)
    overview.update(menus=menus, drinks=drinks)

    result = {"video_id": vid, "video_title": store["video_title"], "google_cid": cid, "korean_name": store["korean_name"],
              "address": store["address"], "google_official_name": store["google_official_name"], "model": tag,
              "transcript_source": data.get("source"), "usage": usage, "skipped_dishes": skipped,
              "dish_spans": [{k: s[k] for k in ("name", "kind", "status", "mention_line", "scene_start", "scene_end")} for s in spans], **overview}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{vid} {tag}: 호출 {usage['calls']}회 ${usage['cost']:.4f} | 먹은 요리 {len(menus)}·음료 {len(drinks)}, 제외(추천만/없음) {len(skipped)}")
    for m in menus:
        print(f"  - {m['name']} [{m['mentioned_at']} {m['time_basis']}]\n      조리: {m['cooking_features']}\n      맛: {m['taste_review']}\n      팁: {m['tips']}")
    print("  음료:", [(d["name"], d["review"]) for d in drinks])
    print("  제외:", skipped)
    print("  key_points:", overview["key_points"])


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="시각 자막에서 3단계(요리 찾기 -> 요리별 정리 -> 가게 정보)로 추출한다.")
    ap.add_argument("--video-id", action="append", required=True, help="이 영상만 처리 (여러 번 지정 가능)")
    ap.add_argument("--model", default="gpt-4o-mini", help="2·3단계에 쓸 OpenAI 모델 (기본 gpt-4o-mini)")
    ap.add_argument("--stage1-model", default=None, help="1단계(요리·장면 찾기)에만 따로 쓸 모델. 없으면 --model 과 같다")
    ap.add_argument("--force", action="store_true", help="이미 있는 결과도 다시 호출")
    args = ap.parse_args()
    for v in args.video_id:
        run(v, args.model, args.stage1_model, args.force)
