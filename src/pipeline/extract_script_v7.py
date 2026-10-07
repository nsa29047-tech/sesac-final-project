"""
시각 자막에서 식당별 메뉴·음료를 추출한다(v7). v5(전체 호출 + 6분 구간 추출 + LLM 중복 제거)에 아래 세 가지를 더했다.

1. 구어체·감탄사 정리: 추출 프롬프트에서 정리된 평서문으로 쓰게 하고, 마지막에 정리 전용 패스로 한 번 더 다듬는다.
2. 의미 없는 평가 제거: "맛있다", "훌륭하다", "죽인다", "좋은 술이다"처럼 평가만 있는 문장은 비운다. 설명이 없으면 칸을 비워 두고,
   설명이 하나도 없는 메뉴는 embed_chunks.py 가 청크로 만들지 않는다. 정리 패스는 추출된 문장만 입력으로 받아 다듬거나 비우는 일만 하므로
   자막을 다시 읽지 않고 새 정보를 만들 여지가 적다.
3. 메뉴 이름 병기: 자막에서 그 요리를 부른 이름(예: 에스카르고)을 name 으로, 자막에 함께 나온 한국어 풀이(예: 달팽이)를 alt_name 으로 받아
   최종 이름을 "에스카르고(달팽이)"로 만든다. 풀이는 자막에 실제로 나온 것만 쓰고 모델이 지어내지 않게 한다.

결과는 data/video_notes/gpt-4o-mini-script-v7/{video_id}__{google_cid}.json 에 기존 추출 결과와 같은 형식으로 저장하므로
load_notes.py --model gpt-4o-mini-script-v7 로 그대로 적재할 수 있다. 이미 있는 결과는 다시 호출하지 않는다(--force 로 덮어씀).

실행 예:
  uv run python src/pipeline/extract_script_v7.py --video-id 3vYwR8V8IaU --video-id 6vYMBhJOneU
"""

import argparse
import json
import re
from pathlib import Path
from typing import List, Optional

from pydantic import BaseModel, Field

from compare_extraction_methods import TRANSCRIPT_PROMPT_V2
from compare_prompt_v3 import (CATEGORY_ONLY, DEDUPE_PROMPT, OLD_RULE, WINDOW_RULES, DedupeOut, absorb, call, key_of, same_item,
                               user_message, windows_of)
from extract_script_notes import PRICE_PER_M, TIME_RULES, ScriptMenu, client, fix_times, hms_to_sec, load_stores
from extract_transcript_notes import Drink, TranscriptNotes
from get_transcripts_since import OUTPUT_DIR as TRANSCRIPT_DIR, load_timed_transcript

ROOT = Path(__file__).resolve().parents[2]
TAG = "gpt-4o-mini-script-v7"
OUTPUT_DIR = ROOT / "data" / "video_notes" / TAG


class V7Menu(ScriptMenu):
    name: str = Field(
        description="자막에서 이 요리를 부를 때 실제로 쓴 이름. 외국어 요리명이면 자막의 한글 발음 표기(예: 라자냐, 파에야)를 쓰고, 한국어 일반 명칭으로 "
        "바꿔 쓰지 않는다. 파스타, 생선 같은 범주는 제외")
    alt_name: Optional[str] = Field(
        None, description="name 이 외국어 요리명(한글 발음 표기)일 때, 자막에 함께 나온 한국어 풀이나 일반 명칭(괄호 설명 포함)을 반드시 찾아 적는다. "
        "자막에 실제로 있는 것만 쓰고 일반 지식으로 지어내지 않는다. name 자체가 한국어 이름이거나 풀이가 자막에 없으면 null")


class VerifyItem(BaseModel):
    id: int = Field(description="항목 번호(사용자가 준 번호 그대로)")
    name_ok: bool = Field(description="이 이름이 실제 음식(또는 음료) 항목의 이름이고, 아래 설명들이 그 항목에 관한 것이면 true. 이름이 다른 종류(예: 음식 칸에 와인 이름)이거나 설명이 다른 요리의 것이면 false")
    cooking_supported: bool = Field(description="조리 특징이 자막에 나온 내용이면 true. 근거가 없거나 이름만 되풀이했으면 false. 입력이 null이면 true")
    taste_supported: bool = Field(description="맛 평가가 자막에 나온 내용이면 true. 자막에 없는 형용사·특징을 붙였으면 false. 입력이 null이면 true")
    tips_supported: bool = Field(description="팁이 자막에 직접 나온 내용이면 true. 입력이 null이면 true")


class VerifyOut(BaseModel):
    items: List[VerifyItem] = Field(default_factory=list)


class V7Notes(TranscriptNotes):
    menus: List[V7Menu] = Field(default_factory=list)


class WindowItems7(BaseModel):
    menus: List[V7Menu] = Field(default_factory=list)
    drinks: List[Drink] = Field(default_factory=list)


class CleanItem(BaseModel):
    id: int = Field(description="항목 번호(사용자가 준 번호 그대로)")
    cooking_features: Optional[str] = None
    taste_review: Optional[str] = None
    tips: Optional[str] = None


class CleanOut(BaseModel):
    items: List[CleanItem] = Field(default_factory=list)


NEW_RULES_V7 = """- 유튜버의 미식 표현은 핵심 어휘(바삭함, 진한 육수 등)를 살리되 정리된 서술문으로 쓴다.

[작성 방식]
- cooking_features, taste_review, tips는 구어체·감탄사·말버릇·반말·의문형("~잖아", "~죠?", "~네", "진짜", "대박")을 빼고 정리된 평서문으로 쓴다. 자막 문장을 그대로 옮기지 않는다. 원문 인용은 evidence에만 둔다.
- taste_review에는 맛의 성질·식감·향·온도처럼 구체적인 묘사만 쓴다. "맛있다", "훌륭하다", "좋다", "최고다", "죽인다", "대박"처럼 평가만 있으면 null로 둔다. 자막에 구체적인 묘사가 없으면 비워 둔다. 지어내지 않는다.
- 대상 이름이나 속성 이름 뒤에 평가어만 붙은 문장도 평가다("A가 좋다", "B가 딱 좋다", "C가 인상적이다", "계속 찾게 되는 맛이다", "처음 먹어보는 맛이다", "D가 엄청 맛있다"). 이런 문장은 null로 둔다.
- "엄청", "너무", "딱", "완전", "정말", "아주", "되게" 같은 강조 부사는 쓰지 않는다.
- cooking_features에는 재료·조리법·소스·곁들임·제공 방식(개수 단위, 뜨겁게 등)만 쓴다. 메뉴 이름을 풀어 쓴 것에 불과하면 쓰지 않고 alt_name에 둔다.
- tips는 자막에 직접 나온 먹는 방법·주문 요령만 쓰고, 없으면 null이다.
- 자동 생성 자막의 오타는 문맥과 [대상 가게]로 보정한다. 보정이 안 되는 글자는 쓰지 않는다.

[메뉴 이름]
- name에는 자막에서 그 요리를 부를 때 쓴 이름을 한글 표기로 쓴다. 외국어 요리명이면 자막의 한글 발음 표기를 우선한다. 같은 요리를 여러 이름으로 불러도 항목은 하나만 만든다.
- 자막에 한국어 풀이나 일반 명칭이 함께 나오면(예: 자막에 "파에야 (해산물 볶음밥)"이 있으면 name은 파에야, alt_name은 해산물 볶음밥) alt_name에 그 풀이를 쓴다. 자막에 없는 풀이는 일반 지식으로 채우지 않고 null로 둔다.

[메뉴와 음료]
- menus에는 요리 이름이나 재료로 구체화되는 음식만 넣고, 이 가게에서 나온 요리는 빠짐없이 넣는다. "스프", "디저트"처럼 범주만 말해서 구체적인 요리를 알 수 없으면 넣지 않는다.
- 와인, 샴페인, 사케, 위스키, 맥주, 칵테일, 차, 커피, 주스 등 마시는 것은 menus가 아니라 drinks에 넣는다. 병 이름이나 포도밭·빈티지 표기가 있어도 음료다. 음료도 빠짐없이 drinks에 넣는다.
- 요리가 아닌 설명(식당 역사, 가격 이야기)은 메뉴가 아니다."""
PROMPT_V7 = TRANSCRIPT_PROMPT_V2.replace(OLD_RULE, NEW_RULES_V7) + TIME_RULES

CLEAN_PROMPT = """너는 식당 메뉴의 설명 문장을 다듬는 편집자야. 항목마다 이름과 세 칸(조리 특징, 맛 평가, 팁)이 주어진다. 주어진 문장만 고치고, 자막이나 일반 상식에서 새로운 내용을 더하지 않는다.

규칙:
- 구어체·감탄사·말버릇·반말·의문형("~잖아", "~죠?", "~네", "진짜", "대박")을 정리된 평서문으로 고친다. 의미를 바꾸거나 입력에 없는 형용사·사실을 추가하지 않는다.
- 구체적인 정보(재료, 조리법, 소스, 곁들임, 제공 방식, 식감·향·온도·맛의 성질, 먹는 방법)가 없고 평가만 있는 칸은 null로 바꾼다. 평가만 있는 문장의 예: "맛있다", "정말 훌륭하다", "최고다", "죽인다", "대박", "미쳤다", "완벽하다", "괜찮다", "또 오고 싶다", "좋은 술이다", "인상적이다".
- 대상이나 속성 이름 뒤에 평가어만 붙은 문장도 평가만 있는 문장이다. 속성 이름(식감, 농도, 간, 양)이 들어 있어도 어떤 성질인지 알 수 없으면 null이다. 예: "농도가 딱 좋다", "식감이 인상적이다", "간이 완벽하다", "계속 찾게 되는 맛이다", "처음 먹어보는 맛이다", "속이 엄청 맛있다", "맛도 좋고 먹기도 편하다".
- "엄청", "너무", "딱", "완전", "정말", "아주", "되게" 같은 강조 부사는 지운다("너무 달콤했던 과일" -> "달콤한 과일").
- 한 칸에 평가와 구체적인 정보가 같이 있으면 구체적인 정보만 남긴다(예: "매콤하고 진짜 맛있다" -> "매콤하다.").
- 메뉴 이름을 되풀이하거나 풀어 쓴 것뿐인 칸은 null로 바꾼다(예: 이름이 "고구마 튀김"인데 조리 특징이 "고구마를 튀긴 것").
- 맛 평가 칸에 먹는 방법이나 주문 요령이 들어 있으면 그 내용은 tips로 옮기고 맛 평가에서는 뺀다. 조리 특징 칸에 맛 평가가 들어 있으면 맛 평가 칸으로 옮긴다.
- 입력이 null인 칸은 null로 둔다. 정리 후 남는 것이 없으면 모두 null이다.
- 음료 항목은 taste_review 칸만 쓴다. 시음 소감 중 구체적인 묘사(향, 숙성, 산도, 온도 등)만 남기고 평가만 있으면 null이다.

예시(다른 가게의 예시이며 이 영상의 내용이 아니다):
- 맛 평가 "와 이거 진짜 쫄깃하네" -> "쫄깃하다."
- 맛 평가 "맛있다 진짜 최고다" -> null
- 맛 평가 "소스에 찍어 먹으면 더 맛있어요" -> 맛 평가 null, tips "소스에 찍어 먹는다."
- 맛 평가 "향이 은은하고 맛있네" -> "향이 은은하다."
- 맛 평가 "속이 엄청 촉촉하다" -> "속이 촉촉하다."
- 맛 평가 "반죽 두께가 딱 좋아요" -> null
- 맛 평가 "씹는 맛이 인상적이에요" -> null
- 맛 평가 "처음 먹어보는 맛이에요" -> null
- 맛 평가 "너무 쫄깃한 면" -> "쫄깃한 면."
- 조리 특징 "해산물 수프" (이름이 "해산물 수프") -> null"""


VERIFY_PROMPT = """너는 식당 메뉴 정보가 자막에 근거가 있는지 확인하는 검수자야. 자막 전문("[HH:MM:SS] 텍스트")과 항목 목록이 주어진다.

항목마다 판단한다.
- 설명(조리 특징, 맛 평가, 팁)이 자막에 나온 내용인지 본다. 표현이 달라도 의미가 같으면 근거가 있는 것이다.
- 자막에 없는 형용사나 특징을 덧붙인 문장은 근거가 없는 것이다(예: 자막에 "맛있다"뿐인데 "진하고 깊은 맛이 난다"라고 쓴 경우, 자막에 없는 지역·유래를 붙인 경우).
- 메뉴 이름을 되풀이하거나 풀어 쓴 데 불과한 조리 특징("A를 제공", "A 요리")은 근거가 없는 것으로 본다.
- 설명이 그 항목의 것인지도 본다. 음식 칸에 와인·사케 같은 음료 이름이 들어 있거나, 설명이 다른 요리를 가리키면 name_ok를 false로 한다.
- 입력이 null인 칸은 true로 답한다. 판단이 애매하면 근거가 있는 쪽(true)으로 본다. 자막에 없다고 분명한 내용(자막에 없는 형용사, 지역·유래, 일반 상식으로 채운 문장)만 false로 한다.
- 자동 생성 자막은 오타가 많으므로 철자가 달라도 같은 말을 가리키면 근거가 있는 것이다."""

EVAL_WORD = r"(?:맛있|훌륭|좋|최고|완벽|대단|괜찮|죽인|죽여|죽이|미쳤|대박|인상적)[가-힣]*"
EVAL_NOUN = r"(?:좋은|훌륭한|맛있는|대단한|괜찮은|최고의|멋진)\s*[가-힣]{1,6}\s*(?:이다|이에요|입니다|이네요|이야)"  # "좋은 술이다" 같은 평가형
INTENSIFIER = r"(?:(?:정말|진짜|너무|아주|매우|되게|역시|엄청|딱|완전|몹시)\s*)?"
EVAL_ATTR = rf"[가-힣 ]{{1,12}}(?:이|가|은|는|도)\s*{INTENSIFIER}(?:{EVAL_WORD})"  # "농도가 딱 좋다" 같은 속성+평가어
EVAL_TASTE = r"(?:계속 찾게 되는|처음 먹어보는|처음 느껴보는|처음 접하는|잊을 수 없는|특별한|독특한)\s*맛(?:이다|이에요|입니다)"  # 단맛·짠맛 같은 구체적 맛은 건드리지 않는다
EVAL_MISC = rf"(?:{INTENSIFIER}{EVAL_WORD}\s*(?:맛|느낌|것)(?:이다|이에요|입니다)|맛이\s*(?:남다르|다르|특별하)[가-힣]*|맛있어\s*보[가-힣]*|유명한\s*[가-힣]{{1,8}}(?:이다|이에요|입니다)|다양한\s*맛이\s*조화[가-힣]*)"  # "완벽한 맛이다", "맛이 다르다" 같은 내용 없는 평가
INTENSIFIER_ANY =r"(?:정말|진짜|너무|아주|매우|되게|엄청|딱|완전|몹시)\s+"


def strip_generic(text: Optional[str]) -> Optional[str]:
    """끝에 붙은 평가어("~하고 맛있다")를 걷어내고, 평가어만 남으면 None 으로 만든다."""
    if not text:
        return None
    t = text.strip()
    t = re.sub(rf"(?:고|며|으며|면서|지만)\s*{INTENSIFIER}{EVAL_WORD}[.!?\s]*$", "다.", t)
    if re.fullmatch(rf"\s*{INTENSIFIER}(?:{EVAL_WORD}|{EVAL_NOUN}|{EVAL_ATTR}|{EVAL_TASTE}|{EVAL_MISC})[.!?\s]*", t):
        return None
    return re.sub(INTENSIFIER_ANY, "", t)


def verify_texts(merged: dict, transcript: str, usage: dict) -> dict:
    """근거 확인 패스: 자막에 근거가 없는 칸을 비우고, 이름과 설명이 어긋난 항목의 설명을 비운다."""
    items = merged["menus"] + merged["drinks"]
    n_menus = len(merged["menus"])
    lines = []
    for i, it in enumerate(items, 1):
        if i <= n_menus:
            lines.append(f"[{i}] 이름: {it['name']}\n  조리 특징: {it.get('cooking_features')}\n  맛 평가: {it.get('taste_review')}\n  팁: {it.get('tips')}")
        else:
            lines.append(f"[{i}] 이름: {it['name']} (음료)\n  맛 평가: {it.get('review')}")
    stats = {"unsupported_fields": 0, "name_mismatch": []}
    if not lines:
        return stats
    out = call(VERIFY_PROMPT, f"자막:\n{transcript}\n\n항목:\n" + "\n\n".join(lines), VerifyOut, usage)
    by_id = {v["id"]: v for v in out["items"]}
    for i, it in enumerate(items, 1):
        v = by_id.get(i)
        if v is None:
            continue
        fields = (("cooking_features", "cooking_supported"), ("taste_review", "taste_supported"), ("tips", "tips_supported")) if i <= n_menus else (("review", "taste_supported"),)
        if not v["name_ok"]:
            # 검수 모델의 이름-설명 불일치 판정은 정상 항목에도 자주 걸려서(첫 시험에서 15개 중 11개) 설명을 지우는 데는 쓰지 않고 기록만 남긴다
            stats["name_mismatch"].append(it["name"])
        for field, flag in fields:
            if it.get(field) and not v[flag]:
                it[field] = None
                stats["unsupported_fields"] += 1
    return stats


def fmt_items(items: List[dict]) -> str:
    return "\n".join(f"- {i['name']}" + (f" ({i['alt_name']})" if i.get("alt_name") else "") for i in items) or "(없음)"


def apply_dedupe(merged: dict, kind: str, cands: List[dict], decisions: List[dict], log: dict) -> None:
    """후보를 기존 목록에 합치거나(같은 요리) 범주 이름이면 버리고, 새 요리만 추가한다."""
    by_id = {d["id"]: d for d in decisions}
    for i, cand in enumerate(cands, 1):
        d = by_id.get(i) or {"same_as": None, "keep": not any(same_item(x["name"], cand["name"]) for x in merged[kind])}
        target = None
        if d["same_as"]:
            target = next((x for x in merged[kind] if x["name"] == d["same_as"]), None) or \
                     next((x for x in merged[kind] if same_item(x["name"], d["same_as"])), None)
        if target is None and kind == "menus" and hms_to_sec(cand.get("mentioned_at")) is not None:
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


def clean_texts(merged: dict, usage: dict) -> dict:
    """정리 패스: 메뉴·음료의 설명 칸을 다듬거나 비운다. 비운 칸 수 등을 반환한다."""
    items = merged["menus"] + merged["drinks"]
    lines, n_menus = [], len(merged["menus"])
    for i, it in enumerate(items, 1):
        if i <= n_menus:
            lines.append(f"[{i}] 이름: {it['name']}\n  조리 특징: {it.get('cooking_features')}\n  맛 평가: {it.get('taste_review')}\n  팁: {it.get('tips')}")
        else:
            lines.append(f"[{i}] 이름: {it['name']} (음료)\n  맛 평가: {it.get('review')}")
    stats = {"emptied": 0, "changed": 0}
    if not lines:
        return stats
    out = call(CLEAN_PROMPT, "\n\n".join(lines), CleanOut, usage)
    by_id = {c["id"]: c for c in out["items"]}
    for i, it in enumerate(items, 1):
        c = by_id.get(i)
        if c is None:
            continue
        fields = (("cooking_features", "cooking_features"), ("taste_review", "taste_review"), ("tips", "tips")) if i <= n_menus else (("review", "taste_review"),)
        for field, key in fields:
            before, after = (it.get(field) or None), (c.get(key) or None)
            if after is not None and str(after).strip().lower() in ("null", "none", "없음", "-"):
                after = None
            if before != after:
                stats["emptied" if after is None else "changed"] += 1
            it[field] = after
    return stats


class NameInfo(BaseModel):
    id: int = Field(description="메뉴 번호(사용자가 준 번호 그대로)")
    hangul_spoken: Optional[str] = Field(
        None, description="자막에서 이 요리를 가리키며 쓴 외국어 요리명의 한글 발음 표기. 자막에 그런 표기가 없거나 주어진 이름과 같은 말이면 null")


class NameOut(BaseModel):
    items: List[NameInfo] = Field(default_factory=list)


NAME_PROMPT = """너는 식당 메뉴 이름을 자막의 표기에 맞추는 편집자야. 메뉴마다 현재 이름과, 그 요리가 나오는 장면의 자막 몇 줄이 주어진다.

그 줄들에서 요리 이름으로 쓰인 표기가 현재 이름과 다른 **한글 표기**(외국어 요리명의 한글 발음 표기)이면 hangul_spoken에 줄에 적힌 그대로 쓴다.
- 줄에 괄호 설명이 붙어 있으면 괄호 안은 빼고 이름만 쓴다(예: 줄이 "(소스를 곁들인 구운 닭고기) 풀레 로티"이면 "풀레 로티").
- 줄에 실제로 적힌 글자만 쓴다. 일반 지식으로 발음을 만들어 쓰지 않는다. 줄에 이름으로 쓰인 한글 표기가 없거나 현재 이름과 같은 말이면 null이다.
- 요리 이름이 아닌 문장(감상, 설명, 대화)은 쓰지 않는다."""


def resolve_names(merged: dict, segments: List[dict], usage: dict) -> None:
    """메뉴가 나오는 장면의 자막 줄에 적힌 한글 발음 표기를 찾아 name 앞에 붙이고 기존 이름을 괄호로 병기한다(예: 에스카르고(달팽이)).
    모델이 고른 표기가 그 줄에 실제로 있는지 코드로 확인해서 지어낸 이름은 버린다."""
    blocks, excerpts = [], {}
    for i, m in enumerate(merged["menus"], 1):
        sec = hms_to_sec(m.get("mentioned_at"))
        if sec is None:
            continue
        excerpts[i] = "\n".join(s["text"] for s in segments if sec - 10 <= s["start"] <= sec + 6)
        blocks.append(f"[{i}] 현재 이름: {m['name']}\n자막:\n{excerpts[i]}")
    if not blocks:
        return
    out = call(NAME_PROMPT, "\n\n".join(blocks), NameOut, usage)
    for info in out["items"]:
        spoken = (info.get("hangul_spoken") or "").strip()
        i = info["id"]
        if i not in excerpts or not spoken or not re.search(r"[가-힣]", spoken):
            continue
        m = merged["menus"][i - 1]
        if key_of(spoken) not in key_of(excerpts[i]):  # 줄에 실제로 없는 표기는 버린다
            continue
        if key_of(spoken) != key_of(m["name"]) and key_of(spoken) not in key_of(m["name"]):
            m["spoken_name"] = spoken


NAME_STOP = {"제공", "제공됨", "사용", "함께", "곁들여", "곁들임", "곁들인", "요리", "조리", "나온다", "올린", "올라간", "들어간", "다양한", "모둠으로", "있다"}
PARTICLES = ("에서", "으로", "과", "와", "을", "를", "은", "는", "이", "가", "의", "로", "에", "도", "만")
BEVERAGE_NAME = re.compile(r"와인|샴페인|샹파뉴|사케|소주|맥주|위스키|하이볼|칵테일|막걸리|프리미어\s*크루|그랑\s*크뤼|빈티지|브랜디|코냑")


def restates_name(text: Optional[str], name: str) -> bool:
    """조리 특징이 메뉴 이름에 나온 말과 제공·사용 같은 허사만으로 이루어졌으면 True(예: 이름 '오리 푸아그라', 문장 '푸아그라를 제공')."""
    if not text:
        return False
    tokens = re.findall(r"[가-힣A-Za-z0-9]+", text)
    if not tokens:
        return False
    for tok in tokens:
        base = next((tok[: -len(p)] for p in PARTICLES if tok.endswith(p) and len(tok) > len(p) + 1), tok)
        if base in NAME_STOP or tok in NAME_STOP:
            continue
        if key_of(base) and key_of(base) in key_of(name):
            continue
        return False
    return True


def tidy_items(merged: dict) -> dict:
    """규칙 정리: 이름만 되풀이한 조리 문장은 비우고, 음식 칸에 들어간 음료(와인 등)는 음료로 옮긴다."""
    stats = {"restated_cleared": 0, "moved_to_drinks": []}
    kept = []
    for m in merged["menus"]:
        if BEVERAGE_NAME.search(m["name"]):
            merged["drinks"].append({"name": m["name"], "review": strip_generic(m.get("taste_review"))})
            stats["moved_to_drinks"].append(m["name"])
            continue
        if restates_name(m.get("cooking_features"), m["name"]):
            m["cooking_features"] = None
            stats["restated_cleared"] += 1
        kept.append(m)
    merged["menus"] = kept
    return stats


def display_name(item: dict) -> str:
    spoken = item.get("spoken_name")
    if spoken:
        return f"{spoken}({item['name']})"
    """name 에 alt_name 을 병기한다(이미 name 에 들어 있거나 같은 말이면 그대로)."""
    alt = (item.get("alt_name") or "").strip()
    if not alt or key_of(alt) in key_of(item["name"]) or key_of(item["name"]) in key_of(alt):
        return item["name"]
    return f"{item['name']}({alt})"


def run(vid: str, force: bool) -> None:
    stores = load_stores()[vid]
    store, others = stores[0], stores[1:]
    cid = store["google_cid"].strip()
    path = OUTPUT_DIR / f"{vid}__{cid}.json"
    if path.exists() and not force:
        print(f"{vid} v7 결과가 이미 있어 호출하지 않습니다(--force 로 다시 실행)")
        return
    data = json.loads((TRANSCRIPT_DIR / f"{vid}.json").read_text(encoding="utf-8"))
    segments, starts = data["segments"], [int(s["start"]) for s in data["segments"]]
    usage = {"prompt_tokens": 0, "output_tokens": 0, "calls": 0}

    merged = {"video_id": vid, "video_title": store["video_title"], "google_cid": cid, "korean_name": store["korean_name"],
              "address": store["address"], "google_official_name": store["google_official_name"], "transcript_source": data.get("source"),
              **call(PROMPT_V7, user_message(store, others, load_timed_transcript(vid)), V7Notes, usage)}
    cand_menus, cand_drinks = [], []
    for t, text in windows_of(segments):
        header = f"(이 자막은 영상의 {t // 60}분~{(t + 360) // 60}분 구간이다)\n\n"
        part = call(PROMPT_V7 + WINDOW_RULES, user_message(store, others, text, header), WindowItems7, usage)
        cand_menus += part["menus"]
        cand_drinks += part["drinks"]

    log = {"candidate_menus": len(cand_menus), "candidate_drinks": len(cand_drinks), "merged_into_existing": [], "dropped_category": []}
    if cand_menus or cand_drinks:
        user = (f"[기존 메뉴]\n{fmt_items(merged['menus'])}\n\n[후보 메뉴]\n" + ("\n".join(f"{i}. {c['name']}" + (f" ({c['alt_name']})" if c.get("alt_name") else "")
                                                                          for i, c in enumerate(cand_menus, 1)) or "(없음)")
                + f"\n\n[기존 음료]\n{fmt_items(merged['drinks'])}\n\n[후보 음료]\n" + ("\n".join(f"{i}. {c['name']}" for i, c in enumerate(cand_drinks, 1)) or "(없음)"))
        out = call(DEDUPE_PROMPT, user, DedupeOut, usage)
        apply_dedupe(merged, "menus", cand_menus, out["menus"], log)
        apply_dedupe(merged, "drinks", cand_drinks, out["drinks"], log)
    fix_times(merged, starts)

    clean_stats = clean_texts(merged, usage)
    for m in merged["menus"]:
        m["taste_review"] = strip_generic(m.get("taste_review"))
    for d in merged["drinks"]:
        d["review"] = strip_generic(d.get("review"))
    clean_stats.update(verify_texts(merged, load_timed_transcript(vid), usage))
    clean_stats.update(tidy_items(merged))
    if data.get("source") == "manual_ko":  # 자동 생성 자막은 요리 이름에도 오타가 많아 자막 표기를 이름으로 쓰지 않는다
        resolve_names(merged, segments, usage)
    for m in merged["menus"]:
        m["raw_name"] = m["name"]
        m["name"] = display_name(m)

    cost = (usage["prompt_tokens"] * PRICE_PER_M[0] + usage["output_tokens"] * PRICE_PER_M[1]) / 1e6
    merged.update(model=TAG, usage=dict(usage, cost=round(cost, 6)), dedupe_log=log, clean_stats=clean_stats)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
    blank = sum(1 for m in merged["menus"] if not (m.get("cooking_features") or m.get("taste_review") or m.get("tips")))
    print(f"{vid} v7: 호출 {usage['calls']}회 ${cost:.4f} | 메뉴 {len(merged['menus'])}(설명 없음 {blank}) 음료 {len(merged['drinks'])} | "
          f"구간 후보 합침 {len(log['merged_into_existing'])}·범주 제외 {len(log['dropped_category'])} | 정리 패스: 비운 칸 {clean_stats['emptied']}, 고친 칸 {clean_stats['changed']} | "
          f"근거 없어 비운 칸 {clean_stats['unsupported_fields']}, 이름-설명 불일치 {len(clean_stats['name_mismatch'])}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="시각 자막에서 메뉴·음료를 추출한다(v7: 구어체 정리, 의미 없는 평가 제거, 이름 병기).")
    ap.add_argument("--video-id", action="append", required=True, help="이 영상만 처리 (여러 번 지정 가능)")
    ap.add_argument("--force", action="store_true", help="이미 있는 결과도 다시 호출")
    args = ap.parse_args()
    for v in args.video_id:
        run(v, args.force)
