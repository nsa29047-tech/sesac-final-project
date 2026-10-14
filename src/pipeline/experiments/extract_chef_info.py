"""
(보류: 셰프 추출은 이제 extract_script_split.py 가 추출 단계에서 함께 한다. 이 파일은 data/chef_notes 에 따로 뽑던 예전 일괄 방식이다. 정의는 common.py 에 있다)

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

import sys
from pathlib import Path as _P

sys.path.insert(0, str(_P(__file__).resolve().parents[1]))  # experiments/ 밖의 pipeline 모듈(get_transcripts_since 등)을 import 하기 위해

import argparse
import json
import re
import time
from pathlib import Path
from typing import List, Literal, Optional

from pydantic import BaseModel, Field

from common import (CHEF_PROMPT as SYSTEM_PROMPT, CHEF_WORDS, MAX_CONSECUTIVE_FAILS, PRICE_PER_M, ChefResult, assess, chefs_for_store,
                    client, describe, load_stores)
from get_transcripts_since import OUTPUT_DIR as TRANSCRIPT_DIR, format_timestamp, load_timed_transcript

ROOT = Path(__file__).resolve().parents[3]
MODEL = "gpt-4o-mini"
TAG = "gpt-4o-mini-chef-v1"
OUTPUT_DIR = ROOT / "data" / "chef_notes" / TAG
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
