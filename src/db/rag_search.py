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
CANDIDATES = 8        # LLM 이 판단할 후보 수 (청크 종류를 지정한 경우)
PER_TYPE = 4          # 종류를 지정하지 않으면 종류별 후보 수 (최대 4종 x 4)

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
    key_terms: List[str]      # 먼저 질문에서 후보를 가려내는 핵심어(음식명·조리법·나라 표현)를 뽑게 해서, 일반어("치킨") 하나로 통과시키는 것을 막는다
    relevant_ids: List[int]


_pool = None   # enable_pool() 을 부르면 connect() 가 연결 풀을 쓴다(챗봇 API 서버용). 기본은 호출마다 새 연결.


class PooledConnection:
    """풀에서 빌린 연결. close() 는 연결을 닫지 않고 풀에 돌려준다(호출하는 쪽 코드는 그대로 conn.close() 를 쓴다)."""

    def __init__(self, pool, conn):
        self._pool, self._conn, self._returned = pool, conn, False

    def __getattr__(self, name):
        return getattr(self._conn, name)

    def close(self):
        if self._returned:
            return
        self._returned = True
        broken = self._conn.closed != 0
        if not broken:
            try:
                self._conn.rollback()   # 쓰다 만 트랜잭션을 정리하고 돌려준다
            except psycopg2.Error:
                broken = True
        self._pool.putconn(self._conn, close=broken)


def enable_pool(minconn: int = 1, maxconn: int = 8):
    """연결 풀을 켠다. 읽기 전용 계정 규칙은 그대로다(풀의 접속 문자열도 READONLY_POSTGRES_URI)."""
    global _pool
    if _pool is None:
        from psycopg2.pool import ThreadedConnectionPool
        _pool = ThreadedConnectionPool(minconn, maxconn, _readonly_uri(), keepalives=1, keepalives_idle=30, keepalives_interval=10, keepalives_count=3)
    return _pool


def _readonly_uri() -> str:
    uri = os.environ.get("READONLY_POSTGRES_URI")
    if not uri:
        raise RuntimeError("READONLY_POSTGRES_URI 가 없습니다. 챗봇은 읽기 전용 계정으로만 DB에 접속합니다(.env 에 설정하세요. 계정 생성: src/db/create_readonly_role.sql). "
                           "관리자 계정(POSTGRES_URI)으로는 접속하지 않습니다.")
    return uri


def connect():
    """읽기 전용 계정(READONLY_POSTGRES_URI)으로만 접속한다. 변수가 없으면 관리자 계정(POSTGRES_URI)으로 넘어가지 않고 실행을 멈춘다.
    챗봇이 조용히 쓰기 권한으로 동작하는 일을 막기 위해서다(적재·정리 스크립트는 이 함수를 쓰지 않고 POSTGRES_URI 를 직접 쓴다).
    풀이 켜져 있으면 풀에서 빌린 연결(PooledConnection)을 돌려준다."""
    if _pool is None:
        return psycopg2.connect(_readonly_uri())
    for _ in range(3):
        conn = _pool.getconn()
        try:
            if conn.closed == 0:
                with conn.cursor() as cur:   # 오래 놀던 연결이 서버 쪽에서 끊겼는지 확인한다
                    cur.execute("SELECT 1")
                conn.rollback()
                return PooledConnection(_pool, conn)
        except psycopg2.Error:
            pass
        _pool.putconn(conn, close=True)
    raise RuntimeError("DB 연결을 얻지 못했습니다")


def candidates(cur, client, query, where="TRUE", params=None, chunk_type=None, k=CANDIDATES):
    emb = client.embeddings.create(model=EMBED_MODEL, input=[query]).data[0].embedding
    vec = "[" + ",".join(f"{x:.7f}" for x in emb) + "]"
    style_sql, style_params = style_filter(query)
    sql = f"""
        SELECT c.restaurant_id, COALESCE(r.name_ko, r.name_official), c.chunk_type, c.chunk_key, c.content,
               round((c.embedding <=> %s::vector)::numeric, 3)
        FROM restaurant_chunks c
        JOIN restaurants r USING (restaurant_id)
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
                   "질문의 핵심 요소(고유한 음식명·나라 음식·조리법·양념)가 후보 내용에 그대로 있어야 한다. 음식 종류만 같고 수식어(탄두리, 인도식, 광둥식 등)가 후보에 없으면 충족이 아니다(탄두리 치킨을 물었는데 후보는 한국식 치킨이나 트러플 치킨이면 제외). "
                   "반대로 분위기·고급스러움 같은 주관적 표현은 후보 내용이 명백히 반하지 않으면 충족으로 본다. "
                   "먼저 질문의 핵심어(음식 종류가 아니라 그것을 특정하는 구체적인 말: 탄두리, 광둥식, 양갈비 같은 것)를 key_terms에 적고, 핵심어가 있으면 후보 내용에 그 핵심어나 같은 뜻의 말이 있는 후보만 relevant_ids 에 넣는다. 질문이 '저렴한', '분위기 좋은', '식당' 같은 일반·주관 표현뿐이라 구체적인 핵심어가 없으면 key_terms를 비우고 위의 기준(명백히 반하지 않으면 충족)으로만 판단한다(가격·지역·미슐랭은 SQL로 이미 걸렀다). 하나도 없으면 빈 배열을 돌려준다."},
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
        if chunk_type:
            cands = candidates(cur, client, query, where, params, chunk_type)
        else:   # 종류를 모르면 종류별로 후보를 뽑아 합친다(개요 청크만 상위를 채워 메뉴 청크가 밀리는 것을 막는다)
            cands = sorted((c for t in ("FOOD", "DRINK", "OVERVIEW", "CHEF") for c in candidates(cur, client, query, where, params, t, k=PER_TYPE)),
                           key=lambda c: c["distance"])
        return judge(client, query, cands)
    finally:
        if own:
            conn.close()
