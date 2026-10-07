"""
A/B 분리 추출 결과(extract_script_split.py)의 음식 분류(category_broad, category_detail, cuisine_tags)만 별도 호출로 다시 정한다.

- A 호출 안에서 분류하면 한국 식당이 중식으로, 스페인 타파스 바가 중식으로 잘못 분류되는 일이 있었다.
  그래서 extract_script_course.classify_cuisine(영상 제목·가게 이름·주소·국가·컨셉·키포인트·나온 메뉴를 보고 분류하는 별도 호출)을 쓴다.
- 결과 파일의 분류 3개 필드만 덮어쓰고 다른 필드는 건드리지 않는다. 이전 값은 cuisine_before 에 남기며,
  cuisine_before 가 이미 있는 파일은 건너뛴다(다시 실행해도 중복 호출 없음).
- 가게 1곳당 gpt-4o-mini 1회, 입력은 수백 토큰이라 비용은 가게당 $0.0001 안팎이다.

실행 예:
  uv run python src/pipeline/reclassify_split_cuisine.py --dry-run
  uv run python src/pipeline/reclassify_split_cuisine.py
"""

import argparse
import json
from pathlib import Path

from extract_script_course import classify_cuisine
from extract_script_notes import load_stores

ROOT = Path(__file__).resolve().parents[2]
TAG = "gpt-4o-mini-script-split-v2"
FIELDS = ("category_broad", "category_detail", "cuisine_tags")


def main(tag: str, dry_run: bool) -> None:
    stores = {(vid, s["google_cid"].strip()): s for vid, group in load_stores().items() for s in group}
    files = sorted((ROOT / "data" / "video_notes" / tag).glob("*.json"))
    todo = []
    for f in files:
        note = json.loads(f.read_text(encoding="utf-8"))
        if "cuisine_before" not in note and (note["video_id"], note["google_cid"]) in stores:
            todo.append((f, note))
    print(f"결과 {len(files)}개 중 분류를 다시 할 가게 {len(todo)}곳 (예상 비용 약 ${len(todo) * 0.0001:.3f})")
    if dry_run:
        return
    usage = {"prompt_tokens": 0, "output_tokens": 0, "calls": 0}
    changed = 0
    for f, note in todo:
        store = stores[(note["video_id"], note["google_cid"])]
        result = classify_cuisine(store, note, usage)
        before = {k: note.get(k) for k in FIELDS}
        note["cuisine_before"] = before
        note["category_broad"], note["category_detail"], note["cuisine_tags"] = result["broad"], result["detail"], result["tags"]
        f.write_text(json.dumps(note, ensure_ascii=False, indent=2), encoding="utf-8")
        if before["category_broad"] != result["broad"]:
            changed += 1
            print(f"[변경] {note['video_id']} / {note['korean_name']}: {before['category_broad']}/{before['category_detail']} -> {result['broad']}/{result['detail']}")
    print(f"완료 {len(todo)}곳, 대분류가 바뀐 가게 {changed}곳, 입력 {usage['prompt_tokens']} 토큰 / 출력 {usage['output_tokens']} 토큰")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="A/B 분리 추출 결과의 음식 분류만 다시 호출해 갱신한다.")
    ap.add_argument("--tag", default=TAG, help=f"대상 결과 폴더(data/video_notes/{{tag}}). 기본 {TAG}")
    ap.add_argument("--dry-run", action="store_true", help="API 호출 없이 대상 수만 출력")
    args = ap.parse_args()
    main(args.tag, args.dry_run)
