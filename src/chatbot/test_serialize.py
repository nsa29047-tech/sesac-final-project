"""serialize.py / schemas.py 시험. 실행: uv run python src/chatbot/test_serialize.py [--no-db]

- 단위 시험: DB 없이 가짜 행으로 변환 규칙(region·tags·michelin·hours·price)을 확인한다.
- DB 시험(기본): 실제 식당으로 summarize/fetch_detail 결과를 변환해 스키마 검증과 JSON 직렬화를 확인한다(읽기 전용 계정 사용, 외부 API 호출 없음).
"""
import json
import sys
from datetime import time
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import find_dotenv, load_dotenv  # noqa: E402

load_dotenv(find_dotenv(usecwd=True))

from chatbot import serialize as sz  # noqa: E402


def as_json(model) -> dict:
    data = model.model_dump(mode="json")
    json.dumps(data, ensure_ascii=False)   # 직렬화가 되는지(Decimal·time 같은 값이 남아 있지 않은지)
    return data


# ---------------------------------------------------------------------------------------------- 단위 시험
def test_to_restaurant_converts_strings():
    raw = {"restaurant_id": 1, "name": "밍글스", "name_official": "Mingles", "country_code": "KR",
           "region": "서울특별시 > 강남구", "address": "서울 강남구", "category_broad": "한식", "category_detail": "모던 한식",
           "price_level": 4, "price_min": Decimal("100000"), "price_max": None, "price_currency": "KRW",
           "rating": Decimal("4.6"), "rating_count": 1200, "chef_name": "강민구", "google_maps_url": "https://maps/x",
           "latitude": Decimal("37.52"), "longitude": Decimal("127.04"), "tags": "코스, 모던, 한식", "michelin": "미슐랭 3스타",
           "videos": "제목 | 일부 https://www.youtube.com/watch?v=aaaaaaaaaaa",   # LLM용 문자열은 무시한다
           "matched": [{"type": "FOOD", "item": "육회", "content": "설명"}], "notes": [{"x": 1}]}
    d = as_json(sz.to_restaurant(raw))
    assert d["region"] == ["서울특별시", "강남구"]
    assert d["tags"] == ["코스", "모던", "한식"]
    assert d["michelin"] == {"grade": "3_STARS", "label": "미슐랭 3스타"}
    assert d["price"] == {"min": 100000.0, "max": None, "currency": "KRW"}
    assert d["rating"] == 4.6 and d["latitude"] == 37.52
    assert d["videos"] == []   # videos 는 따로 조회해서 넘긴다
    assert d["matched"][0]["content"] == "설명"


def test_michelin_label_mapping():
    assert sz._michelin("미슐랭 아님(Parse 기준)") is None
    assert sz._michelin("미슐랭 아님") is None
    assert sz._michelin(None) is None
    assert sz._michelin("빕 구르망").grade == "BIB_GOURMAND"
    assert sz._michelin("미슐랭 등재(등급 미확인)").grade == "UNKNOWN"


def test_missing_optional_fields():
    d = as_json(sz.to_restaurant({"restaurant_id": 2, "name": None, "name_official": "Only Official"}))
    assert d["name"] == "Only Official"
    assert d["region"] == [] and d["tags"] == [] and d["price"] is None and d["michelin"] is None


def test_to_hours():
    rows = [{"day_of_week": 1, "open_time": time(11, 30), "close_time": time(15, 0)},
            {"day_of_week": 1, "open_time": time(18, 0), "close_time": time(22, 0)},
            {"day_of_week": 0, "open_time": None, "close_time": None}]
    hours = [h.model_dump() for h in sz.to_hours(rows)]
    assert hours[0] == {"day": 0, "closed": True, "intervals": []}
    assert hours[1]["intervals"] == [{"open": "11:30", "close": "15:00"}, {"open": "18:00", "close": "22:00"}]


# ---------------------------------------------------------------------------------------------- DB 시험
def test_db_cards_and_detail():
    from chatbot.detail import fetch_detail
    from chatbot.tools import run_query, summarize

    # 제목에 " | " 가 있는 영상(문자열로는 쪼갤 수 없다)이 있는 식당을 포함해 여러 곳을 본다
    pipe = run_query("""SELECT DISTINCT n.restaurant_id FROM video_restaurant_notes n JOIN videos v USING (video_id)
                        WHERE v.title LIKE '%%|%%' ORDER BY 1 LIMIT 3""")
    others = run_query("SELECT restaurant_id FROM restaurants ORDER BY rating_count DESC NULLS LAST LIMIT 5")
    ids = list(dict.fromkeys([r["restaurant_id"] for r in pipe + others]))
    cards = sz.restaurant_cards(summarize(ids))
    assert [c.restaurant_id for c in cards] == ids   # 순서 유지
    with_video = [c for c in cards if c.videos]
    assert with_video, "영상이 있는 식당이 하나도 없다"
    for c in cards:
        as_json(c)
        for v in c.videos:
            assert len(v.video_id) == 11 and v.url.startswith("http") and v.thumbnail.endswith(f"/{v.video_id}/mqdefault.jpg")
    pipe_cards = [c for c in cards if c.restaurant_id in {r["restaurant_id"] for r in pipe}]
    assert any("|" in v.title for c in pipe_cards for v in c.videos), "제목에 | 가 있는 영상이 구조화되어 나와야 한다"

    rid = with_video[0].restaurant_id
    detail = sz.to_detail(fetch_detail(rid))
    d = as_json(detail)
    assert d["restaurant_id"] == rid and d["notes"] and d["menus"] is not None
    assert {m["item_type"] for m in d["menus"]} <= {"FOOD", "DRINK"}
    assert all(set(h) == {"day", "closed", "intervals"} for h in d["hours"])
    print(f"   상세 예: {d['name']} 메뉴 {len(d['menus'])}개, 노트 {len(d['notes'])}개, 영업시간 {len(d['hours'])}일, 영상 {len(d['videos'])}개")


UNIT = [test_to_restaurant_converts_strings, test_michelin_label_mapping, test_missing_optional_fields, test_to_hours]
DB = [test_db_cards_and_detail]

if __name__ == "__main__":
    tests = UNIT if "--no-db" in sys.argv else UNIT + DB
    failed = 0
    for t in tests:
        try:
            t()
            print(f"ok    {t.__name__}")
        except Exception as e:
            failed += 1
            print(f"FAIL  {t.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} 통과")
    sys.exit(1 if failed else 0)
