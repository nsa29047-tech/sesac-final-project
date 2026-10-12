"""
Places Details(New)를 식당당 1회만 불러 지역(addressComponents)과 가격대(priceLevel/priceRange)를 함께 채운다.
예전 enrich_regions.py(지역)와 fetch_price_level.py(가격대)를 합친 스크립트다. 결과 파일 형식은 그대로라서
load_regions.py, load_prices.py 는 바꾸지 않아도 된다.

- 지역 -> data/restaurant_regions.csv (google_place_id 기준 건별 저장)
  시 > 구/군 > 동·면(한국 기준 서울특별시 > 마포구 > 연남동, 성남시 > 분당구 > 정자동) 최대 3단계이고, 없는 단계는 건너뛰고 있는 데까지만 저장한다
  (예: 안동시는 시만). 도로명은 쓰지 않는다. 한국 도로명 주소는 구성요소에 동이 없는 경우가 많아(예: 부산 해운대구 해운대해변로) 구까지만 나온다.
  "OO역/OO 근처" 질문은 지역 컬럼이 아니라 식당의 위경도(restaurants.latitude/longitude)로 반경 검색한다.
  지역명은 languageCode=ko 로 요청한다. 한국어 이름이 없는 해외 지역은 현지 표기로 온다.
  addressComponents 원본은 저장하지 않는다(용량의 대부분). 매핑 규칙을 바꾸면 Details 를 다시 호출해 재생성한다.
- 가격대 -> data/restaurant_prices_sample.csv (google_cid 기준 건별 저장). 새 식당은 extract_restaurant_info.py 가 Text Search 때
  같은 파일에 기록하므로, 이 스크립트의 가격대는 그 전에 매칭된 식당을 채우는 용도다.
  priceLevel/priceRange 는 Enterprise SKU 필드라 호출당 비용이 더 높다. 그래서 가격대는 --prices 를 줄 때만 받고,
  한 식당에 꼭 필요한 필드만 요청한다(지역만 필요하면 addressComponents 만, 둘 다 필요하면 한 번에).
- 이미 기록된 식당은 건너뛰므로 중단 후 재실행하면 이어서 처리된다. API 오류가 난 건은 기록하지 않아 다음 실행에서 재시도된다.

사용(샘플 먼저):
  uv run python src/pipeline/enrich_places_details.py --limit 10 --regions-output data/restaurant_regions_sample.csv
  uv run python src/pipeline/enrich_places_details.py --prices --notes-tag gpt-4o-mini-script-split-v2   # 지역 + 영상 분석 결과가 있는 식당의 가격대
  uv run python src/pipeline/enrich_places_details.py --prices --skip-regions --notes-tag ...             # 가격대만

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
from dotenv import find_dotenv, load_dotenv

load_dotenv(find_dotenv(usecwd=True))
GOOGLE_PLACES_API_KEY = os.getenv("GOOGLE_PLACES_API_KEY")

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "data"
INPUT_FILE = DATA_DIR / "restaurants_info.csv"
REGIONS_FILE = DATA_DIR / "restaurant_regions.csv"
PRICES_FILE = DATA_DIR / "restaurant_prices_sample.csv"

LANGUAGE_CODE = "ko"
CALL_DELAY = (0.2, 0.5)

REGION_FIELDS = ["google_cid", "google_place_id", "country_code", "region_1", "region_2", "region_3"]
PRICE_FIELDS = ["google_cid", "google_place_id", "korean_name", "country_code", "price_level", "price_min", "price_max", "price_currency", "status"]


# -------------------------------------------------------------
# 1. 지역: addressComponents -> 시 > 다음 단계
# -------------------------------------------------------------
# 시 다음 단계(구/군)는 sublocality_level_1, 없으면 neighborhood. 그 다음 단계(동·면)는 sublocality_level_2.
# 도로명(sublocality_level_3 이하, 한국은 level_4)은 쓰지 않는다.
SECOND_LEVEL_TYPES = ["sublocality_level_1", "neighborhood"]
THIRD_LEVEL_TYPE = "sublocality_level_2"


def _component_name(components: List[Dict[str, Any]], comp_type: str) -> Optional[str]:
    for c in components:
        if comp_type in c.get("types", []):
            return c.get("longText") or c.get("shortText")
    return None


def extract_region_path(components: List[Dict[str, Any]]) -> List[str]:
    """
    addressComponents 에서 [시, 구/군, 동·면] 을 최대 3개까지, 없는 단계는 건너뛰고 있는 데까지 뽑는다(빈 칸 없이 앞에서부터 채움).
      - 시: locality(성남시) -> postal_town -> administrative_area_level_1(서울특별시)
      - 구/군: sublocality_level_1 -> neighborhood 중 먼저 있는 것
      - 동·면: sublocality_level_2
    나라마다 단계 구조가 달라서 일본 도쿄처럼 locality 가 구(区) 단위인 곳은 시 자리에 구가 들어간다.
    그런 경우는 규칙을 고친 뒤 Details 를 다시 호출해 재생성한다.
    """
    city = (_component_name(components, "locality")
            or _component_name(components, "postal_town")
            or _component_name(components, "administrative_area_level_1"))
    second = next((n for n in (_component_name(components, t) for t in SECOND_LEVEL_TYPES) if n), None)
    third = _component_name(components, THIRD_LEVEL_TYPE)
    # 제주처럼 Google 이 광역 이름의 일부('특별자치도')를 sublocality_level_1 에 넣는 경우가 있어, 광역 이름에 포함된 조각은 쓰지 않는다.
    admin1 = _component_name(components, "administrative_area_level_1") or ""
    if second and second != city and second in admin1:
        second = None
    path = [city] if city else []
    for name in (second, third):
        if name and name not in path:
            path.append(name)
    return path


def build_region_row(cid: str, place_id: str, country_code: str, components: List[Dict[str, Any]]) -> Dict[str, Any]:
    path = extract_region_path(components) + ["", "", ""]
    return {
        "google_cid": cid, "google_place_id": place_id, "country_code": country_code,
        "region_1": path[0], "region_2": path[1], "region_3": path[2],
    }


# -------------------------------------------------------------
# 2. 가격대: priceLevel / priceRange -> CSV 컬럼
# -------------------------------------------------------------
def _units(m: Optional[dict]) -> Optional[str]:
    return (m or {}).get("units", "0") if m else None


def price_fields(data: Optional[dict]) -> Dict[str, str]:
    """Places 응답(priceLevel/priceRange)을 가격 CSV 컬럼 값으로 바꾼다. extract_restaurant_info.py 도 같이 쓴다."""
    data = data or {}
    pr = data.get("priceRange") or {}
    return {"price_level": data.get("priceLevel") or "",
            "price_min": _units(pr.get("startPrice")) or "", "price_max": _units(pr.get("endPrice")) or "",
            "price_currency": (pr.get("startPrice") or pr.get("endPrice") or {}).get("currencyCode", "")}


# -------------------------------------------------------------
# 3. Details 호출 (필요한 필드만 요청)
# -------------------------------------------------------------
def fetch_details(place_id: str, need_region: bool, need_price: bool) -> Optional[dict]:
    fields = (["addressComponents"] if need_region else []) + (["priceLevel", "priceRange"] if need_price else [])
    resp = requests.get(f"https://places.googleapis.com/v1/places/{place_id}",
                        headers={"X-Goog-Api-Key": GOOGLE_PLACES_API_KEY, "X-Goog-FieldMask": ",".join(fields)},
                        params={"languageCode": LANGUAGE_CODE}, timeout=10)
    if resp.status_code != 200:
        print(f"    ⚠️ Details 응답 오류 ({resp.status_code}): {resp.text[:200]}")
        return None
    return resp.json()


# -------------------------------------------------------------
# 4. 배치 실행
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


def done_keys(path: Path, fieldnames: List[str], key: str, ok_only: bool = False) -> Optional[set]:
    """이미 기록된 식당 키 집합. 파일이 없으면 빈 집합, 컬럼이 현재 스키마와 다르면 None."""
    if not (path.exists() and path.stat().st_size > 0):
        return set()
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames != fieldnames:
            print(f"오류: {path} 의 컬럼이 현재 스키마와 다릅니다. 다른 경로를 쓰거나 파일을 옮겨 주세요.")
            return None
        return {r[key] for r in reader if not ok_only or r.get("status") == "ok"}


def open_writer(path: Path, fieldnames: List[str]):
    resume = path.exists() and path.stat().st_size > 0
    f = open(path, "a" if resume else "w", encoding="utf-8-sig", newline="")
    w = csv.DictWriter(f, fieldnames=fieldnames)
    if not resume:
        w.writeheader()
    return f, w


def price_wanted(notes_tag: str) -> Optional[set]:
    """--notes-tag 가 있으면 그 결과 폴더에 있는 식당의 google_cid 집합, 없으면 None(제한 없음)."""
    if not notes_tag:
        return None
    return {str(json.loads(f.read_text(encoding="utf-8")).get("google_cid")).strip()
            for f in (DATA_DIR / "video_notes" / notes_tag).glob("*.json")}


def main(input_file: Path, regions_output: Path, prices_output: Path, limit: Optional[int],
         do_regions: bool, do_prices: bool, notes_tag: str) -> None:
    if not GOOGLE_PLACES_API_KEY:
        print("오류: .env 에 GOOGLE_PLACES_API_KEY 가 필요합니다.")
        return
    if not input_file.exists():
        print(f"오류: {input_file} 파일이 없습니다.")
        return

    targets = load_targets(input_file)
    region_done = done_keys(regions_output, REGION_FIELDS, "google_place_id") if do_regions else set()
    price_done = done_keys(prices_output, PRICE_FIELDS, "google_cid", ok_only=True) if do_prices else set()
    if region_done is None or price_done is None:
        return
    wanted = price_wanted(notes_tag) if do_prices else None

    todo = []  # (식당 행, 지역 필요, 가격대 필요)
    for t in targets:
        pid, cid = t["google_place_id"].strip(), (t.get("google_cid") or "").strip()
        need_region = do_regions and pid not in region_done
        need_price = do_prices and bool(cid) and cid not in price_done and (wanted is None or cid in wanted)
        if need_region or need_price:
            todo.append((t, need_region, need_price))
    if limit:
        todo = todo[:limit]
    n_region, n_price = sum(1 for _, r, _ in todo if r), sum(1 for _, _, p in todo if p)
    print(f"식당 {len(targets)}곳 중 이번에 처리할 곳 {len(todo)}곳 (Details {len(todo)}회: 지역 {n_region}곳, 가격대 {n_price}곳"
          f"{', 가격대는 Enterprise SKU라 호출당 비용이 더 높음' if n_price else ''})")

    failed = 0
    region_f, region_w = open_writer(regions_output, REGION_FIELDS) if do_regions else (None, None)
    price_f, price_w = open_writer(prices_output, PRICE_FIELDS) if do_prices else (None, None)
    try:
        for i, (row, need_region, need_price) in enumerate(todo, start=1):
            pid, cid = row["google_place_id"].strip(), (row.get("google_cid") or "").strip()
            try:
                data = fetch_details(pid, need_region, need_price)
            except Exception as e:
                print(f"[{i}/{len(todo)}] ⚠️ 예기치 못한 오류 ({row.get('korean_name')}): {e}")
                data = None
            time.sleep(random.uniform(*CALL_DELAY))
            if data is None:
                failed += 1
                continue
            summary = []
            if need_region:
                result = build_region_row(cid, pid, row.get("country_code", ""), data.get("addressComponents", []))
                region_w.writerow(result)
                region_f.flush()
                summary.append(" > ".join(p for p in (result["region_1"], result["region_2"], result["region_3"]) if p) or "(지역 없음)")
            if need_price:
                price_w.writerow({"google_cid": cid, "google_place_id": pid, "korean_name": row.get("korean_name", ""),
                                  "country_code": row.get("country_code", ""), **price_fields(data), "status": "ok"})
                price_f.flush()
                summary.append(f"가격대 {data.get('priceLevel') or '(없음)'}")
            print(f"[{i}/{len(todo)}] {row.get('korean_name')}: {' / '.join(summary)}")
    finally:
        for f in (region_f, price_f):
            if f:
                f.close()
    print(f"완료. 실패 {failed}건(기록하지 않음, 재실행 시 재시도).")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Places Details 1회로 지역과 가격대를 함께 채운다.")
    ap.add_argument("--input", type=Path, default=INPUT_FILE)
    ap.add_argument("--regions-output", type=Path, default=REGIONS_FILE)
    ap.add_argument("--prices-output", type=Path, default=PRICES_FILE)
    ap.add_argument("--limit", type=int, default=None, help="이번 실행에서 처리할 식당 수(샘플 검증용)")
    ap.add_argument("--skip-regions", action="store_true", help="지역은 받지 않는다")
    ap.add_argument("--prices", action="store_true", help="가격대도 받는다(Enterprise SKU라 기본은 끔)")
    ap.add_argument("--notes-tag", default="", help="--prices 와 함께: data/video_notes/<태그>/ 결과에 있는 식당의 가격대만 받는다")
    args = ap.parse_args()
    if args.skip_regions and not args.prices:
        ap.error("--skip-regions 는 --prices 와 함께 써야 합니다(받을 항목이 없음).")
    main(args.input, args.regions_output, args.prices_output, args.limit, not args.skip_regions, args.prices, args.notes_tag)
