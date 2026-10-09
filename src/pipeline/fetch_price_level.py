"""Google Places Details(New)에서 priceLevel / priceRange 를 받아 건별로 CSV에 저장한다(샘플 검증용).

- 대상: --notes-tag 가 있으면 data/video_notes/<태그>/ 결과에 있는 식당, 없으면 restaurants_info.csv 전체(google_place_id 가 있는 식당).
- 식당(google_cid)마다 1회 호출하고, 이미 저장된 식당은 건너뛰므로 중단 후 재실행하면 이어서 처리한다.
- 새 식당은 extract_restaurant_info.py 가 Text Search 때 가격을 함께 받아 같은 CSV에 기록하므로, 이 스크립트는 그 전에 매칭된 식당을 채우는 용도다.
- priceLevel / priceRange 는 Enterprise SKU 필드라 호출당 비용이 일반 Details 보다 높다. 대량 실행 전에 샘플로 채워지는 비율을 확인한다.

사용: uv run python src/pipeline/fetch_price_level.py --notes-tag gpt-4o-mini-script-split-v2 [--limit 51]
"""
import argparse
import csv
import json
import os
import random
import time
from pathlib import Path

import requests
from dotenv import find_dotenv, load_dotenv

load_dotenv(find_dotenv(usecwd=True))
API_KEY = os.getenv("GOOGLE_PLACES_API_KEY")

ROOT = Path(__file__).resolve().parents[2]
INPUT_FILE = ROOT / "data" / "restaurants_info.csv"
OUTPUT_FILE = ROOT / "data" / "restaurant_prices_sample.csv"
FIELDNAMES = ["google_cid", "google_place_id", "korean_name", "country_code", "price_level", "price_min", "price_max", "price_currency", "status"]


def fetch(place_id):
    resp = requests.get(f"https://places.googleapis.com/v1/places/{place_id}",
                        headers={"X-Goog-Api-Key": API_KEY, "X-Goog-FieldMask": "priceLevel,priceRange"},
                        params={"languageCode": "ko"}, timeout=10)
    if resp.status_code != 200:
        return None, f"HTTP {resp.status_code}: {resp.text[:120]}"
    return resp.json(), "ok"


def money(m):
    return (m or {}).get("units", "0") if m else None


def price_fields(data):
    """Places 응답(priceLevel/priceRange)을 가격 CSV 컬럼 값으로 바꾼다. extract_restaurant_info.py 도 같이 쓴다."""
    data = data or {}
    pr = data.get("priceRange") or {}
    return {"price_level": data.get("priceLevel") or "",
            "price_min": money(pr.get("startPrice")) or "", "price_max": money(pr.get("endPrice")) or "",
            "price_currency": (pr.get("startPrice") or pr.get("endPrice") or {}).get("currencyCode", "")}


def main(notes_tag, limit, output):
    wanted = None
    if notes_tag:
        wanted = {str(json.loads(f.read_text(encoding="utf-8")).get("google_cid")).strip()
                  for f in (ROOT / "data" / "video_notes" / notes_tag).glob("*.json")}
    with open(INPUT_FILE, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    targets, seen = [], set()
    for r in rows:
        cid, pid = r["google_cid"].strip(), r["google_place_id"].strip()
        if not cid or not pid or cid in seen or (wanted is not None and cid not in wanted):
            continue
        seen.add(cid)
        targets.append(r)
    done = set()
    if output.exists():
        with open(output, encoding="utf-8-sig", newline="") as f:
            done = {r["google_cid"] for r in csv.DictReader(f) if r["status"] == "ok"}
    todo = [r for r in targets if r["google_cid"].strip() not in done][:limit]
    print(f"대상 {len(targets)}곳, 이미 저장 {len(done)}곳, 이번 호출 {len(todo)}회")
    new_file = not output.exists()
    with open(output, "a", encoding="utf-8-sig", newline="") as out:
        w = csv.DictWriter(out, fieldnames=FIELDNAMES)
        if new_file:
            w.writeheader()
        for r in todo:
            data, status = fetch(r["google_place_id"].strip())
            w.writerow({"google_cid": r["google_cid"].strip(), "google_place_id": r["google_place_id"].strip(),
                        "korean_name": r["korean_name"], "country_code": r["country_code"],
                        **price_fields(data), "status": status})
            out.flush()
            time.sleep(random.uniform(0.2, 0.5))


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Places priceLevel / priceRange 샘플 수집")
    ap.add_argument("--notes-tag", default="")
    ap.add_argument("--limit", type=int, default=51)
    ap.add_argument("--output", type=Path, default=OUTPUT_FILE)
    a = ap.parse_args()
    main(a.notes_tag, a.limit, a.output)
