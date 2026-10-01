"""
restaurants_info.csv 의 식당(google_place_id 기준)마다 Places Details(New)로 addressComponents 를 받아
시와 그 다음 단계 지역(한국 기준 서울특별시 > 마포구, 성남시 > 분당구)을 뽑아 restaurant_regions.csv 에 건별로 저장한다.
DB 적재는 load_regions.py 에서 한다(추후).

- 지역은 최대 2단계다. 그 다음 단계(구/군 등)가 없으면(예: 안동시) 시만 저장한다. 도로명 주소의 도로명은 쓰지 않는다.
- "OO역/OO 근처" 질문은 지역 컬럼이 아니라 식당의 위경도(restaurants.latitude/longitude)로 반경 검색한다.
- 건별로 CSV에 기록하고, 재실행하면 이미 기록된 google_place_id 는 건너뛴다. API 오류가 난 건은 기록하지 않아 다음 실행에서 재시도된다.
- 지역명은 languageCode=ko 로 요청한다. 한국어 이름이 없는 해외 지역은 현지 표기로 온다.
- addressComponents 원본(JSON)도 같이 남겨서, 매핑 규칙을 고쳐도 API를 다시 호출하지 않고 --rebuild-from 으로 재계산할 수 있다.

호출 수: 식당당 Details 1회.
사용(샘플 먼저): uv run python src/pipeline/enrich_regions.py --limit 10 --output data/restaurant_regions_sample.csv
원본에서 재계산: uv run python src/pipeline/enrich_regions.py --rebuild-from data/old.csv --output data/new.csv

환경변수(.env): GOOGLE_PLACES_API_KEY
"""

import argparse
import csv
import json
import os
import random
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests
from dotenv import load_dotenv

load_dotenv()
GOOGLE_PLACES_API_KEY = os.getenv("GOOGLE_PLACES_API_KEY")

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "data"
INPUT_FILE = DATA_DIR / "restaurants_info.csv"
OUTPUT_FILE = DATA_DIR / "restaurant_regions.csv"

LANGUAGE_CODE = "ko"
CALL_DELAY = (0.2, 0.5)

FIELDNAMES = ["google_cid", "google_place_id", "country_code", "region_1", "region_2", "address_components"]


# -------------------------------------------------------------
# 1. 지역: addressComponents -> 시 > 다음 단계
# -------------------------------------------------------------
# 시 다음 단계 후보. 도로명(sublocality_level_3 이하)은 쓰지 않는다.
SECOND_LEVEL_TYPES = ["sublocality_level_1", "sublocality_level_2", "neighborhood"]


def _component_name(components: List[Dict[str, Any]], comp_type: str) -> Optional[str]:
    for c in components:
        if comp_type in c.get("types", []):
            return c.get("longText") or c.get("shortText")
    return None


def extract_region_path(components: List[Dict[str, Any]]) -> List[str]:
    """
    addressComponents 에서 [시, 다음 단계] 를 뽑는다. 다음 단계가 없으면 [시] 만 돌려준다.
      - 시: locality(성남시) -> postal_town -> administrative_area_level_1(서울특별시)
      - 다음 단계: sublocality_level_1(구) -> level_2(동) -> neighborhood 중 먼저 있는 것
    나라마다 단계 구조가 달라서 일본 도쿄처럼 locality 가 구(区) 단위인 곳은 시 자리에 구가 들어간다.
    그런 경우는 address_components 원본으로 규칙을 고쳐서 재계산한다.
    """
    city = (_component_name(components, "locality")
            or _component_name(components, "postal_town")
            or _component_name(components, "administrative_area_level_1"))
    path = [city] if city else []
    for t in SECOND_LEVEL_TYPES:
        name = _component_name(components, t)
        if name and name not in path:
            path.append(name)
            break
    return path


def fetch_address_components(place_id: str) -> Optional[List[Dict[str, Any]]]:
    url = f"https://places.googleapis.com/v1/places/{place_id}"
    headers = {"X-Goog-Api-Key": GOOGLE_PLACES_API_KEY, "X-Goog-FieldMask": "addressComponents"}
    resp = requests.get(url, headers=headers, params={"languageCode": LANGUAGE_CODE}, timeout=10)
    if resp.status_code != 200:
        print(f"    ⚠️ Details 응답 오류 ({resp.status_code}): {resp.text[:200]}")
        return None
    return resp.json().get("addressComponents", [])


def build_row(cid: str, place_id: str, country_code: str, components: List[Dict[str, Any]]) -> Dict[str, Any]:
    path = extract_region_path(components) + ["", ""]
    return {
        "google_cid": cid, "google_place_id": place_id, "country_code": country_code,
        "region_1": path[0], "region_2": path[1],
        "address_components": json.dumps(components, ensure_ascii=False),
    }


# -------------------------------------------------------------
# 2. 배치 실행
# -------------------------------------------------------------
def load_targets(input_file: Path) -> List[Dict[str, str]]:
    """영상 x 식당 행을 google_place_id 기준으로 중복 제거한다."""
    targets, seen = [], set()
    with open(input_file, "r", encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            pid = (row.get("google_place_id") or "").strip()
            if not pid or pid in seen:
                continue
            seen.add(pid)
            targets.append(row)
    return targets


def rebuild(source: Path, output_file: Path) -> None:
    """저장된 address_components 로 지역 컬럼만 다시 계산한다(API 호출 없음)."""
    with open(source, "r", encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    with open(output_file, "w", encoding="utf-8-sig", newline="") as out_f:
        writer = csv.DictWriter(out_f, fieldnames=FIELDNAMES)
        writer.writeheader()
        for r in rows:
            writer.writerow(build_row(r["google_cid"], r["google_place_id"], r["country_code"],
                                      json.loads(r["address_components"])))
    print(f"{len(rows)}건 재계산 완료 (API 호출 없음). 결과: {output_file}")


def main(input_file: Path, output_file: Path, limit: Optional[int]):
    if not GOOGLE_PLACES_API_KEY:
        print("오류: .env 에 GOOGLE_PLACES_API_KEY 가 필요합니다.")
        return
    if not input_file.exists():
        print(f"오류: {input_file} 파일이 없습니다.")
        return

    targets = load_targets(input_file)
    done = set()
    resume = output_file.exists() and output_file.stat().st_size > 0
    if resume:
        with open(output_file, "r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            if reader.fieldnames != FIELDNAMES:
                print(f"오류: {output_file} 의 컬럼이 현재 스키마와 다릅니다. 다른 --output 을 쓰거나 파일을 옮겨 주세요.")
                return
            done = {r["google_place_id"] for r in reader}

    todo = [t for t in targets if t["google_place_id"].strip() not in done]
    skipped = len(targets) - len(todo)
    if limit:
        todo = todo[:limit]
    print(f"식당 {len(targets)}곳 중 이미 처리된 {skipped}곳은 건너뛰고 {len(todo)}곳을 처리합니다. (예상 API 호출: Details {len(todo)}회)")

    failed = 0
    with open(output_file, "a" if resume else "w", encoding="utf-8-sig", newline="") as out_f:
        writer = csv.DictWriter(out_f, fieldnames=FIELDNAMES)
        if not resume:
            writer.writeheader()
        for i, row in enumerate(todo, start=1):
            pid = row["google_place_id"].strip()
            try:
                components = fetch_address_components(pid)
            except Exception as e:
                print(f"[{i}/{len(todo)}] ⚠️ 예기치 못한 오류 ({row.get('korean_name')}): {e}")
                components = None
            time.sleep(random.uniform(*CALL_DELAY))
            if components is None:
                failed += 1
                continue
            result = build_row(row.get("google_cid", ""), pid, row.get("country_code", ""), components)
            writer.writerow(result)
            out_f.flush()
            print(f"[{i}/{len(todo)}] {row.get('korean_name')}: "
                  f"{' > '.join(p for p in (result['region_1'], result['region_2']) if p) or '(지역 없음)'}")
    print(f"완료. 실패 {failed}건(기록하지 않음, 재실행 시 재시도). 결과: {output_file}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", type=Path, default=INPUT_FILE)
    ap.add_argument("--output", type=Path, default=OUTPUT_FILE)
    ap.add_argument("--limit", type=int, default=None, help="이번 실행에서 처리할 식당 수(샘플 검증용)")
    ap.add_argument("--rebuild-from", type=Path, default=None,
                    help="address_components 가 들어 있는 기존 결과 CSV. API 호출 없이 지역 컬럼만 다시 계산해 --output 에 쓴다")
    args = ap.parse_args()
    if args.rebuild_from:
        rebuild(args.rebuild_from, args.output)
    else:
        main(args.input, args.output, args.limit)
