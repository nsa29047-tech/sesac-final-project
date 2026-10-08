"""51곳 RAG 검색 테스트(rag_search.search: SQL 필터 -> 벡터 후보 -> LLM 확인). 맞는 결과가 없으면 "없음"을 출력한다.

사용: uv run python src/db/test_rag.py
"""
from openai import OpenAI

import rag_search as rs

# (질문, SQL 조건, 청크 종류)
TESTS = [
    ("바삭하게 튀긴 음식", "TRUE", None),
    ("양고기 요리가 맛있는 곳", "TRUE", "FOOD"),
    ("한국 서울의 고기 구이 식당", "r.country_code = 'KR' AND starts_with(r.region_1, '서울')", None),
    ("흑백요리사에 나온 셰프의 식당", "r.chef_name IS NOT NULL", "CHEF"),
    ("코스 요리로 즐기는 프랑스 레스토랑", "n.is_course", "OVERVIEW"),
    ("가볍게 먹을 수 있는 저렴한 한 끼", "r.price_level <= 2", None),
    ("고급스러운 분위기의 특별한 날 식사", "r.price_level >= 4", "OVERVIEW"),
    ("와인과 곁들이기 좋은 음료", "TRUE", "DRINK"),
    ("홍콩 딤섬이나 광둥 요리", "TRUE", None),          # 홍콩은 country_code 가 아니라 카테고리로 판단(rag_search.STYLE_PLACES)
    ("일본 오마카세 스시", "r.country_code = 'JP'", None),   # 데이터에 없음 -> "없음"이어야 한다
    ("태국 쌀국수", "TRUE", None),                        # 없음
    ("피자", "TRUE", None),
]

conn = rs.connect()
cur = conn.cursor()
client = OpenAI()
for q, where, ctype in TESTS:
    results = rs.search(q, where, None, ctype, cur=cur, client=client)
    print(f"\n## {q}")
    if not results:
        print("  -> 조건에 맞는 식당이 없습니다")
    for c in results[:4]:
        print(f"  {c['distance']}  {c['name']} [{c['chunk_type']}] {c['key'] if c['chunk_type'] != 'OVERVIEW' else ''} | {c['content'][:70].replace(chr(10), ' ')}")
