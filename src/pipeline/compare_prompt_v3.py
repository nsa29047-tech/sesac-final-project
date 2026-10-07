"""
자막 기반 추출의 프롬프트 개선 효과를 영상 단위로 비교한다. (v2: DB에 적재된 현재 결과 / v3 / v3w)

- v2  : extract_script_notes.py 의 현재 결과(data/video_notes/gpt-4o-mini-script-v2). 새로 호출하지 않고 읽기만 한다.
- v4  : v3 에서 확인된 문제(지어낸 팁, 음료 누락, 와인이 음식으로 분류, 범주 이름 메뉴)를 고친 프롬프트. v4w 는 v4 + 구간 분할.
- v3  : v2 프롬프트에서 "미식 표현을 원래 표현대로"를 없애고, 구어체를 정리된 서술문으로 쓰게 하며, 메뉴를 빠짐없이 뽑으라는 규칙을 더했다.
- v3w : v3 결과에 구간 분할 추출을 더한다. 자막을 6분 구간(30초 겹침)으로 나눠 구간마다 메뉴·음료만 다시 뽑고, v3에 없는 것만 합친다.
        긴 자막을 한 번에 넣으면 모델이 눈에 띄는 요리만 고르는 문제(누락)를 줄이려는 것이다.

결과는 data/video_notes/gpt-4o-mini-script-v3(w)/{video_id}__{google_cid}.json 에 v2 와 같은 형식으로 저장하므로
효과가 좋으면 load_notes.py --model gpt-4o-mini-script-v3w 로 그대로 적재할 수 있다. 이미 있는 결과는 다시 호출하지 않는다(--force 로 덮어씀).
비교 리포트는 data/compare/prompt_v3_{video_id}.md 에 쓴다.

실행 예:
  uv run python src/pipeline/compare_prompt_v3.py --video-id 6vYMBhJOneU
  uv run python src/pipeline/compare_prompt_v3.py --video-id 6vYMBhJOneU --report-only
"""

import argparse
import json
import re
import time
from pathlib import Path
from typing import Dict, List, Optional

from pydantic import BaseModel, Field

from compare_extraction_methods import TRANSCRIPT_PROMPT_V2
from extract_script_notes import MODEL, PRICE_PER_M, TIME_RULES, ScriptMenu, ScriptNotes, client, describe, fix_times, hms_to_sec, load_stores
from extract_transcript_notes import Drink
from get_transcripts_since import OUTPUT_DIR as TRANSCRIPT_DIR, load_timed_transcript

ROOT = Path(__file__).resolve().parents[2]
NOTES_DIR = ROOT / "data" / "video_notes"
COMPARE_DIR = ROOT / "data" / "compare"
TAGS = {"v2": "gpt-4o-mini-script-v2", "v3": "gpt-4o-mini-script-v3", "v3w": "gpt-4o-mini-script-v3w",
        "v4": "gpt-4o-mini-script-v4", "v4w": "gpt-4o-mini-script-v4w", "v5": "gpt-4o-mini-script-v5"}
WINDOW_SEC, OVERLAP_SEC = 360, 30

OLD_RULE = "- 유튜버의 미식 표현(바삭함, 진한 육수 등)은 최대한 원래 표현을 살려 적는다."
NEW_RULES = """- 유튜버의 미식 표현은 핵심 어휘(바삭함, 진한 육수 등)를 살리되 정리된 서술문으로 쓴다.

[작성 방식]
- cooking_features, taste_review, tips는 구어체·감탄사·말버릇("진짜", "~더라고", "대박")을 빼고 명사형이나 평서문으로 정리해 쓴다. 자막 문장을 그대로 옮기지 않는다. 원문 인용은 evidence에만 둔다.
- "맛있다"처럼 이유가 없는 한마디는 taste_review에 넣지 않고 null로 둔다. 유튜버가 이유(식감, 향, 조합 등)를 말했으면 그 이유를 포함해 쓴다.
- 자동 생성 자막의 오타는 문맥과 [대상 가게]로 보정한다. 보정이 안 되는 글자는 쓰지 않는다.
- tips에는 그 메뉴에만 해당하는 먹는 방법·추천 팁만 쓴다. 식당이나 문화에 대한 일반 설명은 key_points에 쓴다.

[누락 방지]
- 이 가게에서 실제로 나오거나 언급된 요리를 빠짐없이 menus에 넣는다. 코스라면 아뮤즈부슈, 전채, 수프, 생선, 육류, 치즈, 디저트, 프티푸르까지 각각 따로 넣는다.
- 요리가 아닌 설명(식당 역사, 가격 이야기)은 메뉴가 아니다."""
assert OLD_RULE in TRANSCRIPT_PROMPT_V2
PROMPT_V3 = TRANSCRIPT_PROMPT_V2.replace(OLD_RULE, NEW_RULES) + TIME_RULES

# v4: v3 에서 확인된 문제(지어낸 팁, 음료 누락, 와인이 음식으로 분류, "스프"·"디저트" 같은 범주 이름, 맛 칸에 먹는 법)를 고친다.
NEW_RULES_V4 = """- 유튜버의 미식 표현은 핵심 어휘(바삭함, 진한 육수 등)를 살리되 정리된 서술문으로 쓴다.

[작성 방식]
- cooking_features, taste_review는 구어체·감탄사·말버릇("진짜", "~더라고", "대박")을 빼고 명사형이나 평서문으로 정리해 쓴다. 자막 문장을 그대로 옮기지 않는다. 원문 인용은 evidence에만 둔다.
- taste_review에는 맛·식감·향·온도에 대한 평가만 쓴다. 먹는 방법, 곁들이기 권유, 눈으로 본 인상("맛있어 보인다")은 넣지 않는다. 이유 없는 "맛있다" 한마디는 null로 둔다.
- tips는 유튜버가 그 메뉴를 먹는 방법이나 주문 요령을 자막에서 직접 말한 경우에만 쓴다. 자막에 없으면 반드시 null이다. "함께 먹으면 좋다", "즐기기" 같은 일반적인 문장을 만들어 채우지 않는다.
- 자동 생성 자막의 오타는 문맥과 [대상 가게]로 보정한다. 보정이 안 되는 글자는 쓰지 않는다.

[메뉴와 음료]
- menus에는 요리 이름이나 재료로 구체화되는 음식만 넣고, 이 가게에서 나온 요리는 빠짐없이 넣는다. 코스라면 아뮤즈부슈, 전채, 수프, 생선, 육류, 치즈, 디저트, 프티푸르를 각각 따로 넣되, "스프", "디저트"처럼 범주만 말해서 구체적인 요리를 알 수 없으면 메뉴로 넣지 않는다.
- 와인, 샴페인, 사케, 위스키, 맥주, 칵테일, 차, 커피, 주스 등 마시는 것은 menus가 아니라 drinks에 넣는다. 병 이름이나 포도밭·빈티지 표기(예: 프리미어 크루)가 있어도 음료다. 음료도 빠짐없이 drinks에 넣는다.
- 요리가 아닌 설명(식당 역사, 가격 이야기)은 메뉴가 아니다."""
PROMPT_V4 = TRANSCRIPT_PROMPT_V2.replace(OLD_RULE, NEW_RULES_V4) + TIME_RULES
PROMPTS = {"v3": PROMPT_V3, "v4": PROMPT_V4}
WINDOW_RULES = """
[구간 추출]
- 지금 주어진 자막은 영상 전체가 아니라 일부 구간이다. 이 구간에 나오는 [대상 가게]의 메뉴와 음료만 menus, drinks에 담는다. 이 구간에 없으면 빈 목록이다.
- 이름이 불분명한 요리는 만들지 말고 제외한다. 시각은 이 구간 자막의 [HH:MM:SS]를 그대로 쓴다.
"""


class WindowItems(BaseModel):
    menus: List[ScriptMenu] = Field(default_factory=list)
    drinks: List[Drink] = Field(default_factory=list)


def user_message(store: dict, others: List[dict], transcript: str, header: str = "") -> str:
    others_text = "\n".join(f"- {describe(o)}" for o in others) or "(없음)"
    return (f"[영상 제목]\n{store['video_title']}\n\n[대상 가게]\n- {describe(store)}\n\n"
            f"[같은 영상의 다른 가게 (추출 대상 아님, 정보를 섞지 말 것)]\n{others_text}\n\n{header}"
            f"다음 시각 자막에서 [대상 가게]의 정보만 추출해줘:\n\n{transcript}")


def call(system: str, user: str, schema, usage: dict):
    r = client.beta.chat.completions.parse(model=MODEL, temperature=0.1, response_format=schema,
                                           messages=[{"role": "system", "content": system}, {"role": "user", "content": user}])
    usage["prompt_tokens"] += r.usage.prompt_tokens
    usage["output_tokens"] += r.usage.completion_tokens
    usage["calls"] += 1
    return r.choices[0].message.parsed.model_dump()


def windows_of(segments: List[dict]):
    end = segments[-1]["start"]
    t = 0
    while t <= end:
        lines = [f"[{int(s['start']) // 3600:02d}:{int(s['start']) % 3600 // 60:02d}:{int(s['start']) % 60:02d}] {s['text']}"
                 for s in segments if t <= s["start"] < t + WINDOW_SEC]
        if lines:
            yield t, "\n".join(lines)
        t += WINDOW_SEC - OVERLAP_SEC


def key_of(name: str) -> str:
    return re.sub(r"[^0-9A-Za-z가-힣]", "", name)


def same_item(a: str, b: str) -> bool:
    ka, kb = key_of(a), key_of(b)
    return bool(ka) and bool(kb) and (ka == kb or (min(len(ka), len(kb)) >= 3 and (ka in kb or kb in ka)))


def merge(base: dict, extra: List[dict], kind: str) -> int:
    """base[kind]에 없는 항목만 extra 에서 추가한다. 이미 있으면 base 의 빈 필드만 채운다. 새로 추가한 개수를 반환."""
    added = 0
    for item in extra:
        match = next((b for b in base[kind] if same_item(b["name"], item["name"])), None)
        if match is None:
            base[kind].append({**item, "from_window": True})
            added += 1
        else:
            for k, v in item.items():
                if match.get(k) in (None, "", "none") and v not in (None, "", "none"):
                    match[k] = v
    return added


class Decision(BaseModel):
    id: int = Field(description="후보 번호(사용자가 준 번호 그대로)")
    same_as: Optional[str] = Field(
        None, description="이 후보가 [기존 목록]의 항목이나 번호가 더 앞선 다른 후보와 같은 요리·음료이면 그 이름을 목록에 적힌 그대로 쓴다. 다른 것이면 null"
    )
    keep: bool = Field(description="새 항목으로 남기려면 true. 같은 것이거나 구체적인 이름이 없는 범주(스프, 디저트, 전채 등)면 false")


class DedupeOut(BaseModel):
    menus: List[Decision] = Field(default_factory=list)
    drinks: List[Decision] = Field(default_factory=list)


DEDUPE_PROMPT = """너는 식당 메뉴 목록을 정리하는 분석가야. [기존 메뉴]/[기존 음료]와 [후보 메뉴]/[후보 음료]가 주어진다.
후보마다 다음을 판단해 id 순서대로 모두 답한다.
- 같은 요리·음료인지: 한국어와 외국어 표기 차이(소시송 = Saucisson, 달팽이 = Escargot), 철자·띄어쓰기·음성 인식 오류로 생긴 차이, 설명의 상세도 차이는 같은 것이다.
  같으면 same_as에 같은 항목의 이름을 [기존 목록]이나 앞선 후보에 적힌 그대로 쓰고 keep은 false로 한다.
- 구체적인 이름이 없는 범주(스프, 수프, 디저트, 전채, 메인, 빵, 와인, 샴페인처럼 종류만 말한 것)는 새 항목이 아니다. 같은 종류의 구체적인 항목이 있으면 same_as로 연결하고, 없으면 keep은 false로 한다.
- 위 두 경우가 아니면 서로 다른 요리·음료이므로 same_as는 null, keep은 true로 한다.
- 이름이 비슷해도 맛이나 구성이 다른 별개의 요리(예: 푸아그라와 랍스터, 랍스터)는 같은 것으로 합치지 않는다."""


# 요리·음료의 구체적인 이름이 아니라 범주만 적힌 것. LLM 판단과 별개로 규칙으로 한 번 더 거른다(르 퀸시 v5 에서 "전채·메인·디저트"가 남았다).
CATEGORY_ONLY = {"전채", "메인", "디저트", "스프", "수프", "빵", "치즈", "애피타이저", "에피타이저", "앙트레", "샐러드", "프티푸르",
                 "커피", "차", "와인", "샴페인", "주류", "음료", "식전주", "코스", "아뮤즈부슈"}


def listing(title: str, items: List[dict]) -> str:
    return f"[{title}]\n" + ("\n".join(f"- {i['name']}" for i in items) or "(없음)")


def absorb(target: dict, item: dict) -> None:
    """같은 항목으로 판단된 후보의 값으로 target 의 빈 필드만 채운다."""
    for key, value in item.items():
        if key in ("name", "from_window"):
            continue
        if target.get(key) in (None, "", "none") and value not in (None, "", "none"):
            target[key] = value


def run_v5(vid: str, force: bool) -> None:
    """v4 프롬프트 + 구간 분할 + LLM 중복 제거. 최종 병합 결과 1개만 data/video_notes/gpt-4o-mini-script-v5 에 저장한다."""
    stores = load_stores()[vid]
    store, others = stores[0], stores[1:]
    cid = store["google_cid"].strip()
    path = NOTES_DIR / TAGS["v5"] / f"{vid}__{cid}.json"
    if path.exists() and not force:
        print(f"{vid} v5 결과가 이미 있어 호출하지 않습니다(--force 로 다시 실행)")
        return
    data = json.loads((TRANSCRIPT_DIR / f"{vid}.json").read_text(encoding="utf-8"))
    segments, starts = data["segments"], [int(s["start"]) for s in data["segments"]]
    usage = {"prompt_tokens": 0, "output_tokens": 0, "calls": 0}

    v4_path = NOTES_DIR / TAGS["v4"] / f"{vid}__{cid}.json"  # v4 전체 호출 결과가 있으면 그대로 쓰고 구간 추출분만 더한다
    if v4_path.exists():
        merged = json.loads(v4_path.read_text(encoding="utf-8"))
        merged["usage"] = dict(merged.get("usage", {"prompt_tokens": 0, "output_tokens": 0}), calls=1)
        usage.update(prompt_tokens=merged["usage"]["prompt_tokens"], output_tokens=merged["usage"]["output_tokens"], calls=1)
        merged = {k: v for k, v in merged.items() if k not in ("windows_added_menus", "windows_added_drinks")}
        merged["menus"] = [m for m in merged["menus"] if not m.get("from_window")]
    else:
        merged = {"video_id": vid, "video_title": store["video_title"], "google_cid": cid, "korean_name": store["korean_name"],
                  "address": store["address"], "google_official_name": store["google_official_name"], "transcript_source": data.get("source"),
                  **call(PROMPT_V4, user_message(store, others, load_timed_transcript(vid)), ScriptNotes, usage)}
    merged.setdefault("drinks", [])

    cand_menus, cand_drinks = [], []
    for t, text in windows_of(segments):
        header = f"(이 자막은 영상의 {t // 60}분~{(t + WINDOW_SEC) // 60}분 구간이다)\n\n"
        part = call(PROMPT_V4 + WINDOW_RULES, user_message(store, others, text, header), WindowItems, usage)
        cand_menus += part["menus"]
        cand_drinks += part["drinks"]

    log = {"candidate_menus": len(cand_menus), "candidate_drinks": len(cand_drinks), "merged_into_existing": [], "dropped_category": []}
    if cand_menus or cand_drinks:
        cand_text = "\n".join(f"{i + 1}. {c['name']}" for i, c in enumerate(cand_menus)) or "(없음)"
        cand_drink_text = "\n".join(f"{i + 1}. {c['name']}" for i, c in enumerate(cand_drinks)) or "(없음)"
        user = (f"{listing('기존 메뉴', merged['menus'])}\n\n[후보 메뉴]\n{cand_text}\n\n"
                f"{listing('기존 음료', merged['drinks'])}\n\n[후보 음료]\n{cand_drink_text}")
        out = call(DEDUPE_PROMPT, user, DedupeOut, usage)
        for kind, cands, decisions in (("menus", cand_menus, out["menus"]), ("drinks", cand_drinks, out["drinks"])):
            by_id = {d["id"]: d for d in decisions}
            for i, cand in enumerate(cands, 1):
                d = by_id.get(i)
                if d is None:  # 판단이 빠지면 안전하게 중복으로 보지 않고 규칙(철자 일치)만 적용
                    d = {"same_as": None, "keep": not any(same_item(x["name"], cand["name"]) for x in merged[kind])}
                target = None
                if d["same_as"]:
                    target = next((x for x in merged[kind] if x["name"] == d["same_as"]), None) or \
                             next((x for x in merged[kind] if same_item(x["name"], d["same_as"])), None)
                if target is None and kind == "menus" and hms_to_sec(cand.get("mentioned_at")) is not None:
                    # 같은 자막 줄(같은 시각)에서 잡힌 메뉴는 같은 요리의 다른 이름이다(소시송/Saucisson, 비건 만두/구운 만두)
                    target = next((x for x in merged["menus"] if hms_to_sec(x.get("mentioned_at")) == hms_to_sec(cand["mentioned_at"])), None)
                if target is None and key_of(cand["name"]) in CATEGORY_ONLY:
                    log["dropped_category"].append(cand["name"])
                elif target is not None:
                    absorb(target, cand)
                    log["merged_into_existing"].append(f"{cand['name']} -> {target['name']}")
                elif d["keep"] and not any(same_item(x["name"], cand["name"]) for x in merged[kind]):
                    merged[kind].append({**cand, "from_window": True})
                else:
                    log["dropped_category"].append(cand["name"])
    fix_times(merged, starts)
    merged.update(model=TAGS["v5"], usage=dict(usage), dedupe_log=log,
                  windows_added_menus=sum(1 for m in merged["menus"] if m.get("from_window")),
                  windows_added_drinks=sum(1 for m in merged["drinks"] if m.get("from_window")))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
    cost = (usage["prompt_tokens"] * PRICE_PER_M[0] + usage["output_tokens"] * PRICE_PER_M[1]) / 1e6
    print(f"{vid} v5: 호출 {usage['calls']}회 ${cost:.4f} | 후보 메뉴 {len(cand_menus)}·음료 {len(cand_drinks)} -> 새로 추가 메뉴 {merged['windows_added_menus']}·음료 {merged['windows_added_drinks']}, "
          f"기존과 합침 {len(log['merged_into_existing'])}, 범주라서 제외 {len(log['dropped_category'])}")


def run(vid: str, force: bool, versions: List[str]) -> None:
    for version in versions:
        if version == "v5":
            run_v5(vid, force)
        else:
            run_version(vid, force, version)


def run_version(vid: str, force: bool, version: str) -> None:
    prompt, tag, tag_w = PROMPTS[version], version, version + "w"
    store = load_stores()[vid][0]
    others = [s for s in load_stores()[vid] if s is not store]
    cid = store["google_cid"].strip()
    paths = {v: NOTES_DIR / TAGS[v] / f"{vid}__{cid}.json" for v in (tag, tag_w)}
    if all(p.exists() for p in paths.values()) and not force:
        print(f"{vid} {tag}, {tag_w} 결과가 이미 있어 호출하지 않습니다(--force 로 다시 실행)")
        return
    data = json.loads((TRANSCRIPT_DIR / f"{vid}.json").read_text(encoding="utf-8"))
    segments = data["segments"]
    starts = [int(s["start"]) for s in segments]
    base = {"video_id": vid, "video_title": store["video_title"], "google_cid": cid, "korean_name": store["korean_name"],
            "address": store["address"], "google_official_name": store["google_official_name"], "transcript_source": data.get("source")}

    usage = {"prompt_tokens": 0, "output_tokens": 0, "calls": 0}
    notes = call(prompt, user_message(store, others, load_timed_transcript(vid)), ScriptNotes, usage)
    v3_usage = dict(usage)
    fix_times(notes, starts)
    whole = {**base, "model": TAGS[tag], "usage": v3_usage, **notes}

    w_notes = json.loads(json.dumps(notes))  # v3 결과 복사 후 구간 추출분을 합친다
    added_menus = added_drinks = 0
    for t, text in windows_of(segments):
        header = f"(이 자막은 영상의 {t // 60}분~{(t + WINDOW_SEC) // 60}분 구간이다)\n\n"
        part = call(prompt + WINDOW_RULES, user_message(store, others, text, header), WindowItems, usage)
        added_menus += merge(w_notes, part["menus"], "menus")
        added_drinks += merge(w_notes, part["drinks"], "drinks")
    fix_times(w_notes, starts)
    windowed = {**base, "model": TAGS[tag_w], "usage": dict(usage), "windows_added_menus": added_menus, "windows_added_drinks": added_drinks, **w_notes}

    for key, payload in ((tag, whole), (tag_w, windowed)):
        paths[key].parent.mkdir(parents=True, exist_ok=True)
        paths[key].write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    cost = lambda u: (u["prompt_tokens"] * PRICE_PER_M[0] + u["output_tokens"] * PRICE_PER_M[1]) / 1e6
    print(f"{vid} {tag}: 호출 1회 ${cost(v3_usage):.4f} / {tag_w}: 호출 {usage['calls']}회(구간 {usage['calls'] - 1}) 누적 ${cost(usage):.4f}, 구간에서 추가된 메뉴 {added_menus}개, 음료 {added_drinks}개")


def norm(text: str) -> str:
    return re.sub(r"[^0-9A-Za-z가-힣]", "", text or "")


def metrics(note: dict, transcript_norm: str) -> Dict[str, object]:
    menus = note["menus"]
    def stat(field, min_len):
        vals = [m[field] for m in menus if m.get(field)]
        verb = sum(1 for v in vals if len(norm(v)) >= min_len and norm(v) in transcript_norm)
        return len(vals), verb
    taste, taste_v = stat("taste_review", 6)
    cook, cook_v = stat("cooking_features", 8)
    tips, tips_v = stat("tips", 8)
    return {"음식 메뉴 수": len(menus), "음료 수": len(note.get("drinks", [])), "맛 표현 있음": taste, "  자막 그대로 일치": taste_v,
            "조리 특징 있음": cook, "  자막 그대로 일치 ": cook_v, "팁 있음": tips, "  자막 그대로 일치  ": tips_v,
            "맛 표현 평균 글자": round(sum(len(m["taste_review"]) for m in menus if m.get("taste_review")) / max(taste, 1), 1)}


def report(vid: str) -> None:
    store = load_stores()[vid][0]
    cid = store["google_cid"].strip()
    notes = {}
    for v in TAGS:
        path = NOTES_DIR / TAGS[v] / f"{vid}__{cid}.json"
        if path.exists():
            notes[v] = json.loads(path.read_text(encoding="utf-8"))
    versions = list(notes)
    transcript = json.loads((TRANSCRIPT_DIR / f"{vid}.json").read_text(encoding="utf-8"))
    tn = norm(" ".join(s["text"] for s in transcript["segments"]))
    ms = {v: metrics(n, tn) for v, n in notes.items()}
    labels = {"v2": "v2 (현재)", "v3": "v3", "v3w": "v3w", "v4": "v4", "v4w": "v4w (v4+구간)", "v5": "v5 (v4w+중복 제거)"}
    lines = [f"# 프롬프트 비교: {store['korean_name']} (`{vid}`, 자막 {transcript['source']}, {len(transcript['segments'])}구간)", "",
             "| 항목 | " + " | ".join(labels[v] for v in versions) + " |", "|" + "---|" * (len(versions) + 1)]
    for k in ms["v2"]:
        lines.append(f"| {k.strip()} | " + " | ".join(str(ms[v][k]) for v in versions) + " |")
    for v in versions:
        title = labels[v]
        lines += ["", f"## {title}", "", "| 메뉴 | 조리 특징 | 맛 표현 | 팁 | 시각 |", "|---|---|---|---|---|"]
        for m in notes[v]["menus"]:
            sec = m.get("mentioned_at") or "-"
            mark = " (구간)" if m.get("from_window") else ""
            lines.append(f"| {m['name']}{mark} | {m.get('cooking_features') or '-'} | {m.get('taste_review') or '-'} | {m.get('tips') or '-'} | {sec} |")
        lines += ["", "음료: " + (", ".join(f"{d['name']}({d.get('review') or '-'})" for d in notes[v].get("drinks", [])) or "(없음)")]
    out = COMPARE_DIR / f"prompt_v3_{vid}.md"
    COMPARE_DIR.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"리포트 저장: {out}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="자막 추출 프롬프트 v2 / v3 / v3w 를 영상 단위로 비교한다.")
    ap.add_argument("--video-id", required=True)
    ap.add_argument("--versions", nargs="+", choices=list(PROMPTS) + ["v5"], default=list(PROMPTS), help="호출할 버전 (기본 v3 v4, v5는 v4 결과를 재사용해 구간 추출+중복 제거)")
    ap.add_argument("--force", action="store_true", help="이미 있는 v3, v3w 결과도 다시 호출")
    ap.add_argument("--report-only", action="store_true", help="호출 없이 저장된 결과로 리포트만 만든다")
    args = ap.parse_args()
    if not args.report_only:
        run(args.video_id, args.force, args.versions)
    report(args.video_id)
