"""챗봇 에이전트 도구.

- 정형 조건(국가·지역·미슐랭·가격대·평점·영업시간·좌표 반경)은 SQL, 취향(음식·분위기·메뉴)은 벡터 검색이다.
  `find_restaurants`는 SQL만, `recommend_by_taste`는 같은 SQL 조건으로 먼저 좁힌 뒤 벡터 검색(rag_search.search)을 한다.
- 식당 정보는 원본 테이블을 읽기 전용 계정(READONLY_POSTGRES_URI)으로 조회한다.
- 모든 도구는 JSON 문자열을 돌려주고, 결과가 없으면 빈 목록을 돌려준다(에이전트는 "없다"고 답한다).
"""
import json
import os
import sys
from decimal import Decimal
from pathlib import Path
from typing import List, Literal, Optional

import requests
from langchain_core.tools import tool
from openai import OpenAI
from pydantic import BaseModel, Field

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "db"))
import rag_search as rs  # noqa: E402  (connect, search)

MAX_RESULTS = 10
DAY_NAMES = ["일", "월", "화", "수", "목", "금", "토"]
GRADE_LABEL = {"NONE": "미슐랭 아님", "BIB_GOURMAND": "빕 구르망", "1_STAR": "미슐랭 1스타", "2_STARS": "미슐랭 2스타",
               "3_STARS": "미슐랭 3스타", "SELECTED": "미슐랭 셀렉티드", "UNKNOWN": "미슐랭 등재(등급 미확인)"}


def dumps(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, default=lambda o: float(o) if isinstance(o, Decimal) else str(o))


def run_query(sql, params=()):
    """읽기 전용으로 조회하고 dict 목록을 돌려준다."""
    conn = rs.connect()
    try:
        conn.set_session(readonly=True)
        with conn.cursor() as cur:
            cur.execute(sql, params)
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]
    finally:
        conn.close()


# ---- 공통 필터 -------------------------------------------------------------------------------------------------

class FilterArgs(BaseModel):
    """식당을 좁히는 정형 조건. 사용자가 말하지 않은 조건은 비워 둔다."""
    country_code: Optional[str] = Field(None, description="ISO 2자리 국가 코드 (한국 KR, 일본 JP, 프랑스 FR, 스페인 ES, 미국 US, 이탈리아 IT 등). 홍콩은 HK지만 '홍콩식'은 query에 쓴다")
    exclude_country_code: Optional[str] = Field(None, description="이 국가는 뺀다. '해외'·'한국 말고'는 KR")
    region: Optional[str] = Field(None, description="국내 지역명(서울, 마포구, 연남동, 부산 등). 시/구/동 어느 단계든 된다. 해외 도시나 'OO역 근처'는 쓰지 말고 geocode_place + near_lat/near_lng를 쓴다")
    michelin: Optional[List[Literal["BIB_GOURMAND", "1_STAR", "2_STARS", "3_STARS", "SELECTED"]]] = Field(None, description="미슐랭 등급 목록. '미슐랭 스타'는 [1_STAR,2_STARS,3_STARS], '미슐랭'만 말하면 전체 5개")
    min_price_level: Optional[int] = Field(None, ge=0, le=4, description="가격대 하한 (0 무료 ~ 4 매우 비쌈, Google 기준)")
    max_price_level: Optional[int] = Field(None, ge=0, le=4, description="가격대 상한. 저렴한 곳은 2 이하, 고급은 3 이상")
    min_rating: Optional[float] = Field(None, ge=0, le=5, description="Google 평점 하한")
    cuisine: Optional[str] = Field(None, description="음식 분류 키워드(한식, 일식, 오마카세, 파인다이닝, 라멘 등). 대분류·세부 분류·태그에서 찾는다")
    has_chef: Optional[bool] = Field(None, description="True면 대표 셰프 정보가 있는 식당만")
    course_only: Optional[bool] = Field(None, description="True면 코스 요리 식당만")
    open_day: Optional[int] = Field(None, ge=0, le=6, description="영업 요일 (0=일, 1=월 ... 6=토). open_time과 함께 쓴다")
    open_time: Optional[str] = Field(None, description="'HH:MM' (식당 현지 시간). 이 요일·시각에 영업 중인 식당만. 휴무 요일 자체를 물으면 open_time은 '12:00'처럼 낮 시간을 준다")
    near_lat: Optional[float] = Field(None, description="반경 검색 기준 위도 (geocode_place 결과)")
    near_lng: Optional[float] = Field(None, description="반경 검색 기준 경도")
    radius_km: float = Field(2.0, gt=0, le=50, description="near_lat/near_lng 기준 반경(km)")


# 좌표 간 거리(km). 파라미터 순서: lat, lng, lat
DIST_SQL = ("(6371 * acos(LEAST(1, GREATEST(-1, cos(radians(%s)) * cos(radians(r.latitude::float8)) * "
            "cos(radians(r.longitude::float8) - radians(%s)) + sin(radians(%s)) * sin(radians(r.latitude::float8))))))")


def build_where(f: FilterArgs):
    """(SQL 조건, 파라미터). 별칭 r = restaurants. 영구 폐업은 항상 뺀다."""
    c, p = ["COALESCE(r.business_status, '') <> 'CLOSED_PERMANENTLY'"], []
    if f.country_code:
        c.append("r.country_code = %s"); p.append(f.country_code.upper())
    if f.exclude_country_code:
        c.append("r.country_code <> %s"); p.append(f.exclude_country_code.upper())
    if f.region:
        c.append("(r.region_1 ILIKE %s OR r.region_2 ILIKE %s OR r.region_3 ILIKE %s)"); p += [f"%{f.region}%"] * 3
    if f.michelin:
        c.append("EXISTS (SELECT 1 FROM michelin_status m WHERE m.restaurant_id = r.restaurant_id AND m.is_michelin AND m.latest_grade = ANY(%s))")
        p.append(list(f.michelin))
    if f.min_price_level is not None:
        c.append("r.price_level >= %s"); p.append(f.min_price_level)
    if f.max_price_level is not None:
        c.append("r.price_level <= %s"); p.append(f.max_price_level)
    if f.min_rating is not None:
        c.append("r.rating >= %s"); p.append(f.min_rating)
    if f.cuisine:
        c.append("(r.category_broad ILIKE %s OR r.category_detail ILIKE %s OR EXISTS "
                 "(SELECT 1 FROM restaurant_tags t WHERE t.restaurant_id = r.restaurant_id AND t.tag ILIKE %s))")
        p += [f"%{f.cuisine}%"] * 3
    if f.has_chef:
        c.append("r.chef_name IS NOT NULL")
    if f.course_only:
        c.append("EXISTS (SELECT 1 FROM video_restaurant_notes cn WHERE cn.restaurant_id = r.restaurant_id AND cn.is_course)")
    if f.open_day is not None and f.open_time:
        # 자정을 넘기는 구간(close < open)도 처리. 전날에서 이어지는 구간은 다루지 않는다
        c.append("EXISTS (SELECT 1 FROM restaurant_hours h WHERE h.restaurant_id = r.restaurant_id AND h.day_of_week = %s "
                 "AND h.open_time IS NOT NULL AND ((h.close_time > h.open_time AND %s::time >= h.open_time AND %s::time < h.close_time) "
                 "OR (h.close_time <= h.open_time AND (%s::time >= h.open_time OR %s::time < h.close_time))))")
        p += [f.open_day, *([f.open_time] * 4)]
    if f.near_lat is not None and f.near_lng is not None:
        c.append(f"{DIST_SQL} <= %s"); p += [f.near_lat, f.near_lng, f.near_lat, f.radius_km]
    return " AND ".join(c), p


# ---- 결과 정리 -------------------------------------------------------------------------------------------------

def summarize(ids: List[int]):
    """식당 요약(이름·지역·분류·가격·평점·미슐랭·셰프·영상). ids 순서를 유지한다."""
    if not ids:
        return []
    rows = run_query("""
        SELECT r.restaurant_id, COALESCE(r.name_ko, r.name_official) AS name, r.name_official, r.country_code,
               concat_ws(' > ', r.region_1, r.region_2, r.region_3) AS region, r.formatted_address AS address,
               r.category_broad, r.category_detail, r.price_level, r.price_min, r.price_max, r.price_currency,
               r.rating, r.rating_count, r.business_status, r.chef_name, r.google_maps_url,
               m.latest_grade AS michelin_grade,
               (SELECT string_agg(DISTINCT v.title || ' ' || v.url, ' | ')
                  FROM video_restaurant_notes n JOIN videos v USING (video_id) WHERE n.restaurant_id = r.restaurant_id) AS videos
        FROM restaurants r LEFT JOIN michelin_status m ON m.restaurant_id = r.restaurant_id AND m.is_michelin
        WHERE r.restaurant_id = ANY(%s)""", (ids,))
    by_id = {r["restaurant_id"]: r for r in rows}
    out = []
    for i in ids:
        r = by_id.get(i)
        if r:
            r["michelin"] = GRADE_LABEL.get(r.pop("michelin_grade"), "미슐랭 아님(Parse 기준)")
            out.append(r)
    return out


# ---- 도구 ------------------------------------------------------------------------------------------------------

class FindArgs(FilterArgs):
    sort_by: Literal["rating", "price_low", "price_high", "distance"] = Field("rating", description="정렬: 평점 높은 순 / 가격 낮은 순 / 가격 높은 순 / 가까운 순(near_lat·near_lng 필요)")
    limit: int = Field(5, ge=1, le=MAX_RESULTS, description="돌려줄 식당 수")


@tool(args_schema=FindArgs)
def find_restaurants(sort_by: str = "rating", limit: int = 5, **filters) -> str:
    """정형 조건(국가·지역·미슐랭·가격대·평점·음식 분류·영업 요일/시간·좌표 반경)만으로 식당을 찾는다. 분위기·메뉴 같은 취향 조건이 없는 질문에 쓴다.
    취향 조건이 있으면 recommend_by_taste를 쓴다."""
    f = FilterArgs(**filters)
    where, params = build_where(f)
    order = {"rating": "r.rating DESC NULLS LAST, r.rating_count DESC NULLS LAST",
             "price_low": "r.price_level ASC NULLS LAST, r.rating DESC NULLS LAST",
             "price_high": "r.price_level DESC NULLS LAST, r.rating DESC NULLS LAST",
             "distance": None}[sort_by]
    if sort_by == "distance":
        if f.near_lat is None or f.near_lng is None:
            return dumps({"error": "distance 정렬은 near_lat·near_lng가 필요합니다. geocode_place로 좌표를 먼저 구하세요."})
        order = DIST_SQL
        order_params = [f.near_lat, f.near_lng, f.near_lat]
    else:
        order_params = []
    rows = run_query(f"SELECT r.restaurant_id FROM restaurants r WHERE {where} ORDER BY {order} LIMIT %s",
                     (*params, *order_params, limit))
    results = summarize([r["restaurant_id"] for r in rows])
    return dumps({"count": len(results), "restaurants": results})


class TasteArgs(FilterArgs):
    query: str = Field(description="찾고 싶은 취향을 담은 검색 문장. 예: '바삭하게 튀긴 음식', '조용하고 분위기 좋은 와인바', '양고기 요리가 맛있는 곳'")
    chunk_type: Optional[Literal["OVERVIEW", "FOOD", "DRINK", "CHEF"]] = Field(
        None, description="OVERVIEW 식당 전반(분위기·컨셉), FOOD 음식 메뉴, DRINK 음료, CHEF 셰프 경력. 모르면 비운다")


@tool(args_schema=TasteArgs)
def recommend_by_taste(query: str, chunk_type: Optional[str] = None, **filters) -> str:
    """취향·분위기·메뉴·셰프 경력처럼 비정형 조건으로 식당을 추천한다. 지역·미슐랭·가격대 같은 정형 조건은 먼저 SQL로 좁히고,
    그 안에서 영상 분석 청크를 벡터 검색한 뒤 LLM이 질문 충족 여부를 확인한다. 맞는 식당이 없으면 빈 목록이 온다."""
    f = FilterArgs(**filters)
    where, params = build_where(f)
    # candidates()의 SQL은 where 조각을 그대로 붙이므로, near 조건의 파라미터 순서는 build_where와 같다
    hits = rs.search(query, where, params, chunk_type)
    if not hits:
        return dumps({"count": 0, "restaurants": [], "note": "조건에 맞는 식당이 없습니다"})
    ordered, matched = [], {}
    for h in hits:
        if h["restaurant_id"] not in matched:
            ordered.append(h["restaurant_id"])
            matched[h["restaurant_id"]] = []
        matched[h["restaurant_id"]].append({"type": h["chunk_type"], "item": h["key"] if h["chunk_type"] != "OVERVIEW" else None,
                                            "content": h["content"][:300]})
    results = summarize(ordered[:5])
    for r in results:
        r["matched"] = matched[r["restaurant_id"]][:3]
    return dumps({"count": len(results), "restaurants": results})


@tool
def get_restaurant_info(name: str) -> str:
    """식당 이름(한글 또는 영문 일부)으로 상세 정보를 조회한다: 컨셉·분위기·키포인트·총평, 전체 메뉴/음료, 요일별 영업시간, 태그, 셰프.
    '○○ 메뉴 알려줘', '○○ 몇 시까지 해?' 같은 특정 식당 질문에 쓴다. 메뉴 설명은 영상에서 요약한 것이다."""
    rows = run_query("""SELECT restaurant_id FROM restaurants
                        WHERE name_ko ILIKE %s OR name_official ILIKE %s ORDER BY rating_count DESC NULLS LAST LIMIT 3""",
                     (f"%{name}%", f"%{name}%"))
    out = []
    for s in summarize([r["restaurant_id"] for r in rows]):
        rid = s["restaurant_id"]
        notes = run_query("SELECT concept, atmosphere, key_points, final_review, is_course, course_name FROM video_restaurant_notes WHERE restaurant_id = %s", (rid,))
        menus = run_query("SELECT item_type, name, description, course_stage FROM menus WHERE restaurant_id = %s ORDER BY item_type, menu_order NULLS LAST, menu_id", (rid,))
        hours = run_query("SELECT day_of_week, open_time, close_time FROM restaurant_hours WHERE restaurant_id = %s ORDER BY day_of_week, seq", (rid,))
        tags = run_query("SELECT tag FROM restaurant_tags WHERE restaurant_id = %s", (rid,))
        chef = run_query("SELECT chef_info FROM restaurants WHERE restaurant_id = %s", (rid,))
        s["notes"] = notes
        s["menus"] = menus
        s["hours"] = hours_text(hours)
        s["tags"] = [t["tag"] for t in tags]
        s["chef_info"] = chef[0]["chef_info"] if chef else None
        out.append(s)
    return dumps({"count": len(out), "restaurants": out}) if out else dumps({"count": 0, "restaurants": [], "note": "이름이 일치하는 식당이 없습니다"})


def hours_text(rows):
    days = {}
    for r in rows:
        days.setdefault(r["day_of_week"], []).append("휴무" if r["open_time"] is None else f"{r['open_time']:%H:%M}-{r['close_time']:%H:%M}")
    return {DAY_NAMES[d]: ", ".join(v) for d, v in sorted(days.items())} or "영업시간 정보 없음"


@tool
def geocode_place(place: str) -> str:
    """지명·역·랜드마크('연남동', '강남역', '파리', '삿포로 오도리 공원')의 좌표를 조회한다. 결과의 lat/lng를
    find_restaurants·recommend_by_taste의 near_lat/near_lng에 넣어 반경 검색한다. 해외 지명이나 'OO역 근처' 질문에 쓴다 (Google Places 호출 1회)."""
    key = os.environ.get("GOOGLE_PLACES_API_KEY")
    if not key:
        return dumps({"error": "GOOGLE_PLACES_API_KEY가 없어 좌표를 조회할 수 없습니다"})
    r = requests.post("https://places.googleapis.com/v1/places:searchText", timeout=15,
                      headers={"X-Goog-Api-Key": key, "X-Goog-FieldMask": "places.displayName,places.formattedAddress,places.location",
                               "Content-Type": "application/json"},
                      json={"textQuery": place, "languageCode": "ko", "pageSize": 1})
    if r.status_code != 200:
        return dumps({"error": f"Places 호출 실패 ({r.status_code})"})
    places = r.json().get("places") or []
    if not places:
        return dumps({"error": f"'{place}'의 위치를 찾지 못했습니다"})
    p = places[0]
    return dumps({"name": p["displayName"]["text"], "address": p.get("formattedAddress"),
                  "lat": p["location"]["latitude"], "lng": p["location"]["longitude"]})


TOOLS = [find_restaurants, recommend_by_taste, get_restaurant_info, geocode_place]
