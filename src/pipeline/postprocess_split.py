"""
A/B 분리 추출 결과(extract_script_split.py)를 후처리한다. 추출 호출은 다시 하지 않는다.

고치는 것
1. 설명(cooking_features, 음료 review)에 남은 평가어·가격 문장
   - 규칙: 재료·조리 단서 없이 평가만 있는 문장("맛이 좋다", "맛있어 보인다")은 지운다.
   - LLM(gpt-4o-mini, 가게당 1회, 평가어가 남은 항목이 있는 가게만): 구체적인 내용과 평가가 섞인 문장에서 평가·가격 부분만 빼고 다시 쓴다.
     새 내용을 만들지 않고, 남길 내용이 없으면 null 로 둔다.
2. 코스 단계: 코스 가게의 요리 단계를 코드로 보정한다.
   - 이름에 '치즈'가 있는 요리(케이크·타르트·무스·크림 제외)는 '치즈'
   - '메인'인데 이름이 해산물이고 뒤에 고기 요리가 나오면 '생선'
   - 단계는 나온 순서대로 줄어들지 않게 한다(앞 요리보다 앞 단계가 되면 앞 요리의 단계로 맞춘다)
3. 음료가 메뉴로 들어간 것: 이름이 음료 목록과 같은 메뉴를 메뉴에서 뺀다.
4. 셰프: 이름이 식당 이름과 같거나 식당 이름에 들어 있으면 뺀다(식당 이름이 셰프 이름으로 들어간 경우).

원본은 data/video_notes/{TAG}_raw 에 한 번만 복사해 두고, 항상 원본에서 읽어 {TAG} 에 쓴다. 그래서 다시 실행해도 결과가 같다(LLM 호출은 매번 한다).
결과 파일의 postprocess 키에 무엇을 고쳤는지 남긴다.

실행 예:
  uv run python src/pipeline/postprocess_split.py --dry-run     # 규칙만 적용해 건수 출력(API 호출 없음)
  uv run python src/pipeline/postprocess_split.py
"""

import argparse
import json
import re
import shutil
from pathlib import Path
from typing import List, Optional

from pydantic import BaseModel, Field

from compare_prompt_v3 import key_of
from extract_script_notes import MODEL, client

ROOT = Path(__file__).resolve().parents[2]
TAG = "gpt-4o-mini-script-split-v2"
STAGES = ["아뮤즈부쉬", "식전빵", "전채", "수프", "생선", "메인", "치즈", "프리디저트", "디저트", "프티푸르"]
RANK = {s: i for i, s in enumerate(STAGES)}

# 평가어·가격 표현. DROP 은 문장을 지울지 판단하는 데, FLAG 는 LLM 정리 대상을 고르는 데 쓴다.
EVAL = (r"맛이\s*(?:좋|뛰어나|훌륭|일품|최고|환상)|맛도\s*좋|맛있|훌륭|만족스럽|환상|완벽|최고|평가를\s*받|돋보|인상적|뛰어나|"
        r"고급스러운\s*[가-힣 ]{0,8}(?:특징|조합)|맛에\s*충실|가격|저렴|비싸|가성비")
EVAL_RE = re.compile(EVAL)
FLAG_RE = re.compile(EVAL + r"|추천|좋[고은다]|괜찮")
# 재료·조리·형태 단서가 있으면 평가가 섞여 있어도 문장을 통째로 지우지 않는다(LLM 이 평가 부분만 뺀다)
CONCRETE_RE = re.compile(r"구워|구운|튀[기김겨]|볶|끓|찌[고어]|찐|삶|곁들|올려|얹|넣|만들|사용|소스|재료|숙성|제공|함께|베이스|식감|향|바삭|부드러|쫄깃|촉촉|"
                         r"짭짤|달콤|상큼|매콤|고소|진한|비교|같은|들어가|뿌려|섞|\d")
SEAFOOD = ("랑구스틴", "랍스터", "가리비", "굴", "오징어", "대구", "터보", "광어", "연어", "참치", "새우", "생선", "방어", "도미", "농어", "문어",
           "조개", "해산물", "전복", "성게", "아귀", "고등어")
MEAT = ("소고기", "한우", "램", "양고기", "비둘기", "오리", "돼지", "닭", "스테이크", "안심", "등심", "갈비", "사슴", "송아지", "꿩", "소 ")
CHEESE_EXCLUDE = ("케이크", "타르트", "무스", "크림", "아이스크림")


def sentences_of(text: str) -> List[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s.strip()]


def drop_eval_sentences(text: Optional[str]) -> Optional[str]:
    """재료·조리 단서 없이 평가만 있는 문장을 지운다. 다 지워지면 None."""
    if not text:
        return None
    kept = [s for s in sentences_of(text) if not (EVAL_RE.search(s) and not CONCRETE_RE.search(s))]
    return " ".join(kept) or None


class Rewritten(BaseModel):
    id: int = Field(description="항목 번호(사용자가 준 번호 그대로)")
    text: Optional[str] = Field(None, description="평가·가격을 뺀 설명. 구체적인 내용이 남지 않으면 null")


class RewriteOut(BaseModel):
    items: List[Rewritten] = Field(default_factory=list)


REWRITE_PROMPT = """너는 식당 메뉴·음료 설명을 다듬는 편집자야. 항목마다 이름과 설명이 주어진다.
- 설명에서 평가와 가격 이야기만 빼고, 구체적인 정보(재료, 조리법, 소스, 제공 방식, 맛·식감·향의 구체적인 묘사, 다른 음식에 빗댄 말, 먹는 방법, 산지·품종·숙성 같은 사실)는 그대로 남긴다.
- 뺄 것: "맛이 좋다", "맛있다", "훌륭하다", "뛰어나다", "만족스럽다", "~라는 평가를 받는다", "가격 대비 ~", "가성비", 가격 언급.
- 설명에 없는 내용을 새로 만들거나 바꾸지 않는다. 문장은 자연스러운 정리된 평서문으로 이어 붙인다. 평가를 뺀 뒤 남는 구체적인 내용이 없으면 text는 null이다.
- 모든 항목을 id와 함께 돌려준다."""


def rewrite_items(items: List[dict], field: str, usage: dict) -> int:
    """평가어가 남은 항목만 LLM 으로 다시 쓴다. 바뀐 항목 수를 돌려준다."""
    targets = [it for it in items if it.get(field) and FLAG_RE.search(it[field])]
    if not targets:
        return 0
    shown = "\n\n".join(f"[{i}] 이름: {it['name']}\n설명: {it[field]}" for i, it in enumerate(targets, 1))
    r = client.beta.chat.completions.parse(
        model=MODEL, temperature=0, seed=42, response_format=RewriteOut,
        messages=[{"role": "system", "content": REWRITE_PROMPT}, {"role": "user", "content": shown}])
    usage["prompt_tokens"] += r.usage.prompt_tokens
    usage["output_tokens"] += r.usage.completion_tokens
    usage["calls"] += 1
    by_id = {x.id: x.text for x in r.choices[0].message.parsed.items}
    changed = 0
    for i, it in enumerate(targets, 1):
        if i not in by_id:
            continue
        new = (by_id[i] or "").strip() or None
        if new and new.lower() in ("null", "none", "없음", "-"):
            new = None
        if new != it[field]:
            it[field] = new
            changed += 1
    return changed


def fix_stages(note: dict) -> dict:
    """코스 가게의 단계를 코드로 보정한다. 코스가 아니면 그대로 둔다."""
    log = {"cheese": 0, "fish": 0, "monotone": 0}
    if not note.get("course"):
        return log
    menus = sorted(note["menus"], key=lambda m: m["order"])
    for i, m in enumerate(menus):
        name = m["name"]
        if m.get("course_stage") is None:
            continue
        if "치즈" in name and not any(x in name for x in CHEESE_EXCLUDE) and m["course_stage"] != "치즈":
            m["course_stage"], log["cheese"] = "치즈", log["cheese"] + 1
        elif (m["course_stage"] == "메인" and any(x in name for x in SEAFOOD) and not any(x in name for x in MEAT)
              and any(any(x in later["name"] for x in MEAT) for later in menus[i + 1:])):
            m["course_stage"], log["fish"] = "생선", log["fish"] + 1
    top = -1
    for m in menus:
        if m.get("course_stage") is None:
            continue
        rank = RANK[m["course_stage"]]
        if rank < top:
            m["course_stage"], log["monotone"] = STAGES[top], log["monotone"] + 1
        else:
            top = rank
    return log


def drop_drink_menus(note: dict) -> List[str]:
    drinks = {key_of(d["name"]) for d in note["drinks"]}
    dropped = [m["name"] for m in note["menus"] if key_of(m["name"]) in drinks]
    note["menus"] = [m for m in note["menus"] if m["name"] not in dropped]
    for rank, m in enumerate(sorted(note["menus"], key=lambda m: m["order"]), 1):
        m["order"] = rank
    return dropped


def drop_restaurant_name_chefs(note: dict) -> List[str]:
    names = [key_of(note.get("korean_name") or ""), key_of(note.get("google_official_name") or "")]
    dropped = []
    for chef in note.get("chefs", []):
        key = key_of(chef["name"])
        if key and any(n and (key == n or key in n) for n in names):
            dropped.append(chef["name"])
    note["chefs"] = [c for c in note.get("chefs", []) if c["name"] not in dropped]
    return dropped


def process(note: dict, usage: Optional[dict]) -> dict:
    """한 가게의 결과를 후처리한다. usage 가 None 이면 LLM 을 호출하지 않는다."""
    before = sum(1 for m in note["menus"] if m.get("cooking_features") and FLAG_RE.search(m["cooking_features"]))
    sentences = 0
    for it, field in [(m, "cooking_features") for m in note["menus"]] + [(d, "review") for d in note["drinks"]]:
        old = it.get(field)
        it[field] = drop_eval_sentences(old)
        sentences += old != it[field]
    rewritten = 0
    if usage is not None:
        rewritten = rewrite_items(note["menus"], "cooking_features", usage) + rewrite_items(note["drinks"], "review", usage)
        for it, field in [(m, "cooking_features") for m in note["menus"]] + [(d, "review") for d in note["drinks"]]:
            it[field] = drop_eval_sentences(it.get(field))  # LLM 이 다시 쓴 문장에 평가만 남은 경우
    note["postprocess"] = {
        "flagged_before": before,
        "sentence_rule_changed": sentences,
        "llm_rewritten": rewritten,
        "stage_fixes": fix_stages(note),
        "drink_menus_dropped": drop_drink_menus(note),
        "chefs_dropped_restaurant_name": drop_restaurant_name_chefs(note),
    }
    return note


def main(tag: str, dry_run: bool) -> None:
    out_dir, raw_dir = ROOT / "data" / "video_notes" / tag, ROOT / "data" / "video_notes" / f"{tag}_raw"
    if not raw_dir.exists():
        shutil.copytree(out_dir, raw_dir)
        print(f"원본 백업: {raw_dir}")
    usage = None if dry_run else {"prompt_tokens": 0, "output_tokens": 0, "calls": 0}
    totals = {"flagged_before": 0, "sentence_rule_changed": 0, "llm_rewritten": 0, "cheese": 0, "fish": 0, "monotone": 0}
    dropped_menus, dropped_chefs, remaining = [], [], 0
    files = sorted(raw_dir.glob("*.json"))
    for f in files:
        note = process(json.loads(f.read_text(encoding="utf-8")), usage)
        pp = note["postprocess"]
        for k in ("flagged_before", "sentence_rule_changed", "llm_rewritten"):
            totals[k] += pp[k]
        for k in ("cheese", "fish", "monotone"):
            totals[k] += pp["stage_fixes"][k]
        dropped_menus += [(note["korean_name"], n) for n in pp["drink_menus_dropped"]]
        dropped_chefs += [(note["korean_name"], n) for n in pp["chefs_dropped_restaurant_name"]]
        remaining += sum(1 for m in note["menus"] if m.get("cooking_features") and FLAG_RE.search(m["cooking_features"]))
        if not dry_run:
            (out_dir / f.name).write_text(json.dumps(note, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{len(files)}곳 처리{' (dry-run: 규칙만, 파일은 쓰지 않음)' if dry_run else ''}")
    print(f"평가어 표현이 있던 메뉴 {totals['flagged_before']}개 -> 처리 후 {remaining}개 (규칙으로 고친 항목 {totals['sentence_rule_changed']}, LLM 으로 다시 쓴 항목 {totals['llm_rewritten']})")
    print(f"코스 단계 보정: 치즈 {totals['cheese']}, 생선 {totals['fish']}, 순서 맞춤 {totals['monotone']}")
    print(f"음료라서 메뉴에서 뺀 항목: {dropped_menus}")
    print(f"식당 이름과 같아서 뺀 셰프: {dropped_chefs}")
    if usage:
        print(f"LLM 호출 {usage['calls']}회, 입력 {usage['prompt_tokens']} / 출력 {usage['output_tokens']} 토큰 "
              f"(약 ${(usage['prompt_tokens'] * 0.15 + usage['output_tokens'] * 0.6) / 1e6:.4f})")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="A/B 분리 추출 결과를 후처리한다(평가어, 코스 단계, 음료 중복, 셰프).")
    ap.add_argument("--tag", default=TAG, help=f"대상 결과 폴더. 기본 {TAG}")
    ap.add_argument("--dry-run", action="store_true", help="LLM 을 부르지 않고 규칙만 적용해 건수를 본다(파일은 쓰지 않음)")
    args = ap.parse_args()
    main(args.tag, args.dry_run)
