"""restaurants.category_detail 을 한 식당에 하나만 남기도록 정리하고(나머지는 restaurant_tags 로), 수동 분류(MANUAL_CATEGORY)를 반영한다.
여러 번 실행해도 결과가 같다(이미 정리된 값은 건드리지 않는다). 규칙은 category_rules.py.

사용: uv run python src/db/normalize_categories.py --dry-run   # 바뀔 내용만 출력
      uv run python src/db/normalize_categories.py             # 적용 (POSTGRES_URI 관리자 계정 필요)
적용 뒤에는 개요 청크 내용(대분류/세부 분류)이 바뀌므로 embed_chunks.py 를 다시 실행한다(바뀐 청크만 임베딩한다).
"""
import argparse
import os

import psycopg2
from dotenv import load_dotenv

from category_rules import MANUAL_DROP_TAGS, normalize

load_dotenv()


def main(dry_run):
    conn = psycopg2.connect(os.environ["POSTGRES_URI"])
    cur = conn.cursor()
    cur.execute("SELECT restaurant_id, google_cid, COALESCE(name_ko, name_official), category_broad, category_detail FROM restaurants "
                "WHERE category_broad IS NOT NULL OR category_detail IS NOT NULL ORDER BY restaurant_id")
    changed = 0
    for rid, cid, name, broad, detail in cur.fetchall():
        nb, nd, extras, why = normalize(cid, broad, detail)
        drop = MANUAL_DROP_TAGS.get(cid, [])
        if drop:
            cur.execute("SELECT tag FROM restaurant_tags WHERE restaurant_id = %s AND tag = ANY(%s)", (rid, drop))
            drop = [t for (t,) in cur.fetchall()]
        if (nb, nd) == (broad, detail) and not extras and not drop:
            continue
        changed += 1
        print(f"{name}: [{broad}] {detail}  ->  [{nb}] {nd}" + (f"  | 태그 추가 {extras}" if extras else "") + (f"  | 태그 삭제 {drop}" if drop else "") + (f"  | {why}" if why else ""))
        if not dry_run:
            cur.execute("UPDATE restaurants SET category_broad = %s, category_detail = %s, updated_at = now() WHERE restaurant_id = %s", (nb, nd, rid))
            if drop:
                cur.execute("DELETE FROM restaurant_tags WHERE restaurant_id = %s AND tag = ANY(%s)", (rid, drop))
            for tag in extras:
                cur.execute("INSERT INTO restaurant_tags (restaurant_id, tag) VALUES (%s, %s) ON CONFLICT DO NOTHING", (rid, tag[:100]))
    if dry_run:
        conn.rollback()
    else:
        conn.commit()
    print(f"\n{'(dry-run) ' if dry_run else ''}변경 {changed}곳")
    conn.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    main(ap.parse_args().dry_run)
