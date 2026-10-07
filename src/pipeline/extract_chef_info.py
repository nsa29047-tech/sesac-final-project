"""
시각 자막(data/transcripts/{video_id}.json)에서 가게별 셰프 정보(이름, 역할, 경력·특징)를 추출한다.

- 대상은 extract_script_notes.py 와 같은 가게(2025-01-01 이후 영상, google_cid 있는 가게)다. 셰프 관련 단어가 자막이나
  영상 제목에 없는 영상은 호출하지 않는다(--all 이면 전부). 가게 1곳당 gpt-4o-mini 1회.
- 그 가게의 셰프(오너·총괄·대표 셰프)만 뽑는다. 동행한 다른 셰프, 지인, 다른 식당 셰프는 제외한다.
- 자동 생성 자막은 사람 이름이 자주 깨지므로, 영상 제목·[대상 가게] 표기를 우선하고 이름마다 확신도(high/medium/low)와
  근거 문장을 받는다. low 는 DB 에 넣지 않는다(load_chefs.py). 경력·수상은 유튜버가 한 말이라 검증된 사실이 아니다.
- 결과는 data/chef_notes/{TAG}/{video_id}__{google_cid}.json 에 건별 저장하고, 이미 있는 가게는 건너뛴다(이어서 처리).
  기존 추출 결과(data/video_notes/...)는 건드리지 않는다.

실행 예:
  uv run python src/pipeline/extract_chef_info.py --dry-run
  uv run python src/pipeline/extract_chef_info.py --video-id ckIP-DO1-jI --video-id 7L_UMisgqrw    # 샘플
  uv run python src/pipeline/extract_chef_info.py                                                  # 전체(이어서 처리)
"""

import argparse
import json
import re
import time
from pathlib import Path
from typing import List, Literal, Optional

from pydantic import BaseModel, Field

from extract_script_notes import MAX_CONSECUTIVE_FAILS, PRICE_PER_M, client, describe, load_stores
from get_transcripts_since import OUTPUT_DIR as TRANSCRIPT_DIR, format_timestamp, load_timed_transcript

ROOT = Path(__file__).resolve().parents[2]
MODEL = "gpt-4o-mini"
TAG = "gpt-4o-mini-chef-v1"
OUTPUT_DIR = ROOT / "data" / "chef_notes" / TAG
CHEF_WORDS = re.compile(r"셰프|쉐프|세프|주방장|chef|Chef|CHEF")


class Chef(BaseModel):
    name: str = Field(description="셰프 이름. '셰프님' 같은 칭호는 빼고 한국어 표기로 적는다. 영상 제목·[대상 가게]에 적힌 표기를 우선한다")
    role: Literal["owner_chef", "head_chef", "chef", "pastry_chef", "other"] = Field(
        description="owner_chef: 오너 셰프·대표 / head_chef: 총괄·헤드 셰프 / chef: 이 가게 주방의 셰프 / pastry_chef: 파티시에 / other"
    )
    background: Optional[str] = Field(
        None, description="유튜버가 말한 경력·수상·방송 출연·요리 철학을 1~2문장으로. '영상에서 ~라고 소개' 형태로 쓰지 않고 내용만 적는다. 없으면 null"
    )
    evidence: str = Field(description="이 셰프가 이 가게의 셰프라고 판단한 근거 자막 원문 1~2줄(시각 [HH:MM:SS] 포함)")
    confidence: Literal["high", "medium", "low"] = Field(
        description="high: 이름이 영상 제목이나 [대상 가게]에 있거나 자막에서 분명하다 / medium: 제목에는 없지만 자막에서 2번 이상 같은 표기로 나온다 / "
        "low: 한 번만 나오거나 표기가 깨져 보이거나 이 가게의 셰프인지 불확실하다"
    )


class ChefResult(BaseModel):
    chefs: List[Chef] = Field(default_factory=list, description="이 가게의 셰프. 없으면 빈 목록")


SYSTEM_PROMPT = """너는 미식 유튜브 영상 자막(STT)에서 식당의 셰프 정보를 뽑는 분석가야. 자막은 "[HH:MM:SS] 텍스트" 형식이다.

규칙:
- 사용자가 지정한 [대상 가게]의 셰프(오너 셰프, 총괄·헤드 셰프, 대표 셰프)만 뽑는다.
- 유튜버와 함께 간 다른 셰프, 지인, 다른 식당의 셰프, 비교로 언급된 셰프, 인사말에 나오는 채널 운영자는 이 가게의 셰프가 아니므로 제외한다.
- 자막에는 음성 인식 오류가 있어 사람 이름이 자주 깨진다(예: 성만 맞고 이름이 다르게 적힘). 영상 제목과 [대상 가게] 표기가 있으면 그 표기를 우선하고,
  자막에서만 나오는 이름은 같은 표기로 여러 번 나오는지 보고 확신도를 정한다. 이름을 추측해서 만들거나 외국 셰프 이름을 일반 지식으로 보충하지 않는다.
- 경력·수상·방송 출연 등은 영상 제목이나 자막에 나온 내용만 적는다(제목의 "정식당 출신 셰프" 같은 표현도 근거다). 없으면 background는 null이다.
- 셰프 이름은 반드시 제목이나 자막에 실제로 나온 글자만 쓴다. 이름을 알 수 없으면 항목을 만들지 말고 chefs를 빈 목록으로 둔다(name에 null, 없음 같은 값을 넣지 않는다).
- 셰프 언급이 없거나 이 가게의 셰프가 누구인지 알 수 없으면 chefs를 빈 목록으로 둔다.
"""


GARBAGE_NAMES = {"null", "none", "n/a", "na", "없음", "미상", "unknown", "-", ""}
# 직함·호칭·채널 운영자는 셰프 이름이 아니다(src/db/load_chefs.py 의 GENERIC 과 같은 목록. 거기서도 한 번 더 거르지만 추출 파일에도 남기지 않는다)
GENERIC_NAMES = {"사장님", "사장", "대표님", "대표", "오너", "주방장", "선생님", "프렌치", "프렌치셰프", "요리사", "쉐프", "세프", "비밀이야", "비밀아", "비밀리아"}
GENERIC_SUFFIX = ("사장님", "대표님", "셰프님", "방탈")


def _norm(text: str) -> str:
    return re.sub(r"[\s·.,'\"()\[\]]", "", text).replace("셰프님", "").replace("셰프", "")


def assess(chefs: List[dict], title: str, segments: List[dict], source: str) -> List[dict]:
    """모델이 적은 셰프를 검증하고 확신도를 코드로 다시 계산한다. 가짜 이름·자막과 제목에 없는 이름은 버린다.
    high: 제목에 이름이 있거나(제작자 자막이면 자막에서 2번 이상) / medium: 자막에서 2번 이상(자동 자막), 제작자 자막에서 1번 / low: 그 외.
    모델의 확신도는 model_confidence 로 남기고 쓰지 않는다."""
    texts = [_norm(seg["text"]) for seg in segments]
    kept = []
    for chef in chefs:
        name = chef["name"].strip()
        key = _norm(name)
        if name.lower() in GARBAGE_NAMES or len(key) < 2 or len(key) > 12:
            continue
        if name.replace(" ", "") in GENERIC_NAMES or key in GENERIC_NAMES or name.endswith(GENERIC_SUFFIX):
            continue  # "대표님", "사장님" 같은 호칭이나 채널 운영자
        in_title = key in _norm(title)
        mentions = sum(1 for t in texts if key in t)
        if not in_title and mentions == 0:
            continue  # 제목에도 자막에도 없는 이름은 모델이 지어낸 것
        if in_title or (source == "manual_ko" and mentions >= 2):
            confidence = "high"
        elif mentions >= 2 or source == "manual_ko":
            confidence = "medium"
        else:
            confidence = "low"
        kept.append({**chef, "name": name, "model_confidence": chef["confidence"], "confidence": confidence,
                     "name_in_title": in_title, "name_mentions": mentions})
    return kept


def chefs_for_store(store: dict, others: List[dict], lines: List[dict], source: str, usage: dict) -> List[dict]:
    """식당 구간 자막(lines)에서 이 가게의 셰프를 뽑아 검증한다(extract_script_course.py 가 같은 호출에서 쓴다). 셰프 단어가 제목·자막에 없으면 호출하지 않는다.
    이름 언급 횟수는 이 구간 자막 안에서만 세므로, 식당이 여러 곳인 영상에서 다른 가게 구간의 언급이 확신도를 올리지 않는다."""
    if not (CHEF_WORDS.search(store["video_title"]) or any(CHEF_WORDS.search(l["text"]) for l in lines)):
        return []
    others_text = "\n".join(f"- {describe(o)}" for o in others) or "(없음)"
    transcript = "\n".join(f"[{format_timestamp(l['start'])}] {l['text']}" for l in lines)
    response = client.beta.chat.completions.parse(
        model=MODEL, temperature=0, seed=42, response_format=ChefResult,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": (
                f"[영상 제목]\n{store['video_title']}\n\n[대상 가게]\n- {describe(store)}\n\n"
                f"[같은 영상의 다른 가게 (이 가게들의 셰프는 제외)]\n{others_text}\n\n"
                f"다음 자막에서 [대상 가게]의 셰프 정보를 추출해줘:\n\n{transcript}")},
        ],
    )
    usage["prompt_tokens"] += response.usage.prompt_tokens
    usage["output_tokens"] += response.usage.completion_tokens
    usage["calls"] += 1
    return assess([c.model_dump() for c in response.choices[0].message.parsed.chefs], store["video_title"], lines, source)


def mentions_chef(video_id: str, title: str) -> bool:
    data = json.loads((TRANSCRIPT_DIR / f"{video_id}.json").read_text(encoding="utf-8"))
    return bool(CHEF_WORDS.search(title) or any(CHEF_WORDS.search(seg["text"]) for seg in data["segments"]))


def output_path(video_id: str, cid: str) -> Path:
    return OUTPUT_DIR / f"{video_id}__{cid}.json"


def pending_items(video_ids: List[str], include_all: bool):
    items = []
    for vid, stores in sorted(load_stores().items()):
        if video_ids and vid not in video_ids:
            continue
        if not (TRANSCRIPT_DIR / f"{vid}.json").exists():
            continue
        if not include_all and not mentions_chef(vid, stores[0]["video_title"]):
            continue
        for store in stores:
            if not output_path(vid, store["google_cid"].strip()).exists():
                items.append((vid, store, [s for s in stores if s is not store]))
    return items


def process(vid: str, store: dict, others: List[dict]) -> str:
    cid = store["google_cid"].strip()
    others_text = "\n".join(f"- {describe(o)}" for o in others) or "(없음)"
    started = time.time()
    try:
        response = client.beta.chat.completions.parse(
            model=MODEL,
            temperature=0,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": (
                        f"[영상 제목]\n{store['video_title']}\n\n"
                        f"[대상 가게]\n- {describe(store)}\n\n"
                        f"[같은 영상의 다른 가게 (이 가게들의 셰프는 제외)]\n{others_text}\n\n"
                        f"다음 자막에서 [대상 가게]의 셰프 정보를 추출해줘:\n\n{load_timed_transcript(vid)}"
                    ),
                },
            ],
            response_format=ChefResult,
        )
        result = response.choices[0].message.parsed
    except Exception as e:
        print(f"[fail] {vid} / {store['korean_name']}: {str(e)[:120]}")
        return "fail"
    transcript = json.loads((TRANSCRIPT_DIR / f"{vid}.json").read_text(encoding="utf-8"))
    source = transcript.get("source")
    chefs = assess([c.model_dump() for c in result.chefs], store["video_title"], transcript["segments"], source)
    data = {
        "video_id": vid,
        "video_title": store["video_title"],
        "google_cid": cid,
        "korean_name": store["korean_name"],
        "transcript_source": source,
        "model": TAG,
        "usage": {"prompt_tokens": response.usage.prompt_tokens, "output_tokens": response.usage.completion_tokens,
                  "seconds": round(time.time() - started, 1)},
        "chefs": chefs,
        "dropped_by_validation": len(result.chefs) - len(chefs),
    }
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    output_path(vid, cid).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    names = ", ".join(f"{c['name']}({c['confidence']})" for c in chefs) or "-"
    drop = f" (검증 탈락 {data['dropped_by_validation']})" if data["dropped_by_validation"] else ""
    print(f"[ok]   {vid} / {store['korean_name']} [{source}]: {names}{drop}")
    return "ok"


def main(video_ids: List[str], limit: Optional[int], include_all: bool, dry_run: bool) -> None:
    items = pending_items(video_ids, include_all)
    if limit:
        items = items[:limit]
    est = (len(items) * 6500 * PRICE_PER_M[0] + len(items) * 250 * PRICE_PER_M[1]) / 1e6
    print(f"처리 안 된 가게 {len(items)}곳 (저장 폴더: data/chef_notes/{TAG}), 예상 비용 약 ${est:.2f} (gpt-4o-mini, 자막 길이에 따라 +40% 정도 오차)")
    if dry_run:
        return
    fails = done = 0
    for vid, store, others in items:
        status = process(vid, store, others)
        done += status == "ok"
        fails = fails + 1 if status == "fail" else 0
        if fails >= MAX_CONSECUTIVE_FAILS:
            print(f"연속 {fails}번 실패해서 멈춥니다. 같은 명령을 다시 실행하면 이어서 처리합니다.")
            break
    print(f"완료 {done}곳")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="시각 자막에서 가게별 셰프 정보를 추출한다.")
    ap.add_argument("--video-id", action="append", default=[], help="이 영상만 처리 (여러 번 지정 가능)")
    ap.add_argument("--limit", type=int, default=None, help="이번 실행에서 처리할 가게 수")
    ap.add_argument("--all", action="store_true", help="셰프 단어가 없는 영상도 포함")
    ap.add_argument("--dry-run", action="store_true", help="API 호출 없이 대상과 예상 비용만 출력")
    args = ap.parse_args()
    main(args.video_id, args.limit, args.all, args.dry_run)
