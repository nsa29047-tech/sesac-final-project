"""restaurant_regions.csv -> restaurants.region_1/2/3 적재. 재실행해도 중복 없음.

선행: restaurant_schema.sql(이미 적용한 DB는 migrate_simplify_schema.sql)과 load_restaurants.py 를 먼저 실행해
restaurants 에 region_1/2/3 컬럼이 있고 식당이 채워져 있어야 한다. google_cid 로 식당을 찾아 지역을 넣는다.

적재 내용:
  - restaurants.region_1/2/3 : 시 > 구/군 > 동·면. 없는 단계는 건너뛰고 앞에서부터 채운다(안동시는 region_1만).
                               restaurants 에 아직 없는 식당(google_cid)은 건너뛰고 목록을 알려 준다.

사용: uv run python src/db/load_regions.py [--csv data/restaurant_regions.csv] [--dry-run]
      POSTGRES_URI 환경변수(.env)를 사용한다. --dry-run 은 DB 접속 없이 지역 수만 출력한다.
"""
import argparse
import csv
import os
from pathlib import Path
from typing import Dict, List, Tuple

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CSV = ROOT / "data" / "restaurant_regions.csv"
NAME_MAX = 100   # restaurants.region_1/2/3 VARCHAR(100)


def region_path(row: Dict[str, str]) -> List[str]:
    """CSV 행 -> [시, 구/군, 동·면] (비어 있는 단계는 건너뛴 compact 목록)."""
    return [row[k].strip()[:NAME_MAX] for k in ("region_1", "region_2", "region_3") if row.get(k, "").strip()]


def load_rows(csv_path: Path) -> List[Dict[str, str]]:
    with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def dry_run(rows: List[Dict[str, str]]) -> None:
    levels: Dict[int, set] = {1: set(), 2: set(), 3: set()}
    skipped = 0
    for r in rows:
        path = region_path(r)
        if not path:
            skipped += 1
            continue
        for i in range(1, len(path) + 1):
            levels[i].add((r["country_code"].strip(),) + tuple(path[:i]))
    print(f"[dry-run] CSV {len(rows)}행, 건너뜀 {skipped}행 -> 서로 다른 지역: 시 {len(levels[1])}, 구/군 {len(levels[2])}, 동·면 {len(levels[3])}")


def main(csv_path: Path, is_dry_run: bool) -> None:
    rows = load_rows(csv_path)
    if is_dry_run:
        dry_run(rows)
        return

    import psycopg2
    from dotenv import load_dotenv

    load_dotenv()
    conn = psycopg2.connect(os.environ["POSTGRES_URI"])
    cur = conn.cursor()

    updated, skipped, missing = 0, [], []
    for r in rows:
        path = region_path(r)
        if not path:
            skipped.append(r["google_cid"])
            continue
        r1, r2, r3 = (path + [None] * 3)[:3]
        cur.execute("""UPDATE restaurants SET region_1 = %s, region_2 = %s, region_3 = %s, updated_at = now()
                       WHERE google_cid = %s""", (r1, r2, r3, r["google_cid"]))
        if cur.rowcount:
            updated += 1
        else:
            missing.append(r["google_cid"])
    conn.commit()
    print(f"식당 {updated}곳에 지역 적재. 지역 없음으로 건너뜀 {len(skipped)}곳, "
          f"restaurants 에 없어 건너뜀 {len(missing)}곳: {missing[:5] or '없음'}")
    if missing:
        print("→ load_restaurants.py 로 해당 식당을 먼저 적재한 뒤 다시 실행하세요.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="지역 CSV를 DB에 적재해 restaurants.region_1/2/3 에 넣는다.")
    ap.add_argument("--csv", type=Path, default=DEFAULT_CSV)
    ap.add_argument("--dry-run", action="store_true", help="DB 접속 없이 지역 수만 출력")
    args = ap.parse_args()
    main(args.csv, args.dry_run)
