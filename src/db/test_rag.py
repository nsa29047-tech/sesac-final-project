"""51곳 RAG 검색 테스트. SQL 로 조건을 좁힌 뒤(restaurants / video_restaurant_notes) 벡터 검색(restaurant_chunks)으로 고른다.

사용: uv run python src/db/test_rag.py
"""
import os

import psycopg2
from dotenv import find_dotenv, load_dotenv
from openai import OpenAI

load_dotenv(find_dotenv(usecwd=True))
conn = psycopg2.connect(os.environ["POSTGRES_URI"])
cur = conn.cursor()
client = OpenAI()

# (질문, SQL 조건, 청크 종류, 설명)
TESTS = [
    ("바삭하게 튀긴 음식", "TRUE", None, "필터 없음"),
    ("양고기 요리가 맛있는 곳", "TRUE", "FOOD", "필터 없음, 음식 청크"),
    ("한국 서울의 고기 구이 식당", "r.country_code = 'KR' AND starts_with(r.region_1, '서울')", None, "지역 필터(서울)"),
    ("흑백요리사에 나온 셰프의 식당", "r.chef_name IS NOT NULL", "CHEF", "CHEF 청크"),
    ("코스 요리로 즐기는 프랑스 레스토랑", "n.is_course", "OVERVIEW", "코스 식당만"),
    ("가볍게 먹을 수 있는 저렴한 한 끼", "r.price_level <= 2", None, "가격대 보통 이하"),
    ("고급스러운 분위기의 특별한 날 식사", "r.price_level >= 4", "OVERVIEW", "가격대 매우 비쌈"),
    ("와인과 곁들이기 좋은 음료", "TRUE", "DRINK", "음료 청크"),
    ("홍콩 딤섬이나 광둥 요리", "r.country_code = 'HK'", None, "홍콩만"),
    ("일본 오마카세 스시", "r.country_code = 'JP'", None, "일본만"),
]


def search(q, where, ctype, k=3):
    emb = client.embeddings.create(model="text-embedding-3-small", input=[q]).data[0].embedding
    vec = "[" + ",".join(f"{x:.7f}" for x in emb) + "]"
    cur.execute(f"""
        SELECT COALESCE(r.name_ko, r.name_official), c.chunk_type, c.chunk_key, left(c.content, 90),
               round((c.embedding <=> %s::vector)::numeric, 3), r.price_level, r.country_code
        FROM restaurant_chunks c
        JOIN restaurants r USING (restaurant_id)
        JOIN video_restaurant_notes n ON n.restaurant_id = c.restaurant_id AND n.video_id = c.video_id
        WHERE {where} {"AND c.chunk_type = %s" if ctype else ""}
        ORDER BY c.embedding <=> %s::vector LIMIT {k}""",
                (vec, ctype, vec) if ctype else (vec, vec))
    return cur.fetchall()


for q, where, ctype, desc in TESTS:
    cur.execute(f"SELECT count(DISTINCT r.restaurant_id) FROM restaurants r JOIN video_restaurant_notes n USING (restaurant_id) WHERE {where}")
    print(f"\n## {q}  [{desc}, 조건 통과 식당 {cur.fetchone()[0]}곳]")
    for name, ct, key, content, dist, pl, cc in search(q, where, ctype):
        print(f"  {dist}  {name} ({cc}, 가격 {pl}) [{ct}] {key if ct != 'OVERVIEW' else ''} | {content.replace(chr(10), ' ')}")
