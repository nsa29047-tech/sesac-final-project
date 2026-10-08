"""RAG 검색: SQL 로 식당을 좁히고 -> 벡터 검색으로 청크 후보를 뽑고 -> LLM 이 질문에 맞는지 확인해 맞는 것만 돌려준다.

- 거리 임계값만으로는 맞는 결과와 없는 결과가 갈리지 않았다(짧은 질문 "딤섬"은 0.74, 없는 "일본 오마카세 스시"는 0.62).
  그래서 먼 후보만 거리로 버리고(MAX_DISTANCE), 나머지 후보는 LLM(gpt-4o-mini)이 "이 후보가 질문을 충족하는가"를 판단한다.
  충족하는 후보가 하나도 없으면 빈 목록을 돌려주고 챗봇은 "조건에 맞는 식당이 없다"고 답한다.
- 지명 중 음식 스타일로 쓰이는 말(홍콩 등)은 country_code 가 아니라 카테고리(category_broad/detail, 태그)로 판단한다.
  우 완턴 킹처럼 미국(US)에 있어도 홍콩식이면 "홍콩" 질문에 나와야 하기 때문이다. STYLE_PLACES 에 키워드를 추가하면 된다.
"""
import os
from typing import List, Optional

import psycopg2
from dotenv import find_dotenv, load_dotenv
from openai import OpenAI
from pydantic import BaseModel

load_dotenv(find_dotenv(usecwd=True))
EMBED_MODEL = "text-embedding-3-small"
JUDGE_MODEL = "gpt-4o-mini"
MAX_DISTANCE = 0.70   # 이보다 먼 후보는 판단 없이 버린다
CANDIDATES = 8        # LLM 이 판단할 후보 수

# 질문에 이 말이 있으면 country_code 대신 카테고리·태그에 키워드가 있는 식당으로 좁힌다
STYLE_PLACES = {"홍콩": ["홍콩", "광동", "딤섬", "완탕"]}


def style_filter(query: str):
    """(SQL 조건, 파라미터). 해당하는 말이 없으면 ("TRUE", [])."""
    clauses, params = [], []
    for place, keywords in STYLE_PLACES.items():
        if place in query:
            for kw in keywords:
                clauses.append("(r.category_broad ILIKE %s OR r.category_detail ILIKE %s OR EXISTS "
                               "(SELECT 1 FROM restaurant_tags t WHERE t.restaurant_id = r.restaurant_id AND t.tag ILIKE %s))")
                params += [f"%{kw}%"] * 3
    return (("(" + " OR ".join(clauses) + ")"), params) if clauses else ("TRUE", [])


class Judgement(BaseModel):
    relevant_ids: List[int]


def connect():
    return psycopg2.connect(os.environ["POSTGRES_URI"])


def candidates(cur, client, query, where="TRUE", params=None, chunk_type=None, k=CANDIDATES):
    emb = client.embeddings.create(model=EMBED_MODEL, input=[query]).data[0].embedding
    vec = "[" + ",".join(f"{x:.7f}" for x in emb) + "]"
    style_sql, style_params = style_filter(query)
    sql = f"""
        SELECT c.restaurant_id, COALESCE(r.name_ko, r.name_official), c.chunk_type, c.chunk_key, c.content,
               round((c.embedding <=> %s::vector)::numeric, 3)
        FROM restaurant_chunks c
        JOIN restaurants_in_scope r USING (restaurant_id)
        JOIN video_restaurant_notes n ON n.restaurant_id = c.restaurant_id AND n.video_id = c.video_id
        WHERE ({where}) AND {style_sql} {"AND c.chunk_type = %s" if chunk_type else ""}
        ORDER BY c.embedding <=> %s::vector LIMIT {k}"""
    args = [vec, *(params or []), *style_params, *([chunk_type] if chunk_type else []), vec]
    cur.execute(sql, args)
    return [dict(restaurant_id=a, name=b, chunk_type=c, key=d, content=e, distance=float(f)) for a, b, c, d, e, f in cur.fetchall()
            if float(f) <= MAX_DISTANCE]


def judge(client, query, cands):
    """질문을 실제로 충족하는 후보의 번호만 고른다. 비슷해 보이기만 하는 후보(다른 음식, 다른 나라)는 제외한다."""
    if not cands:
        return []
    shown = "\n\n".join(f"[{i}] 식당: {c['name']} ({c['chunk_type']})\n{c['content'][:400]}" for i, c in enumerate(cands, 1))
    r = client.beta.chat.completions.parse(
        model=JUDGE_MODEL, temperature=0, seed=42, response_format=Judgement,
        messages=[{"role": "system", "content":
                   "사용자 질문에 대해 검색된 후보가 질문의 요구를 실제로 충족하는지 판단한다. "
                   "후보 내용에 질문한 음식·음료·특징이 있거나 명확히 뒷받침될 때만 충족으로 본다. "
                   "이름이나 분위기만 비슷하고 질문한 대상이 없으면 충족이 아니다(예: 스시를 물었는데 이탈리안 식당, 와인과의 어울림을 물었는데 맥주). "
                   "충족하는 후보의 번호만 relevant_ids 에 넣고, 하나도 없으면 빈 배열을 돌려준다."},
                  {"role": "user", "content": f"질문: {query}\n\n{shown}"}])
    ids = r.choices[0].message.parsed.relevant_ids
    return [cands[i - 1] for i in ids if 1 <= i <= len(cands)]


def search(query, where="TRUE", params=None, chunk_type=None, cur=None, client=None):
    """질문에 맞는 청크 목록(가까운 순). 맞는 것이 없으면 빈 목록."""
    own = cur is None
    if own:
        conn = connect()
        cur = conn.cursor()
    client = client or OpenAI()
    try:
        return judge(client, query, candidates(cur, client, query, where, params, chunk_type))
    finally:
        if own:
            conn.close()
