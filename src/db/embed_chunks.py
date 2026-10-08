"""video_restaurant_notes / menus -> 청크 생성 -> 임베딩 -> restaurant_chunks 적재. 재실행해도 중복 없음.

선행: restaurant_chunks.sql 실행(pgvector), load_notes.py 실행.
  - OVERVIEW 청크: 식당 이름 + 대분류/세부 + 컨셉 + 분위기 + 총평 + 방문 팁 + 임베딩용 요약
  - CHEF 청크    : "셰프 이름: 경력" 만 담은 짧은 청크(restaurants.chef_name/chef_info, 셰프가 있는 식당만, chunk_key='chef').
                   개요 청크에 섞으면 경력 질문("흑백요리사 출연 셰프")의 순위가 낮아져 분리했다. 식당 이름은 넣지 않는다.
  - FOOD 청크    : 메뉴명 + 설명(menus.description) (menus.item_type = 'FOOD')
  - DRINK 청크   : 음료명 + 설명 (menus.item_type = 'DRINK')
  FOOD/DRINK 청크 content 에는 식당 이름을 넣지 않는다. 식당은 restaurant_id 로 정해지고(SQL 로 먼저 좁힌다), 이름까지 임베딩하면
  같은 식당의 청크끼리 벡터가 비슷해지고 메뉴 내용의 비중이 줄어든다. 답변에 쓸 식당 정보는 검색 결과에서 restaurants 를 조인해 붙인다.
  설명이 없는 메뉴는 메뉴명 한 줄뿐이라 검색 노이즈가 되므로 청크로 만들지 않는다
  (--include-empty 로 포함). 메뉴 원본은 menus 테이블에 그대로 있으니 "OO 식당 메뉴 알려줘"는 menus 를 조회해서 답한다.
  content 가 이전과 같으면 임베딩을 다시 호출하지 않는다(비용 절약, 이어서 실행 가능).
  주의: 이미 만들어진 청크를 지우지 않는다. 청크 종류를 바꿨거나 제외 규칙이 바뀌면 migrate_chunk_types.sql 처럼 SQL로 정리한다.

사용: uv run python src/db/embed_chunks.py [--dry-run] [--include-empty]
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


def chef_text(chef_name, chef_info):
    """경력이 있으면 chef_info("이름: 경력 / 이름: 경력") 그대로, 없으면 이름만."""
    return f"셰프 {chef_info}" if chef_info else f"셰프 {chef_name}"


def menu_text(m):
    return m["name"] + (f": {m['description']}" if m["description"] else "")


def build_chunks(cur, include_empty=False):
    """[(restaurant_id, video_id, chunk_type, chunk_key, content)]. chunk_type 은 OVERVIEW / FOOD / DRINK."""
    chunks = []
    cur.execute("""
        SELECT n.restaurant_id, n.video_id, COALESCE(r.name_ko, r.name_official), r.category_broad, r.category_detail,
               n.concept, n.atmosphere, n.final_review, n.key_points, n.embedding_text, r.chef_name, r.chef_info
        FROM video_restaurant_notes n JOIN restaurants r USING (restaurant_id)""")
    for rid, vid, name, broad, detail, concept, atmos, final, kp, emb, chef_name, chef_info in cur.fetchall():
        r = dict(name=name, category_broad=broad, category_detail=detail, concept=concept, atmosphere=atmos,
                 final_review=final, key_points=kp or [], embedding_text=emb, chef_name=chef_name, chef_info=chef_info)
        chunks.append((rid, vid, "OVERVIEW", "overview", overview_text(r)))
        if chef_name:
            chunks.append((rid, vid, "CHEF", "chef", chef_text(chef_name, chef_info)))

    cur.execute("""
        SELECT m.restaurant_id, m.video_id, m.item_type, m.name, m.description
        FROM menus m
        WHERE m.video_id IS NOT NULL""")
    for rid, vid, item_type, name, desc in cur.fetchall():
        if not include_empty and not desc:
            continue
        m = dict(name=name, description=desc)
        chunks.append((rid, vid, item_type, name, menu_text(m)))  # chunk_type = menus.item_type (FOOD / DRINK)
    return chunks


def main(dry_run, include_empty=False):
    load_dotenv()
    conn = psycopg2.connect(os.environ["POSTGRES_URI"])
    cur = conn.cursor()
    chunks = build_chunks(cur, include_empty)

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
    ap.add_argument("--include-empty", action="store_true", help="설명이 없는 메뉴(이름만 있는 청크)도 포함")
    args = ap.parse_args()
    main(args.dry_run, args.include_empty)
