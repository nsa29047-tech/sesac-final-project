"""청킹 방식 비교: 식당당 통합 청크(A) vs OVERVIEW + 메뉴 청크(B). DB(restaurant_chunks)에는 쓰지 않는다.

선행: load_notes.py 로 video_restaurant_notes / menus 가 적재돼 있어야 한다.
  - A: 식당당 1개. OVERVIEW 텍스트 + 메뉴명 목록
  - B: 식당당 OVERVIEW 1개 + 메뉴별 청크 (현재 embed_chunks.py 방식). 식당 점수는 청크 중 최고 유사도
메모리에서 임베딩하므로 비용은 청크 수 x text-embedding-3-small 정도(수백 개면 몇 원).

사용: uv run python src/db/compare_chunking.py [--query "질문"]... [--top 3]
"""
import argparse
import os
from collections import defaultdict

import numpy as np
import psycopg2
from dotenv import load_dotenv
from openai import OpenAI

from embed_chunks import BATCH, EMBED_MODEL, build_chunks

# 메뉴 구체 질문 / 분위기·컨셉 질문 / 섞인 질문을 골고루 넣는다. 데이터에 맞게 수정해서 쓴다.
DEFAULT_QUERIES = [
    "트러플이 들어간 면 요리",
    "랍스터 요리",
    "캐비아를 곁들여 먹는 메뉴",
    "회식하기 좋은 활기찬 고깃집",
    "고급스럽고 프라이빗한 분위기의 파인다이닝",
    "돼지고기 숯불구이 맛집",
]


def embed(client, texts):
    vecs = []
    for i in range(0, len(texts), BATCH):
        resp = client.embeddings.create(model=EMBED_MODEL, input=texts[i:i + BATCH])
        vecs += [d.embedding for d in resp.data]
    m = np.array(vecs, dtype=np.float32)
    return m / np.linalg.norm(m, axis=1, keepdims=True)  # 정규화하면 내적이 코사인 유사도


def rank_restaurants(owners, labels, matrix, qvec):
    """청크별 유사도 -> 식당별 최고 점수. [(restaurant_id, score, 매칭된 청크 라벨)] 내림차순"""
    sims = matrix @ qvec
    best = {}
    for rid, label, s in zip(owners, labels, sims):
        if rid not in best or s > best[rid][0]:
            best[rid] = (float(s), label)
    return sorted(((rid, s, lab) for rid, (s, lab) in best.items()), key=lambda x: -x[1])


def main(queries, top):
    load_dotenv()
    conn = psycopg2.connect(os.environ["POSTGRES_URI"])
    cur = conn.cursor()
    chunks = build_chunks(cur)  # (restaurant_id, video_id, type, key, content)
    cur.execute("SELECT restaurant_id, COALESCE(name_ko, name_official) FROM restaurants")
    names = dict(cur.fetchall())

    overview = {c[0]: c for c in chunks if c[2] == "OVERVIEW"}
    menus = defaultdict(list)
    for c in chunks:
        if c[2] == "MENU":
            menus[c[0]].append(c[3])

    # A: 통합 청크 (OVERVIEW + 메뉴명 목록)
    a_owner = list(overview)
    a_label = ["통합" for _ in a_owner]
    a_text = [overview[r][4] + ("\n메뉴: " + ", ".join(menus[r]) if menus[r] else "") for r in a_owner]

    # B: OVERVIEW + 메뉴 청크
    b_owner, b_label, b_text = [], [], []
    for c in chunks:
        b_owner.append(c[0])
        b_label.append("개요" if c[2] == "OVERVIEW" else f"메뉴:{c[3]}")
        b_text.append(c[4])

    print(f"식당 {len(overview)}곳 / A 청크 {len(a_text)}개 / B 청크 {len(b_text)}개")
    client = OpenAI()
    a_mat, b_mat = embed(client, a_text), embed(client, b_text)
    q_mat = embed(client, queries)

    for q, qv in zip(queries, q_mat):
        print(f"\n■ {q}")
        for title, owner, label, mat in (("A 통합", a_owner, a_label, a_mat), ("B 개요+메뉴", b_owner, b_label, b_mat)):
            ranked = rank_restaurants(owner, label, mat, qv)[:top]
            row = " | ".join(f"{names.get(r, r)} {s:.3f} ({lab})" for r, s, lab in ranked)
            print(f"  {title:<8} {row}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="식당당 통합 청크 vs 개요+메뉴 청크 검색 결과 비교 (DB 쓰기 없음)")
    ap.add_argument("--query", action="append", default=[], help="비교할 질문 (여러 번 지정 가능). 없으면 기본 질문")
    ap.add_argument("--top", type=int, default=3, help="질문당 보여줄 식당 수")
    args = ap.parse_args()
    main(args.query or DEFAULT_QUERIES, args.top)
