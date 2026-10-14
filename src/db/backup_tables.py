"""스키마 정리 마이그레이션(migrate_simplify_schema.sql) 전에 영향받는 테이블을 CSV로 백업한다. DB는 읽기만 한다.

저장 위치: data/backup_YYYYMMDD_HHMMSS/<테이블>.csv (data/ 는 git 제외 대상). 없는 테이블은 건너뛴다.
복원은 pg_restore 처럼 한 번에 되지 않고, 필요한 CSV를 골라 수동으로 다시 적재한다.

사용: uv run python src/db/backup_tables.py [--tables restaurants menus ...]
      POSTGRES_URI 환경변수(.env)를 사용한다.
"""
import argparse
import os
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_TABLES = ["restaurants", "regions", "restaurant_hours", "menus", "michelin_status", "michelin_records"]


def main(tables) -> None:
    import psycopg2
    from dotenv import load_dotenv

    load_dotenv()
    conn = psycopg2.connect(os.environ["POSTGRES_URI"])
    conn.set_session(readonly=True)
    cur = conn.cursor()

    out_dir = ROOT / "data" / f"backup_{datetime.now():%Y%m%d_%H%M%S}"
    out_dir.mkdir(parents=True, exist_ok=True)

    for table in tables:
        cur.execute("SELECT to_regclass(%s)", (f"public.{table}",))
        if cur.fetchone()[0] is None:
            print(f"- {table}: 테이블 없음, 건너뜀")
            continue
        path = out_dir / f"{table}.csv"
        with open(path, "w", encoding="utf-8-sig", newline="") as f:
            cur.copy_expert(f"COPY (SELECT * FROM {table}) TO STDOUT WITH CSV HEADER", f)
        cur.execute(f"SELECT count(*) FROM {table}")
        print(f"- {table}: {cur.fetchone()[0]}행 -> {path.relative_to(ROOT)}")
    conn.close()
    print(f"백업 완료: {out_dir.relative_to(ROOT)}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="마이그레이션 전에 주요 테이블을 CSV로 백업한다.")
    ap.add_argument("--tables", nargs="+", default=DEFAULT_TABLES)
    main(ap.parse_args().tables)
