"""fetch_price_level.py 결과 CSV -> restaurants.price_level / price_min / price_max / price_currency. 재실행해도 결과가 같다.

선행: migrate_price_columns.sql 적용. 값이 없는 식당은 NULL 로 덮어쓴다(CSV 에 있는 식당만 건드린다).
사용: uv run python src/db/load_prices.py [--csv data/restaurant_prices_sample.csv]
"""
import argparse
import csv
import os
from pathlib import Path

import psycopg2
from dotenv import find_dotenv, load_dotenv

ROOT = Path(__file__).resolve().parents[2]
LEVELS = {"PRICE_LEVEL_FREE": 0, "PRICE_LEVEL_INEXPENSIVE": 1, "PRICE_LEVEL_MODERATE": 2,
          "PRICE_LEVEL_EXPENSIVE": 3, "PRICE_LEVEL_VERY_EXPENSIVE": 4}


def to_int(v):
    return int(v) if v and v.strip() else None


def main(csv_path):
    load_dotenv(find_dotenv(usecwd=True))
    conn = psycopg2.connect(os.environ["POSTGRES_URI"])
    cur = conn.cursor()
    updated = missing = 0
    with open(csv_path, encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            if r["status"] != "ok":
                continue
            cur.execute("""UPDATE restaurants SET price_level = %s, price_min = %s, price_max = %s, price_currency = %s,
                           updated_at = now() WHERE google_cid = %s""",
                        (LEVELS.get(r["price_level"]), to_int(r["price_min"]), to_int(r["price_max"]),
                         r["price_currency"] or None, r["google_cid"]))
            updated += cur.rowcount
            missing += cur.rowcount == 0
    conn.commit()
    print(f"적재 {updated}곳, DB에 식당이 없어 건너뜀 {missing}곳")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", type=Path, default=ROOT / "data" / "restaurant_prices_sample.csv")
    main(ap.parse_args().csv)
