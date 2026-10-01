"""video_restaurant_notes / menus -> 청크 생성 -> 임베딩 -> restaurant_chunks 적재. 재실행해도 중복 없음.

선행: restaurant_chunks.sql 실행(pgvector), load_notes.py 실행.
  - OVERVIEW 청크: 식당 이름 + 대분류/세부 + 컨셉 + 분위기 + 총평 + 방문 팁 + 임베딩용 요약
  - MENU 청크    : 식당 이름 + 메뉴명 + 조리 특징 + 맛 평가 + 팁 (음식만, 음료 제외)
  content 가 이전과 같으면 임베딩을 다시 호출하지 않는다(비용 절약, 이어서 실행 가능).

사용: uv run python src/db/embed_chunks.py [--dry-run]
"""
import argparse
import os

import psycopg2
from dotenv import load_dotenv
from openai import OpenAI

EMBED_MODEL = "text-embedding-3-small"
BATCH = 64


def overview_text(r):
    parts = [f"{r['name']}", " / ".join(x for x in (r["category_broad"], r["category_detail"]) if x)]
    parts += [r["concept"], r["atmosphere"], r["final_review"], " ".join(r["key_points"]), r["embedding_text"]]
    return "\n".join(p for p in parts if p)


def menu_text(restaurant_name, m):
    body = " ".join(x for x in (m["cooking_features"], m["taste_review"], m["tips"]) if x)
    return f"{restaurant_name}의 {m['name']}" + (f": {body}" if body else "")


def build_chunks(cur):
    """[(restaurant_id, video_id, chunk_type, chunk_key, content)]"""
    chunks = []
    cur.execute("""
        SELECT n.restaurant_id, n.video_id, COALESCE(r.name_ko, r.name_official), r.category_broad, r.category_detail,
               n.concept, n.atmosphere, n.final_review, n.key_points, n.embedding_text
        FROM video_restaurant_notes n JOIN restaurants r USING (restaurant_id)""")
    for rid, vid, name, broad, detail, concept, atmos, final, kp, emb in cur.fetchall():
        r = dict(name=name, category_broad=broad, category_detail=detail, concept=concept, atmosphere=atmos,
                 final_review=final, key_points=kp or [], embedding_text=emb)
        chunks.append((rid, vid, "OVERVIEW", "overview", overview_text(r)))

    cur.execute("""
        SELECT m.restaurant_id, m.video_id, COALESCE(r.name_ko, r.name_official), m.name,
               m.cooking_features, m.taste_review, m.tips
        FROM menus m JOIN restaurants r USING (restaurant_id)
        WHERE m.item_type = 'FOOD' AND m.video_id IS NOT NULL""")
    for rid, vid, rname, name, cook, taste, tips in cur.fetchall():
        m = dict(name=name, cooking_features=cook, taste_review=taste, tips=tips)
        chunks.append((rid, vid, "MENU", name, menu_text(rname, m)))
    return chunks


def main(dry_run):
    load_dotenv()
    conn = psycopg2.connect(os.environ["POSTGRES_URI"])
    cur = conn.cursor()
    chunks = build_chunks(cur)

    cur.execute("SELECT restaurant_id, video_id, chunk_type, chunk_key, content FROM restaurant_chunks")
    existing = {(a, b, c, d): e for a, b, c, d, e in cur.fetchall()}
    todo = [c for c in chunks if existing.get(c[:4]) != c[4]]
    print(f"청크 {len(chunks)}개 중 새로 임베딩할 것 {len(todo)}개")
    if dry_run:
        for c in todo[:10]:
            print(f"  [{c[2]}] {c[3]}: {c[4][:80]}")
        return

    client = OpenAI()
    for i in range(0, len(todo), BATCH):
        batch = todo[i:i + BATCH]
        resp = client.embeddings.create(model=EMBED_MODEL, input=[c[4] for c in batch])
        for c, item in zip(batch, resp.data):
            vec = "[" + ",".join(f"{x:.7f}" for x in item.embedding) + "]"
            cur.execute("""
                INSERT INTO restaurant_chunks (restaurant_id, video_id, chunk_type, chunk_key, content, embedding, model)
                VALUES (%s,%s,%s,%s,%s,%s::vector,%s)
                ON CONFLICT (restaurant_id, video_id, chunk_type, chunk_key)
                DO UPDATE SET content = EXCLUDED.content, embedding = EXCLUDED.embedding, model = EXCLUDED.model""",
                        (*c, vec, EMBED_MODEL))
        conn.commit()  # 배치마다 저장해 중단돼도 이어서 처리된다
        print(f"  {min(i + BATCH, len(todo))}/{len(todo)} 완료")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="청크를 만들어 임베딩하고 restaurant_chunks 에 적재한다.")
    ap.add_argument("--dry-run", action="store_true", help="임베딩 호출 없이 대상만 출력")
    main(ap.parse_args().dry_run)
