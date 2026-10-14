"""
restaurants_info.csv 에서 미슐랭을 아직 확인하지 못한 식당만 골라 Parse(guide.michelin.com) API로 채운다.

- Parse API는 하루 100회 제한이 있어서(무료 플랜 월 200크레딧), extract_restaurant_info.py 를 --skip-michelin 으로 돌린 뒤
  이 스크립트를 하루 한도만큼씩 여러 번 실행한다. 중단 후 재실행하면 남은 식당부터 이어서 처리한다.
- 대상: google_cid 가 있고 아래 중 하나인 행
    1) latest_grade 가 비어 있음 (미조회)
    2) note 에 '미슐랭 미조회' 또는 '미슐랭 조회 실패'(하루 한도 등으로 실패)가 있음
  google_cid 가 없는 행(Google 매칭 실패)은 DB에 적재할 수 없어서 건너뛴다.
- 같은 식당(google_cid)이 여러 영상에 나오면 API는 한 번만 호출하고 모든 행에 같은 결과를 채운다.
  다른 영상의 행에 이미 확정된 결과가 있으면 API를 호출하지 않고 그 값을 복사한다.
- 식당 1곳을 처리할 때마다 CSV에 즉시 저장한다. 하루 한도 오류가 나면 그 자리에서 멈추고 남은 건수를 알려 준다.
- 판별 방식은 extract_restaurant_info.check_michelin_status (Parse API, 이름·도시 일치) 와 같다.

사용: uv run python src/pipeline/fill_michelin.py [--csv data/restaurants_info.csv] [--max-calls 90] [--only-notes 태그]
  --only-notes: data/video_notes/<태그>/ 의 JSON 에 있는 식당(google_cid)만 처리한다(챗봇에 먼저 필요한 식당만 우선 채울 때).
환경변수(.env): MICHELIN_GUIDE_API_KEY
"""

import argparse
import csv
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parent))
import extract_restaurant_info as base  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CSV = ROOT / "data" / "restaurants_info.csv"
DEFAULT_MAX_CALLS = 90   # 하루 100회 제한보다 조금 낮게

# 미슐랭 관련 실패·미조회 표시. 채우면 note 에서 지운다.
_PENDING_NOTE_RE = re.compile(r"미슐랭 미조회;\s*|미슐랭 조회 실패:[^;]*;\s*")
_CAP_HINTS = ("Daily request cap", "호출 한도")


def needs_check(row: Dict[str, str]) -> bool:
    if not row["korean_name"].strip() or not row["google_cid"].strip():
        return False
    note = row["note"]
    return (not row["latest_grade"].strip()) or "미슐랭 미조회" in note or "미슐랭 조회 실패" in note


def save_csv(path: Path, fieldnames: List[str], rows: List[Dict[str, str]]) -> None:
    """임시 파일에 쓴 뒤 교체한다(쓰는 도중 중단돼도 원본이 깨지지 않게)."""
    fd, tmp = tempfile.mkstemp(suffix=".csv", dir=path.parent)
    with os.fdopen(fd, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    os.replace(tmp, path)


def main(csv_path: Path, max_calls: int, only_notes: str = "") -> None:
    with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames
        rows = list(reader)

    by_cid: Dict[str, List[Dict[str, str]]] = {}
    known: Dict[str, Dict[str, str]] = {}   # 이미 확정된 결과가 있는 식당(google_cid -> 그 행)
    for r in rows:
        if needs_check(r):
            by_cid.setdefault(r["google_cid"].strip(), []).append(r)
        elif r["korean_name"].strip() and r["google_cid"].strip():
            known.setdefault(r["google_cid"].strip(), r)

    copied = 0
    for cid in [c for c in by_cid if c in known]:
        for r in by_cid.pop(cid):
            for col in ("is_michelin", "latest_grade", "source_urls"):
                r[col] = known[cid][col]
            r["note"] = _PENDING_NOTE_RE.sub("", r["note"])
            copied += 1
    if copied:
        save_csv(csv_path, fieldnames, rows)
        print(f"다른 영상에서 이미 확정된 식당 {copied}행은 API 호출 없이 복사했습니다")

    if only_notes:
        import json
        files = (ROOT / "data" / "video_notes" / only_notes).glob("*.json")
        wanted = {str(json.loads(f.read_text(encoding="utf-8")).get("google_cid") or "").strip() for f in files}
        by_cid = {c: g for c, g in by_cid.items() if c in wanted}
        print(f"--only-notes {only_notes}: 해당 결과의 식당 {len(wanted)}곳으로 대상을 좁혔습니다")

    print(f"미슐랭 확인이 필요한 식당 {len(by_cid)}곳 (행 {sum(len(v) for v in by_cid.values())}개). 이번 실행 최대 {max_calls}회 호출")
    base.MICHELIN_MAX_CALLS = max_calls

    done = failed = 0
    stopped_by_cap = False
    for cid, group in by_cid.items():
        rep = group[0]
        try:
            info = base.check_michelin_status(
                official_name=rep["google_official_name"] or rep["korean_name"],
                korean_name=rep["korean_name"],
                address=rep["google_formatted_address"] or rep["address"],
                country_code=rep["country_code"] or "KR",
            )
        except Exception as e:
            if any(h in str(e) for h in _CAP_HINTS):
                print(f"⚠️ 호출 한도 도달, 여기서 멈춥니다: {str(e)[:120]}")
                stopped_by_cap = True
                break
            failed += 1
            print(f"⚠️ {rep['korean_name']}: 조회 실패(이번엔 건너뜀, 다음 실행에서 재시도): {str(e)[:120]}")
            continue

        for r in group:
            base.apply_michelin(r, info)
            r["note"] = _PENDING_NOTE_RE.sub("", r["note"])
        done += 1
        save_csv(csv_path, fieldnames, rows)
        print(f"[{done}] {rep['korean_name']} / {rep['google_official_name']}: {info.latest_grade.value}")

    remaining = sum(1 for g in by_cid.values() if any(needs_check(r) for r in g))
    print(f"완료 {done}곳, 실패 {failed}곳. 아직 확인 못 한 식당 {remaining}곳"
          + (" (하루 한도에 도달함, 내일 다시 실행)" if stopped_by_cap or remaining else ""))

    try:
        import pandas as pd
        pd.read_csv(csv_path, encoding="utf-8-sig", dtype={"google_cid": str}).to_excel(csv_path.with_suffix(".xlsx"), index=False)
    except Exception as e:  # 엑셀에서 열려 있으면 실패할 수 있다. CSV 가 원본이므로 무시한다.
        print(f"xlsx 갱신은 건너뜀: {e}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="미슐랭을 아직 확인하지 못한 식당만 Parse API로 채운다.")
    ap.add_argument("--csv", type=Path, default=DEFAULT_CSV)
    ap.add_argument("--max-calls", type=int, default=DEFAULT_MAX_CALLS, help="이번 실행에서 Parse API를 호출할 최대 횟수")
    ap.add_argument("--only-notes", default="", help="data/video_notes/<태그>/ 에 결과가 있는 식당만 처리")
    args = ap.parse_args()
    main(args.csv, args.max_calls, args.only_notes)
