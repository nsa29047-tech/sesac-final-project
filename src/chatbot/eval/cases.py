"""챗봇 평가 케이스. 정답은 고정 목록이 아니라 DB에서 SQL로 계산하므로 데이터가 늘어도 그대로 쓴다.

kind
- sql      : 정형 조건 질문. 도구가 돌려준 식당이 모두 gold_sql 결과 안에 있어야 한다(정밀도 100%, 1곳 이상).
- taste    : 취향 질문. 돌려준 식당 중 hit_sql(키워드로 만든 정답 후보)에 속한 곳이 1곳 이상, must_sql(하드 조건)은 모두 만족.
- none     : DB에 없는 음식·나라. 어떤 식당도 추천하지 않고 "없다"고 답해야 한다. empty_sql은 정말 없는지 확인하는 점검용(결과가 있으면 케이스가 낡은 것).
- info     : 특정 식당 조회. get_restaurant_info를 호출하고 답변에 contains_sql 값 중 하나가 있어야 한다.
- chitchat : 맛집과 무관한 말. 도구를 호출하지 않아야 한다.

tools: 이 중 하나라도 호출돼야 도구 선택이 맞다.
모든 SQL은 restaurant_id(또는 contains_sql은 text) 한 열을 돌려준다.
"""

# 강남역(37.4979, 127.0276) / 파리(48.8566, 2.3522) 반경 조건
def near(lat, lng, km):
    return (f"SELECT restaurant_id FROM restaurants r WHERE latitude IS NOT NULL AND "
            f"6371 * acos(LEAST(1, GREATEST(-1, cos(radians({lat})) * cos(radians(latitude::float8)) * "
            f"cos(radians(longitude::float8) - radians({lng})) + sin(radians({lat})) * sin(radians(latitude::float8))))) <= {km}")


def open_at(day, hhmm, extra="TRUE"):
    return (f"SELECT r.restaurant_id FROM restaurants r WHERE ({extra}) AND EXISTS (SELECT 1 FROM restaurant_hours h "
            f"WHERE h.restaurant_id = r.restaurant_id AND h.day_of_week = {day} AND h.open_time IS NOT NULL AND "
            f"((h.close_time > h.open_time AND '{hhmm}'::time >= h.open_time AND '{hhmm}'::time < h.close_time) OR "
            f"(h.close_time <= h.open_time AND ('{hhmm}'::time >= h.open_time OR '{hhmm}'::time < h.close_time))))")


def menu_or_tag(regex):
    """메뉴 이름·설명, 태그, 분류에 regex(대소문자 무시)가 있는 식당."""
    return (f"SELECT restaurant_id FROM menus WHERE name ~* '{regex}' OR description ~* '{regex}' "
            f"UNION SELECT restaurant_id FROM restaurant_tags WHERE tag ~* '{regex}' "
            f"UNION SELECT restaurant_id FROM restaurants WHERE category_detail ~* '{regex}'")


def michelin(grades):
    g = ",".join(f"'{x}'" for x in grades)
    return f"SELECT restaurant_id FROM michelin_status WHERE is_michelin AND latest_grade IN ({g})"


STARS = ["1_STAR", "2_STARS", "3_STARS"]
ALL_M = STARS + ["BIB_GOURMAND", "SELECTED"]
FIND, TASTE, INFO = ["find_restaurants"], ["recommend_by_taste"], ["get_restaurant_info"]

CASES = [
    # ---- 정형 조건(SQL) ----
    dict(id="sql-michelin-seoul", kind="sql", q="서울에 있는 미슐랭 스타 식당 알려줘", tools=FIND,
         gold_sql=f"SELECT restaurant_id FROM restaurants WHERE region_1 ILIKE '%서울%' AND restaurant_id IN ({michelin(STARS)})"),
    dict(id="sql-3star", kind="sql", q="미슐랭 3스타 식당 어디 있어?", tools=FIND, gold_sql=michelin(["3_STARS"])),
    dict(id="sql-bib", kind="sql", q="빕 구르망 식당 추천해줘", tools=FIND, gold_sql=michelin(["BIB_GOURMAND"])),
    dict(id="sql-fr-michelin", kind="sql", q="프랑스에 있는 미슐랭 식당", tools=FIND,
         gold_sql=f"SELECT restaurant_id FROM restaurants WHERE country_code = 'FR' AND restaurant_id IN ({michelin(ALL_M)})"),
    dict(id="sql-busan", kind="sql", q="부산 식당 알려줘", tools=FIND,
         gold_sql="SELECT restaurant_id FROM restaurants WHERE region_1 ILIKE '%부산%'"),
    dict(id="sql-andong", kind="sql", q="안동에 있는 식당", tools=FIND,
         gold_sql="SELECT restaurant_id FROM restaurants WHERE region_1 ILIKE '%안동%' OR region_2 ILIKE '%안동%'"),
    dict(id="sql-japan", kind="sql", q="일본에 있는 식당 추천해줘", tools=FIND,
         gold_sql="SELECT restaurant_id FROM restaurants WHERE country_code = 'JP'"),
    dict(id="sql-abroad", kind="sql", q="한국 말고 해외 식당만 보여줘", tools=FIND,
         gold_sql="SELECT restaurant_id FROM restaurants WHERE country_code <> 'KR'"),
    dict(id="sql-hongkong", kind="sql", q="홍콩 식당 알려줘", tools=FIND + TASTE,
         gold_sql="SELECT restaurant_id FROM restaurants WHERE country_code = 'HK' OR category_broad ~ '홍콩' OR category_detail ~ '홍콩|광동|딤섬' "
                  "OR restaurant_id IN (SELECT restaurant_id FROM restaurant_tags WHERE tag ~ '홍콩|광동|딤섬')"),
    dict(id="sql-rating", kind="sql", q="평점 4.8 이상인 식당", tools=FIND,
         gold_sql="SELECT restaurant_id FROM restaurants WHERE rating >= 4.8"),
    dict(id="sql-cheap", kind="sql", q="가격대가 저렴한 식당 추천해줘", tools=FIND + TASTE,
         gold_sql="SELECT restaurant_id FROM restaurants WHERE price_level <= 2"),
    dict(id="sql-expensive", kind="sql", q="가장 비싼 고급 레스토랑", tools=FIND,
         gold_sql="SELECT restaurant_id FROM restaurants WHERE price_level >= 3"),
    dict(id="sql-chef", kind="sql", q="셰프 정보가 있는 식당", tools=FIND + TASTE,
         gold_sql="SELECT restaurant_id FROM restaurants WHERE chef_name IS NOT NULL"),
    dict(id="sql-course", kind="sql", q="코스 요리를 하는 식당", tools=FIND + TASTE,
         gold_sql="SELECT restaurant_id FROM video_restaurant_notes WHERE is_course"),
    dict(id="sql-open-sat", kind="sql", q="토요일 저녁 7시에 영업하는 서울 식당", tools=FIND,
         gold_sql=open_at(6, "19:00", "r.region_1 ILIKE '%서울%'")),
    dict(id="sql-open-mon-michelin", kind="sql", q="월요일 낮 12시에 문 여는 미슐랭 식당", tools=FIND,
         gold_sql=open_at(1, "12:00", f"r.restaurant_id IN ({michelin(ALL_M)})")),
    dict(id="sql-near-gangnam", kind="sql", q="강남역 근처 식당 알려줘", tools=FIND + TASTE, geo=True,
         gold_sql=near(37.4979, 127.0276, 3)),
    dict(id="sql-near-paris", kind="sql", q="파리에 있는 식당", tools=FIND + TASTE, geo=True,
         gold_sql=near(48.8566, 2.3522, 30) + " UNION SELECT restaurant_id FROM restaurants WHERE region_1 ILIKE '%paris%'"),
    dict(id="sql-2star-korean", kind="sql", q="미슐랭 2스타 이상 한식당", tools=FIND + TASTE,
         gold_sql=f"SELECT restaurant_id FROM restaurants WHERE category_broad ~ '한식' AND restaurant_id IN ({michelin(['2_STARS', '3_STARS'])})"),

    # ---- 취향(RAG) ----
    dict(id="taste-lamb", kind="taste", q="양고기 요리가 맛있는 곳", tools=TASTE, hit_sql=menu_or_tag("양고기|양갈비|램|lamb|양 미트볼|양 소시지")),
    dict(id="taste-fried", kind="taste", q="바삭하게 튀긴 음식을 먹을 수 있는 곳", tools=TASTE, hit_sql=menu_or_tag("튀김|fried|프라이")),
    dict(id="taste-dimsum", kind="taste", q="딤섬 맛집 추천", tools=TASTE + FIND, hit_sql=menu_or_tag("딤섬")),
    dict(id="taste-pasta", kind="taste", q="파스타가 맛있는 식당", tools=TASTE, hit_sql=menu_or_tag("파스타|pasta")),
    dict(id="taste-foiegras", kind="taste", q="푸아그라 요리를 하는 곳", tools=TASTE, hit_sql=menu_or_tag("푸아그라|foie")),
    dict(id="taste-seafood", kind="taste", q="신선한 해산물을 먹을 수 있는 식당", tools=TASTE, hit_sql=menu_or_tag("해산물|회|생선|랍스터|새우|굴|seafood")),
    dict(id="taste-wine", kind="taste", q="와인이랑 같이 즐기기 좋은 곳", tools=TASTE, hit_sql=menu_or_tag("와인|wine")),
    dict(id="taste-hanwoo-seoul", kind="taste", q="서울에서 한우 요리 먹을 수 있는 곳", tools=TASTE + FIND,
         must_sql="SELECT restaurant_id FROM restaurants WHERE region_1 ILIKE '%서울%'", hit_sql=menu_or_tag("한우")),
    dict(id="taste-chef-show", kind="taste", q="흑백요리사에 나온 셰프의 식당", tools=TASTE + FIND,
         hit_sql="SELECT restaurant_id FROM restaurants WHERE chef_info ~ '흑백요리사'"),
    dict(id="taste-french-fine", kind="taste", q="분위기 좋은 프랑스 파인다이닝", tools=TASTE + FIND,
         must_sql="SELECT restaurant_id FROM restaurants WHERE country_code = 'FR' OR category_detail ~ '프렌치|프랑스' "
                  "OR restaurant_id IN (SELECT restaurant_id FROM restaurant_tags WHERE tag ~ '프랑스|프렌치')",
         hit_sql="SELECT restaurant_id FROM restaurants WHERE country_code = 'FR' OR category_detail ~ '프렌치|프랑스'"),
    dict(id="taste-tapas", kind="taste", q="스페인 타파스 바", tools=TASTE + FIND, hit_sql=menu_or_tag("타파스|tapas")),
    dict(id="taste-steak", kind="taste", q="스테이크가 맛있는 식당", tools=TASTE, hit_sql=menu_or_tag("스테이크|steak|T본")),

    # ---- 데이터에 없는 질문(없다고 답해야 함) ----
    dict(id="none-thai", kind="none", q="태국 팟타이 맛집 있어?", tools=FIND + TASTE,
         empty_sql="SELECT restaurant_id FROM restaurants WHERE country_code = 'TH' UNION SELECT restaurant_id FROM menus WHERE name ~* '팟타이|똠얌|pad thai'"),
    dict(id="none-indian", kind="none", q="인도 탄두리 치킨 맛집 알려줘", tools=FIND + TASTE,
         empty_sql="SELECT restaurant_id FROM restaurants WHERE country_code = 'IN' UNION SELECT restaurant_id FROM menus WHERE name ~* '탄두리|비리야니|tandoori'"),
    dict(id="none-turkish", kind="none", q="터키 케밥 전문점", tools=FIND + TASTE,
         empty_sql="SELECT restaurant_id FROM restaurants WHERE country_code = 'TR' UNION SELECT restaurant_id FROM menus WHERE name ~* '케밥|kebab'"),
    dict(id="none-uk", kind="none", q="영국식 피쉬앤칩스 파는 곳", tools=FIND + TASTE,
         empty_sql="SELECT restaurant_id FROM restaurants WHERE country_code = 'GB' UNION SELECT restaurant_id FROM menus WHERE name ~* '피쉬앤칩스|fish and chips|fish & chips'"),
    dict(id="none-brazil", kind="none", q="브라질 슈하스코 식당", tools=FIND + TASTE,
         empty_sql="SELECT restaurant_id FROM restaurants WHERE country_code = 'BR' UNION SELECT restaurant_id FROM menus WHERE name ~* '슈하스코|churrasco'"),

    # ---- 특정 식당 조회 ----
    dict(id="info-mingles-menu", kind="info", q="밍글스 메뉴 알려줘", tools=INFO,
         contains_sql="SELECT m.name FROM menus m JOIN restaurants r USING (restaurant_id) WHERE r.name_ko = '밍글스'"),
    dict(id="info-mingles-hours", kind="info", q="밍글스는 몇 시에 문 열어?", tools=INFO,
         contains_sql="SELECT to_char(h.open_time, 'HH24:MI') FROM restaurant_hours h JOIN restaurants r USING (restaurant_id) "
                      "WHERE r.name_ko = '밍글스' AND h.open_time IS NOT NULL"),
    dict(id="info-mingles-michelin", kind="info", q="밍글스 미슐랭 등급이 뭐야?", tools=INFO, contains=["3스타", "3 스타", "세 개", "삼 스타"]),

    # ---- 범위 밖 ----
    dict(id="chat-weather", kind="chitchat", q="오늘 서울 날씨 어때?"),
    dict(id="chat-hello", kind="chitchat", q="안녕! 넌 뭘 할 수 있어?"),
]
