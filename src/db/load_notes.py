"""영상 추출 결과(data/video_notes/{model}/*.json) -> PostgreSQL 적재. 재실행해도 중복 없음.

선행: restaurant_schema.sql(또는 migrate_notes.sql)과 load_restaurants.py 를 먼저 실행해
restaurants / videos 가 채워져 있어야 한다. 결과 JSON의 google_cid 로 식당을 찾는다.

적재 내용:
  - restaurants.category_broad / category_detail : 영상 추출값(Gemini)
  - restaurant_tags   : cuisine_tags (기존 태그에 추가)
  - menus             : 메뉴/음료. (restaurant_id, video_id) 단위로 지우고 다시 넣는다.
  - video_restaurant_notes : 컨셉/분위기/총평/임베딩용 요약 + 원본 JSON

사용: uv run python src/db/load_notes.py [--model gemini-3.5-flash-lite] [--dry-run]
      POSTGRES_URI 환경변수(.env)를 사용한다.
"""
import argparse
import json
import os
from pathlib import Path

import psycopg2
from dotenv import load_dotenv
from psycopg2.extras import Json

ROOT = Path(__file__).resolve().parents[2]
NOTES_DIR = ROOT / "data" / "video_notes"


def timestamp_to_sec(text):
    """'MM:SS' 또는 'H:MM:SS' -> 초. 형식이 맞지 않으면 None."""
    if not text:
        return None
    try:
        parts = [int(p) for p in text.strip().split(":")]
    except ValueError:
        return None
    if len(parts) not in (2, 3) or any(p < 0 for p in parts):
        return None
    sec = 0
    for p in parts:
        sec = sec * 60 + p
    return sec


def menu_rows(note):
    """추출 JSON -> menus 행 목록 [(item_type, name, cooking, taste, tips, price, is_signature, evidence, first_appearance_sec)]"""
    rows = []
    for m in note.get("menus", []):
        rows.append(("FOOD", m["name"], m.get("cooking_features"), m.get("taste_review"), m.get("tips"),
                     m.get("price"), bool(m.get("is_signature")), m.get("evidence"),
                     timestamp_to_sec(m.get("first_appearance"))))
    for d in note.get("drinks", []):
        rows.append(("DRINK", d["name"], None, d.get("review"), None, None, False, None, None))
    return rows


def load_note(cur, note):
    """JSON 1개를 적재하고 요약 문자열을 반환. 식당이 DB에 없으면 None."""
    cur.execute("SELECT restaurant_id FROM restaurants WHERE google_cid = %s", (note["google_cid"],))
    found = cur.fetchone()
    if found is None:
        return None
    rid = found[0]
    vid = note["video_id"]

    cur.execute("SELECT 1 FROM videos WHERE video_id = %s", (vid,))
    if cur.fetchone() is None:
        return None

    cur.execute("UPDATE restaurants SET category_broad = %s, category_detail = %s, updated_at = now() WHERE restaurant_id = %s",
                (note.get("category_broad"), note.get("category_detail"), rid))

    for tag in note.get("cuisine_tags", []):
        cur.execute("INSERT INTO restaurant_tags (restaurant_id, tag) VALUES (%s,%s) ON CONFLICT DO NOTHING", (rid, tag))

    cur.execute("DELETE FROM menus WHERE restaurant_id = %s AND video_id = %s", (rid, vid))
    seen = set()
    for row in menu_rows(note):
        if (row[0], row[1]) in seen:  # 같은 이름이 두 번 나오면 유니크 제약에 걸리므로 첫 번째만
            continue
        seen.add((row[0], row[1]))
        cur.execute("""INSERT INTO menus (restaurant_id, video_id, item_type, name, cooking_features, taste_review,
                       tips, price_text, is_signature, evidence, first_appearance_sec)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (rid, vid, *row))

    cur.execute("""
        INSERT INTO video_restaurant_notes (video_id, restaurant_id, concept, atmosphere, key_points, final_review,
                                            embedding_text, model, raw)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT (video_id, restaurant_id) DO UPDATE SET
            concept = EXCLUDED.concept, atmosphere = EXCLUDED.atmosphere, key_points = EXCLUDED.key_points,
            final_review = EXCLUDED.final_review, embedding_text = EXCLUDED.embedding_text,
            model = EXCLUDED.model, raw = EXCLUDED.raw, extracted_at = now()""",
                (vid, rid, note.get("concept"), note.get("atmosphere"), Json(note.get("key_points", [])),
                 note.get("final_review"), note.get("embedding_text"), note.get("model"), Json(note)))
    return f"{note['korean_name']}: 메뉴/음료 {len(seen)}개, 태그 {len(note.get('cuisine_tags', []))}개"


def main(model, dry_run):
    files = sorted((NOTES_DIR / model).glob("*.json"))
    if not files:
        print(f"{NOTES_DIR / model} 에 결과 JSON이 없습니다.")
        return
    notes = [json.loads(f.read_text(encoding="utf-8")) for f in files]
    if dry_run:
        for n in notes:
            print(f"[dry-run] {n['video_id']} / {n['korean_name']} (cid {n['google_cid']}): "
                  f"{n['category_broad']}/{n['category_detail']}, 메뉴/음료 {len(menu_rows(n))}개, 태그 {n['cuisine_tags']}")
        return

    load_dotenv()
    conn = psycopg2.connect(os.environ["POSTGRES_URI"])
    cur = conn.cursor()
    missing = []
    for n in notes:
        summary = load_note(cur, n)
        if summary is None:
            missing.append(f"{n['video_id']}/{n['korean_name']}")
        else:
            print(f"[ok] {n['video_id']} {summary}")
    conn.commit()
    print(f"적재 {len(notes) - len(missing)}건, 식당/영상 미적재로 건너뜀 {len(missing)}건: {missing or '없음'}")
    if missing:
        print("→ load_restaurants.py 로 해당 영상의 식당을 먼저 적재한 뒤 다시 실행하세요.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="영상 추출 결과를 DB에 적재한다.")
    ap.add_argument("--model", default="gemini-3.5-flash-lite")
    ap.add_argument("--dry-run", action="store_true", help="DB 접속 없이 적재 대상만 출력")
    args = ap.parse_args()
    main(args.model, args.dry_run)
