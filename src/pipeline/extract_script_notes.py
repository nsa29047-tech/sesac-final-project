"""
시각이 붙은 자막(data/transcripts/{video_id}.json)에서 식당별 메뉴·분위기·분류·태그와 메뉴별 등장 시각을 추출한다. (자막 방식 테스트용)

- 대상은 data/urls/transcript_targets_20250101.txt 의 영상 중 restaurants_info.csv 에 google_cid 가 있는 가게다.
  가게 정보(상호·주소)는 영상 소개란에서 추출해 둔 CSV를 그대로 쓰므로 Places/지역/미슐랭 호출은 필요 없다.
- 가게 1곳당 gpt-4o-mini 1회 호출. 자막 전문과 [대상 가게]를 주고 그 가게의 정보만 뽑는다.
- 메뉴마다 mentioned_at(자막 시각)을 받는다. 서빙·시식 단서("나왔어요", "먹어볼게요")가 있는 자막을 우선하고,
  단서가 없으면 처음 언급된 시각을 쓰며 time_basis 에 구분을 남긴다. 화면에서 음식이 보이는 시각이 아니라 추정치다.
  모델이 적은 시각이 실제 자막 시각과 맞는지 검증해, 자막에 없는 시각은 null 로 버린다(환각 방지).
- 결과는 data/video_notes/{TAG}/{video_id}__{google_cid}.json 에 건별 저장한다. 영상 분석 결과와 같은 키라서
  src/db/load_notes.py --model {TAG} 로 수정 없이 적재할 수 있다. 이미 있는 가게는 건너뛰므로 중단 후 재실행하면 이어서 처리된다.

실행 예:
  uv run python src/pipeline/extract_script_notes.py --dry-run
  uv run python src/pipeline/extract_script_notes.py --limit 4          # 처리 안 된 가게 4곳만
  uv run python src/pipeline/extract_script_notes.py                    # 전체(이어서 처리)
"""

import argparse
import csv
import json
import os
import re
import time
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Literal, Optional

from dotenv import load_dotenv
from openai import OpenAI
from pydantic import Field

from compare_extraction_methods import TRANSCRIPT_PROMPT_V2
from extract_transcript_notes import Menu, TranscriptNotes
from get_transcripts_since import OUTPUT_DIR as TRANSCRIPT_DIR, load_timed_transcript

load_dotenv()

ROOT = Path(__file__).resolve().parents[2]
STORES_CSV = ROOT / "data" / "restaurants_info.csv"
# 대상 영상 ID 목록. 2024년 영상까지 넓힐 때는 환경변수 TARGETS_FILE=data/urls/transcript_targets_20240101.txt 로 바꾼다.
TARGETS_FILE = Path(os.getenv("TARGETS_FILE") or ROOT / "data" / "urls" / "transcript_targets_20250101.txt")
MODEL = "gpt-4o-mini"
TAG = "gpt-4o-mini-script-v2"  # data/video_notes 아래 폴더명이자 DB video_restaurant_notes.model 값
OUTPUT_DIR = ROOT / "data" / "video_notes" / TAG
PRICE_PER_M = (0.15, 0.60)  # (입력, 출력) USD / 1M 토큰
SNAP_TOLERANCE_SEC = 5  # 모델이 적은 시각이 실제 자막 시각과 이만큼 이내로 어긋나면 가장 가까운 자막 시각으로 맞춘다

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))


class ScriptMenu(Menu):
    mentioned_at: Optional[str] = Field(
        None,
        description="이 음식이 실제로 나오거나 먹는 장면으로 판단되는 자막의 시각. 자막 줄 앞의 [HH:MM:SS]를 그대로 복사한다. 근거가 없으면 null",
    )
    time_basis: Literal["served_cue", "mention_only", "none"] = Field(
        "none",
        description="served_cue: '나왔어요', '먹어볼게요' 같은 서빙·시식 단서가 있는 자막의 시각 / mention_only: 단서 없이 처음 언급된 시각 / none: 시각 없음",
    )


class ScriptNotes(TranscriptNotes):
    menus: List[ScriptMenu] = Field(default_factory=list)


TIME_RULES = """
[메뉴 시각]
- 자막은 "[HH:MM:SS] 텍스트" 형식이다. 메뉴마다 mentioned_at에 그 음식이 실제로 나오거나 먹는 장면으로 판단되는 자막의 시각을 적는다.
- "나왔습니다", "나왔어요", "먹어볼게요", "한입", "서빙" 같은 서빙·시식 단서가 있는 자막의 시각을 우선하고 time_basis를 served_cue로 한다.
- 주문하면서 이름만 말하는 구간, 영상 초반에 오늘 먹을 메뉴를 요약하는 구간, 다른 식당과 비교하는 언급은 음식이 나온 시점이 아니다. 피한다.
- 서빙·시식 단서가 없고 이름 언급만 있으면 처음 언급된 자막의 시각을 적고 time_basis를 mention_only로 한다.
- 시각은 자막에 실제로 있는 [HH:MM:SS]를 그대로 복사한다. 계산하거나 지어내지 않는다. 근거가 없으면 mentioned_at은 null, time_basis는 none이다.
- 메뉴의 evidence에는 근거 자막 원문만 적고 시각은 붙이지 않는다.
"""
SYSTEM_PROMPT = TRANSCRIPT_PROMPT_V2 + TIME_RULES


def video_id_of(url: str) -> str:
    return re.search(r"v=([0-9A-Za-z_-]{11})", url).group(1)


def load_stores() -> Dict[str, List[dict]]:
    """대상 영상별 가게 목록(google_cid 있는 가게만, 영상 내 중복 cid 제거)."""
    targets = {l.strip() for l in TARGETS_FILE.read_text(encoding="utf-8").splitlines() if l.strip()}
    by_video: Dict[str, List[dict]] = defaultdict(list)
    seen = set()
    with open(STORES_CSV, "r", encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            vid = video_id_of(row["video_url"])
            cid = row["google_cid"].strip()
            if vid in targets and row["korean_name"].strip() and cid and (vid, cid) not in seen:
                seen.add((vid, cid))
                by_video[vid].append(row)
    return by_video


def describe(store: dict) -> str:
    return " / ".join(p for p in (store["korean_name"], store["address"]) if p)


def hms_to_sec(text: Optional[str]) -> Optional[int]:
    m = re.fullmatch(r"\s*\[?(\d{1,2}):(\d{2}):(\d{2})\]?\s*", text or "")
    return int(m[1]) * 3600 + int(m[2]) * 60 + int(m[3]) if m else None


def fix_times(notes: dict, starts: List[int]) -> int:
    """모델이 적은 mentioned_at 을 실제 자막 시각에 맞춘다. 맞출 수 없으면 null. 반환값은 버려진 시각 수."""
    dropped = 0
    for menu in notes["menus"]:
        if menu.get("mentioned_at") is None:
            menu["time_basis"] = "none"
            continue
        sec = hms_to_sec(menu["mentioned_at"])
        nearest = min(starts, key=lambda s: abs(s - sec)) if sec is not None and starts else None
        if nearest is None or abs(nearest - sec) > SNAP_TOLERANCE_SEC:
            menu["mentioned_at"], menu["time_basis"] = None, "none"
            dropped += 1
        else:
            menu["mentioned_at"] = f"{nearest // 3600:02d}:{nearest % 3600 // 60:02d}:{nearest % 60:02d}"
    return dropped


def extract(vid: str, store: dict, others: List[dict], transcript: str):
    others_text = "\n".join(f"- {describe(o)}" for o in others) or "(없음)"
    return client.beta.chat.completions.parse(
        model=MODEL,
        temperature=0,
        seed=42,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    f"[대상 가게]\n- {describe(store)}\n\n"
                    f"[같은 영상의 다른 가게 (추출 대상 아님, 정보를 섞지 말 것)]\n{others_text}\n\n"
                    f"다음 시각 자막에서 [대상 가게]의 정보만 추출해줘:\n\n{transcript}"
                ),
            },
        ],
        response_format=ScriptNotes,
    )


def output_path(vid: str, cid: str) -> Path:
    return OUTPUT_DIR / f"{vid}__{cid}.json"


def process(vid: str, store: dict, others: List[dict]) -> str:
    cid = store["google_cid"].strip()
    started = time.time()
    transcript_data = json.loads((TRANSCRIPT_DIR / f"{vid}.json").read_text(encoding="utf-8"))
    starts = [int(seg["start"]) for seg in transcript_data["segments"]]
    try:
        response = extract(vid, store, others, load_timed_transcript(vid))
        notes = response.choices[0].message.parsed.model_dump()
    except Exception as e:
        print(f"[fail] {vid} / {store['korean_name']}: {str(e)[:120]}")
        return "fail"
    dropped = fix_times(notes, starts)
    usage = {"prompt_tokens": response.usage.prompt_tokens, "output_tokens": response.usage.completion_tokens,
             "seconds": round(time.time() - started, 1), "stores_in_call": 1}
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
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    output_path(vid, cid).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    timed = sum(1 for m in notes["menus"] if m["mentioned_at"])
    print(f"[ok]   {vid} / {store['korean_name']}: 메뉴 {len(notes['menus'])}개 (시각 {timed}개, 검증 탈락 {dropped}개), "
          f"{notes['category_broad']}/{notes['category_detail']}")
    return "ok"


def pending_items(by_video: Dict[str, List[dict]], video_ids: List[str]):
    items = []
    for vid, stores in sorted(by_video.items()):
        if video_ids and vid not in video_ids:
            continue
        if not (TRANSCRIPT_DIR / f"{vid}.json").exists():
            print(f"[skip] {vid}: 시각 자막(.json) 없음")
            continue
        for store in stores:
            if not output_path(vid, store["google_cid"].strip()).exists():
                items.append((vid, store, [s for s in stores if s is not store]))
    return items


MAX_CONSECUTIVE_FAILS = 3


def main(video_ids: List[str], limit: Optional[int], dry_run: bool) -> None:
    by_video = load_stores()
    total_stores = sum(len(s) for s in by_video.values())
    items = pending_items(by_video, video_ids)
    print(f"대상 영상 {len(by_video)}개, 가게 {total_stores}곳 중 처리 안 된 가게 {len(items)}곳 (저장 폴더: data/video_notes/{TAG})")
    if limit:
        items = items[:limit]
    est_in = len(items) * 6000  # 자막 평균 약 5천~6천 토큰으로 가정한 대략값
    print(f"이번 실행: {len(items)}곳, 예상 비용 약 ${(est_in * PRICE_PER_M[0] + len(items) * 700 * PRICE_PER_M[1]) / 1e6:.2f} (gpt-4o-mini)")
    if dry_run:
        return
    fails, done = 0, 0
    for vid, store, others in items:
        status = process(vid, store, others)
        done += status == "ok"
        fails = fails + 1 if status == "fail" else 0
        if fails >= MAX_CONSECUTIVE_FAILS:
            print(f"연속 {fails}번 실패해서 멈춥니다. 같은 명령을 다시 실행하면 이어서 처리합니다.")
            break
    print(f"완료 {done}곳")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="시각 자막에서 식당별 메뉴·분위기·태그와 메뉴 시각을 추출한다.")
    ap.add_argument("--video-id", action="append", default=[], help="이 영상만 처리 (여러 번 지정 가능)")
    ap.add_argument("--limit", type=int, default=None, help="이번 실행에서 처리할(아직 처리 안 된) 가게 수")
    ap.add_argument("--dry-run", action="store_true", help="API 호출 없이 대상과 예상 비용만 출력")
    ap.add_argument("--tag", default=None, help="결과 저장 폴더(data/video_notes/{tag})와 결과의 model 값. 기본은 DB 적재에 쓴 gpt-4o-mini-script-v2. "
                    "같은 프롬프트로 처음부터 다시 뽑아 비교할 때 기존 결과를 덮어쓰지 않으려고 쓴다")
    args = ap.parse_args()
    if args.tag:
        TAG = args.tag
        OUTPUT_DIR = ROOT / "data" / "video_notes" / TAG
    main(args.video_id, args.limit, args.dry_run)
