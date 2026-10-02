"""restaurant_regions.csv -> PostgreSQL 적재 (regions 계층 테이블 + restaurants.region_id 연결). 재실행해도 중복 없음.

선행: restaurant_schema.sql(이미 적용한 DB는 migrate_regions.sql)과 load_restaurants.py 를 먼저 실행해
regions 테이블이 있고 restaurants 가 채워져 있어야 한다. google_cid 로 식당을 찾아 가장 하위 지역을 연결한다.

적재 내용:
  - regions     : 시(level 1) > 구/군(level 2) > 동·면(level 3). 같은 (국가, 부모, 단계, 이름)은 한 번만 만든다.
  - restaurants : region_id = 그 식당의 가장 하위 지역. 상위 지역은 regions.parent_id 로 올라가서 조회한다.
                  restaurants 에 아직 없는 식당(google_cid)은 건너뛰고 목록을 알려 준다.

사용: uv run python src/db/load_regions.py [--csv data/restaurant_regions.csv] [--dry-run]
      POSTGRES_URI 환경변수(.env)를 사용한다. --dry-run 은 DB 접속 없이 만들 지역 수만 출력한다.
"""
import argparse
import csv
import os
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CSV = ROOT / "data" / "restaurant_regions.csv"
NAME_MAX = 100   # regions.name VARCHAR(100)


def clean_country(code: Optional[str]) -> str:
    """regions.country_code 는 CHAR(2). 앞의 알파벳 2글자만 쓴다. 없으면 빈 문자열."""
    m = re.match(r"\s*([A-Za-z]{2})(?![A-Za-z])", code or "")
    return m.group(1).upper() if m else ""


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
        cc, path = clean_country(r["country_code"]), region_path(r)
        if not cc or not path:
            skipped += 1
            continue
        parent: Tuple = ()
        for i, name in enumerate(path, start=1):
            parent = parent + (name,)
            levels[i].add((cc,) + parent)
    print(f"[dry-run] CSV {len(rows)}행, 건너뜀 {skipped}행 -> 만들 지역: 시 {len(levels[1])}, 구/군 {len(levels[2])}, 동·면 {len(levels[3])}")


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
    cache: Dict[Tuple[str, int, int, str], int] = {}   # (country, parent_id, level, name) -> region_id

    def get_region(cc: str, parent_id: Optional[int], level: int, name: str) -> int:
        key = (cc, parent_id or 0, level, name)
        if key not in cache:
            cur.execute("""
                INSERT INTO regions (country_code, parent_id, level, name) VALUES (%s,%s,%s,%s)
                ON CONFLICT (country_code, COALESCE(parent_id, 0), level, name) DO UPDATE SET name = EXCLUDED.name
                RETURNING region_id""", (cc, parent_id, level, name))
            cache[key] = cur.fetchone()[0]
        return cache[key]

    linked, skipped, missing = 0, [], []
    for r in rows:
        cc, path = clean_country(r["country_code"]), region_path(r)
        if not cc or not path:
            skipped.append(r["google_cid"])
            continue
        parent_id = None
        for level, name in enumerate(path, start=1):
            parent_id = get_region(cc, parent_id, level, name)
        cur.execute("UPDATE restaurants SET region_id = %s, updated_at = now() WHERE google_cid = %s", (parent_id, r["google_cid"]))
        if cur.rowcount:
            linked += 1
        else:
            missing.append(r["google_cid"])
    conn.commit()
    print(f"지역 {len(cache)}개 생성/확인, 식당 {linked}곳 연결. 국가코드·지역 없음으로 건너뜀 {len(skipped)}곳, "
          f"restaurants 에 없어 건너뜀 {len(missing)}곳: {missing[:5] or '없음'}")
    if missing:
        print("→ load_restaurants.py 로 해당 식당을 먼저 적재한 뒤 다시 실행하세요.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="지역 CSV를 DB에 적재하고 restaurants.region_id 를 연결한다.")
    ap.add_argument("--csv", type=Path, default=DEFAULT_CSV)
    ap.add_argument("--dry-run", action="store_true", help="DB 접속 없이 만들 지역 수만 출력")
    args = ap.parse_args()
    main(args.csv, args.dry_run)
