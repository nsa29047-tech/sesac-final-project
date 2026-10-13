"""챗봇 에이전트 도구.

- 정형 조건(국가·지역·미슐랭·가격대·평점·영업시간·좌표 반경)은 SQL, 취향(음식·분위기·메뉴)은 벡터 검색이다.
  `find_restaurants`는 SQL만, `recommend_by_taste`는 같은 SQL 조건으로 먼저 좁힌 뒤 벡터 검색(rag_search.search)을 한다.
- 식당 정보는 원본 테이블을 읽기 전용 계정(READONLY_POSTGRES_URI)으로 조회한다.
- 모든 도구는 JSON 문자열을 돌려주고, 결과가 없으면 빈 목록을 돌려준다(에이전트는 "없다"고 답한다).
"""
import json
import os
import re
import sys
import time
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any, List, Literal, Optional

import requests
from langchain_core.messages import HumanMessage
from langchain_core.tools import tool
from langgraph.prebuilt import InjectedState
from openai import OpenAI
from pydantic import BaseModel, Field

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "db"))
import rag_search as rs  # noqa: E402  (connect, search)
from category_rules import BROAD  # noqa: E402  (대분류 목록)

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
    """식당을 좁히는 정형 조건. 사용자가 직접 말한 조건만 채우고, 말하지 않은 조건은 절대 추측해서 넣지 않는다(특히 country_code·region)."""
    country_code: Optional[str] = Field(None, description="ISO 2자리 국가 코드 (사용자가 나라를 말했을 때만. 한국 KR, 일본 JP, 프랑스 FR, 스페인 ES, 미국 US, 이탈리아 IT 등). 홍콩은 HK지만 '홍콩식'은 query에 쓴다")
    exclude_country_code: Optional[str] = Field(None, description="이 국가는 뺀다. '해외'·'한국 말고'는 KR")
    region: Optional[str] = Field(None, description="국내 지역명(서울, 마포구, 연남동, 부산 등). 시/구/동 어느 단계든 된다. 해외 도시나 'OO역 근처'에는 쓰지 말고 city를 쓴다")
    city: Optional[str] = Field(None, description="해외 도시나 역·랜드마크 이름(파리, 도쿄, 삿포로, 바르셀로나, 강남역, 오도리 공원). 사용자가 말한 지명 그대로 쓴다(강남역을 강남으로 줄이지 않는다). 도구가 좌표를 조회해 반경 검색한다. 도시를 말했으면 country_code가 아니라 이것을 쓴다")
    michelin: Optional[List[Literal["BIB_GOURMAND", "1_STAR", "2_STARS", "3_STARS", "SELECTED"]]] = Field(None, description="미슐랭 등급 목록. '미슐랭 스타'는 [1_STAR,2_STARS,3_STARS], '미슐랭'만 말하면 전체 5개")
    min_price_level: Optional[int] = Field(None, ge=0, le=4, description="가격대 하한 (0 무료 ~ 4 매우 비쌈, Google 기준). 비싼·고급·럭셔리는 3")
    max_price_level: Optional[int] = Field(None, ge=0, le=4, description="가격대 상한. 저렴한 곳은 2")
    min_rating: Optional[float] = Field(None, ge=0, le=5, description="Google 평점 하한")
    cuisine: Optional[str] = Field(None, description="음식·가게 종류. 대분류(한식·일식·중식·양식·동남아식·인도식·중동식·기타)는 정확히 비교하고, 그 외 말은 세부 분류(프렌치, 이탈리안, 타파스 바, 고깃집, 해산물, 오마카세 등)와 태그에서 찾는다. 사용자가 말한 종류를 쓰되 시스템 프롬프트의 '식당 분류' 표기에 맞춘다(프랑스 요리는 '프렌치'). 맛·분위기·메뉴 표현에는 쓰지 않는다")
    has_chef: Optional[bool] = Field(None, description="True면 대표 셰프 정보가 있는 식당만")
    course_only: Optional[bool] = Field(None, description="True면 코스 요리 식당만")
    open_day: Optional[int] = Field(None, ge=0, le=6, description="영업 요일 (0=일, 1=월 ... 6=토). open_time과 함께 쓴다")
    open_time: Optional[str] = Field(None, description="'HH:MM' (식당 현지 시간). 이 요일·시각에 영업 중인 식당만. 휴무 요일 자체를 물으면 open_time은 '12:00'처럼 낮 시간을 준다")
    near_lat: Optional[float] = Field(None, description="반경 검색 기준 위도 (geocode_place 결과)")
    near_lng: Optional[float] = Field(None, description="반경 검색 기준 경도")
    radius_km: Optional[float] = Field(None, gt=0, le=50, description="city·near_lat/near_lng 기준 반경(km). 비우면 도시는 20km, 역·랜드마크는 3km")


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
    if f.cuisine in BROAD:   # 대분류(한식·일식·중식·양식·기타 등)는 정확히 비교한다. 태그까지 보면 다른 분류의 식당이 섞인다
        c.append("r.category_broad = %s"); p.append(f.cuisine)
    elif f.cuisine:           # 세부 분류·태그는 한 식당에 하나/여러 개라 부분 일치로 찾는다
        c.append("(r.category_detail ILIKE %s OR EXISTS "
                 "(SELECT 1 FROM restaurant_tags t WHERE t.restaurant_id = r.restaurant_id AND t.tag ILIKE %s))")
        p += [f"%{f.cuisine}%"] * 2
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
        c.append(f"{DIST_SQL} <= %s"); p += [f.near_lat, f.near_lng, f.near_lat, f.radius_km or 2.0]
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
               (SELECT array_to_string((array_agg(t.tag ORDER BY t.tag))[1:8], ', ') FROM restaurant_tags t WHERE t.restaurant_id = r.restaurant_id) AS tags,
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

def last_user_text(state) -> str:
    """대화 상태에서 사용자의 마지막 말. 모델이 요약해서 넘기는 값이 아니라 사용자가 한 말 그대로라 결과 확인(judge_candidates)이 정확하다."""
    try:
        for m in reversed((state or {}).get("messages", [])):
            if isinstance(m, HumanMessage):
                return m.content if isinstance(m.content, str) else ""
    except AttributeError:
        pass
    return ""


class Picked(BaseModel):
    ids: List[int]


def judge_candidates(request: str, restaurants: list) -> list:
    """질문의 조건을 실제로 충족하는 식당만 남긴다. 모델이 음식 종류 같은 필터를 빠뜨려도 엉뚱한 식당이 답변에 나가지 않게 하는 마지막 확인이다.
    SQL로 이미 거른 조건(지역·미슐랭·가격·영업시간·거리)은 충족으로 본다."""
    if not restaurants or not request:
        return restaurants
    shown = "\n".join(f"[{i}] {r['name']} | 분류: {r.get('category_broad') or '-'} / {r.get('category_detail') or '-'} | 태그: {r.get('tags') or '-'} | "
                      f"지역: {r.get('region') or '-'} | 셰프: {r.get('chef_name') or '-'} | {r.get('michelin') or ''}" for i, r in enumerate(restaurants, 1))
    res = OpenAI().beta.chat.completions.parse(
        model=rs.JUDGE_MODEL, temperature=0, seed=42, response_format=Picked,
        messages=[{"role": "system", "content":
                   "사용자 요청에 대해 후보 식당이 실제로 요청에 맞는지 고른다. 지역·미슐랭·가격대·영업시간·거리는 이미 SQL로 걸렀으니 충족으로 본다. "
                   "요청에 음식 종류나 스타일(일식, 한식, 소고기 전문점, 이탈리안 등)이 있을 때만 그것이 후보의 분류·태그와 다른 식당을 제외한다. 요청에 음식 종류가 없으면(예: '빕 구르망 식당', '평점 높은 식당') 모든 후보를 남긴다. 분류 이름 자체를 묻는 요청(예: '기타로 분류된 식당', '일식 식당')은 후보의 대분류·세부 분류가 그 이름과 같으면 충족이다. "
                   "맞는 식당의 번호만 ids에 넣고, 하나도 없으면 빈 배열을 준다."},
                  {"role": "user", "content": f"요청: {request}\n\n{shown}"}])
    keep = set(res.choices[0].message.parsed.ids)
    return [r for i, r in enumerate(restaurants, 1) if i in keep]


class FindArgs(FilterArgs):
    state: Annotated[Any, InjectedState] = Field(default=None, description="(자동 주입) 대화 상태. 모델이 채우지 않는다")   # 사용자의 마지막 말을 도구가 직접 읽어 결과 확인에 쓴다
    sort_by: Literal["rating", "price_low", "price_high", "distance"] = Field("rating", description="정렬: 평점 높은 순 / 가격 낮은 순 / 가격 높은 순 / 가까운 순(near_lat·near_lng 필요)")
    limit: int = Field(5, ge=1, le=MAX_RESULTS, description="돌려줄 식당 수")


@tool(args_schema=FindArgs)
def find_restaurants(sort_by: str = "rating", limit: int = 5, state: Any = None, **filters) -> str:
    """정형 조건(국가·지역·미슐랭·가격대·평점·음식 분류·영업 요일/시간·좌표 반경)만으로 식당을 찾는다. 분위기·메뉴·스타일(파인다이닝, 아늑한 등) 같은 취향 표현이 없는 질문에만 쓴다.
    취향 조건이 있으면 recommend_by_taste를 쓴다."""
    f = FilterArgs(**filters)
    request = last_user_text(state)
    if not f.michelin and re.search(r"미슐랭|미쉐린|michelin", request, re.I) and not re.search(r"아닌|말고|제외|없는", request):
        f.michelin = ["BIB_GOURMAND", "1_STAR", "2_STARS", "3_STARS", "SELECTED"]   # 모델이 michelin 을 빈 목록/None으로 빼먹어도 미슐랭 질문이면 등재 식당으로 거른다
    if err := resolve_city(f):
        return err
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
                     (*params, *order_params, max(limit * 3, 15)))   # 확인 단계에서 걸러질 것을 감안해 넉넉히 가져온다
    found = summarize([r["restaurant_id"] for r in rows])
    results = judge_candidates(request, found)[:limit]
    if found and not results:
        return dumps({"count": 0, "restaurants": [], "note": f"조건에 맞는 식당이 없습니다(후보 {len(found)}곳은 요청한 종류와 달라 제외)"})
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
    if err := resolve_city(f):
        return err
    if f.cuisine:   # 음식 스타일은 벡터 검색어에 합친다(분류 칼럼으로 거르면 표기가 달라 후보가 사라진다)
        query, f.cuisine = f"{f.cuisine} {query}", None
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


AREA_TYPES = {"locality", "administrative_area_level_1", "administrative_area_level_2", "country"}


GEOCODE_CACHE = Path(__file__).resolve().parents[2] / "data" / "geocode_cache.json"   # data/ 는 git 제외. 같은 지명을 다시 조회하지 않는다


def _cache_load() -> dict:
    try:
        return json.loads(GEOCODE_CACHE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


@lru_cache(maxsize=256)
def geocode(place: str) -> dict:
    """Places Text Search로 좌표를 조회한다. 성공한 결과는 파일에 캐시하고(Places 호출 비용·한도 절약), 429는 잠깐 쉬고 재시도한다. 실패하면 {"error": ...}."""
    cached = _cache_load().get(place)
    if cached:
        return cached
    key = os.environ.get("GOOGLE_PLACES_API_KEY")
    if not key:
        return {"error": "GOOGLE_PLACES_API_KEY가 없어 좌표를 조회할 수 없습니다"}
    for attempt in range(3):
        r = requests.post("https://places.googleapis.com/v1/places:searchText", timeout=15,
                          headers={"X-Goog-Api-Key": key, "X-Goog-FieldMask": "places.displayName,places.formattedAddress,places.location,places.types",
                                   "Content-Type": "application/json"},
                          json={"textQuery": place, "languageCode": "ko", "pageSize": 1})
        if r.status_code != 429:
            break
        time.sleep(2 * (attempt + 1))
    if r.status_code != 200:
        return {"error": f"Places 호출 실패 ({r.status_code})"}
    places = r.json().get("places") or []
    if not places:
        return {"error": f"'{place}'의 위치를 찾지 못했습니다"}
    p = places[0]
    result = {"name": p["displayName"]["text"], "address": p.get("formattedAddress"), "lat": p["location"]["latitude"],
              "lng": p["location"]["longitude"], "is_area": bool(AREA_TYPES & set(p.get("types", [])))}
    try:
        cache = _cache_load()
        cache[place] = result
        GEOCODE_CACHE.parent.mkdir(exist_ok=True)
        GEOCODE_CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")
    except OSError:
        pass
    return result


def resolve_city(f: FilterArgs):
    """f.city가 있으면 좌표로 바꿔 near_lat/near_lng/radius_km를 채운다(모델이 geocode 도구를 따로 부르지 않아도 된다). 실패하면 오류 JSON."""
    if not f.city or f.near_lat is not None:
        return None
    g = geocode(f.city)
    if "error" in g:
        return dumps(g)
    f.near_lat, f.near_lng = g["lat"], g["lng"]
    if f.radius_km is None:
        f.radius_km = 20.0 if g["is_area"] else 3.0
    return None


@tool
def geocode_place(place: str) -> str:
    """지명·역·랜드마크의 좌표만 알고 싶을 때 쓴다. 식당 검색에는 find_restaurants·recommend_by_taste의 city 인자를 쓰면 좌표 조회가 자동으로 된다 (Google Places 호출 1회)."""
    g = geocode(place)
    return dumps({k: v for k, v in g.items() if k != "is_area"})


@lru_cache(maxsize=1)
def category_section() -> str:
    """시스템 프롬프트에 넣을 식당 분류 목록(대분류별 세부 분류와 식당 수). DB 조회에 실패하면 빈 문자열."""
    try:
        rows = run_query("SELECT category_broad AS b, category_detail AS d, count(*) AS n FROM restaurants WHERE category_broad IS NOT NULL "
                         "GROUP BY 1, 2 ORDER BY 1, 3 DESC, 2")
    except Exception:
        return ""
    by = {}
    for r in rows:
        by.setdefault(r["b"], []).append(f"{r['d']}({r['n']})" if r["d"] else None)
    lines = [f"- {b}: " + ", ".join(x for x in v if x) for b, v in by.items()]
    return "\n".join(lines)


TOOLS = [find_restaurants, recommend_by_taste, get_restaurant_info]   # geocode_place는 city 인자가 대신하므로 모델에 노출하지 않는다
