"""Places Details 로 priceLevel / priceRange 가 채워지는지 소수 샘플로 확인한다(저장하지 않고 출력만). 국가별로 고르게 뽑는다.

사용: uv run python src/pipeline/probe_price.py [--n 10] [--cid ...]
"""
import sys
from pathlib import Path as _P

sys.path.insert(0, str(_P(__file__).resolve().parents[1]))  # experiments/ 밖의 pipeline 모듈(get_transcripts_since 등)을 import 하기 위해

import argparse
import collections
import csv
import json
import os
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()
KEY = os.getenv("GOOGLE_PLACES_API_KEY")
ROOT = Path(__file__).resolve().parents[3]


def pick(n: int):
    seen = {}
    for l in open(ROOT / "data" / "transcript_chunks" / "chunks_500.jsonl", encoding="utf-8"):
        r = json.loads(l)
        seen.setdefault(r["google_cid"], r)
    place = {}
    with open(ROOT / "data" / "restaurants_info.csv", encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            place[row["google_cid"].strip()] = row["google_place_id"]
    by_cc = collections.defaultdict(list)
    for cid, r in seen.items():
        by_cc[r["country_code"]].append((cid, r["name"], r["country_code"], place[cid]))
    out, i = [], 0
    while len(out) < n and any(by_cc.values()):  # 국가별로 돌아가며 하나씩
        for cc in sorted(by_cc):
            if by_cc[cc] and len(out) < n:
                out.append(by_cc[cc].pop(0))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=10)
    args = ap.parse_args()
    filled = 0
    for cid, name, cc, pid in pick(args.n):
        resp = requests.get(f"https://places.googleapis.com/v1/places/{pid}",
                            headers={"X-Goog-Api-Key": KEY, "X-Goog-FieldMask": "priceLevel,priceRange"},
                            params={"languageCode": "ko"}, timeout=10)
        d = resp.json() if resp.status_code == 200 else {"error": resp.status_code}
        pr = d.get("priceRange")
        pr_text = f"{pr.get('startPrice', {}).get('units')}~{pr.get('endPrice', {}).get('units')} {(pr.get('startPrice') or pr.get('endPrice') or {}).get('currencyCode')}" if pr else None
        filled += bool(d.get("priceLevel") or pr)
        print(f"{cc} {name[:22]:<24} priceLevel={d.get('priceLevel')} priceRange={pr_text} {'' if 'error' not in d else d}")
        time.sleep(0.3)
    print(f"채워진 곳(priceLevel 또는 priceRange): {filled}/{args.n}")


if __name__ == "__main__":
    main()
