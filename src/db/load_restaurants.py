"""restaurants_info.xlsx -> PostgreSQL 적재 (restaurant_schema.sql 기준, 재실행해도 중복 없음)

사용: python load_restaurants.py restaurants_info.xlsx "postgresql://user:pw@host/db"
필요: pip install openpyxl psycopg2-binary
"""
import re
import sys
from datetime import time
from urllib.parse import parse_qs, urlparse

import openpyxl
import psycopg2

PRICE = {"PRICE_LEVEL_FREE": 0, "PRICE_LEVEL_INEXPENSIVE": 1, "PRICE_LEVEL_MODERATE": 2,
         "PRICE_LEVEL_EXPENSIVE": 3, "PRICE_LEVEL_VERY_EXPENSIVE": 4}
DAYS = {"Sunday": 0, "Monday": 1, "Tuesday": 2, "Wednesday": 3, "Thursday": 4, "Friday": 5, "Saturday": 6}


def s(v):
    """빈 문자열/None -> None, 그 외 strip"""
    if v is None:
        return None
    v = str(v).strip()
    return v or None


def to_bool(v):
    """xlsx 셀의 True/False 또는 'True'/'False' 문자열 -> bool (bool('False') 는 True 라서 문자열을 따로 처리)"""
    if isinstance(v, str):
        return v.strip().lower() in ("true", "1", "yes")
    return bool(v)


def video_id(url):
    return parse_qs(urlparse(url).query)["v"][0]


def cid(maps_url):
    return re.search(r"cid=(\d+)", maps_url).group(1)


# ---- 영업시간 파싱 ---------------------------------------------------------------
_T = re.compile(r"(\d{1,2}):(\d{2})\s*(AM|PM)?")


def _to24(h, m, mer):
    if mer == "PM" and h != 12:
        h += 12
    if mer == "AM" and h == 12:
        h = 0
    return h * 60 + m


def parse_range(rng):
    """'4:30 – 11:00 PM' / '11:30 AM – 3:00 PM' -> (time, time). 시작에 AM/PM이 없으면 더 짧은 구간이 되는 쪽 선택."""
    norm = rng.replace(" ", " ").replace(" ", " ")
    a, b = [x.strip() for x in norm.split("–")]
    ma, mb = _T.fullmatch(a), _T.fullmatch(b)
    if not (ma and mb):
        return None
    end = _to24(int(mb[1]), int(mb[2]), mb[3])
    if ma[3]:
        start = _to24(int(ma[1]), int(ma[2]), ma[3])
    else:
        cands = [_to24(int(ma[1]), int(ma[2]), m) for m in ("AM", "PM")]
        start = min(cands, key=lambda c: (end - c) % 1440 or 1440)
    t = lambda x: time(x // 60 % 24, x % 60)
    return t(start), t(end)


def parse_hours(raw):
    """-> [(day, seq, is_closed, open, close)], 파싱 불가 시 None (raw 는 restaurants 에 보존됨)"""
    out = []
    for part in raw.split(" | "):
        day, _, body = part.partition(": ")
        if day not in DAYS:
            return None
        body = body.strip()
        if body == "Closed":
            out.append((DAYS[day], 0, True, None, None))
            continue
        for seq, rng in enumerate(body.split(", ")):
            r = parse_range(rng)
            if r is None:          # 'Open 24 hours' 등
                return None
            out.append((DAYS[day], seq, False, r[0], r[1]))
    return out


# ---- 적재 -----------------------------------------------------------------------
def main(xlsx, dsn):
    ws = openpyxl.load_workbook(xlsx).active
    rows = list(ws.iter_rows(values_only=True))
    hdr = rows[0]
    conn = psycopg2.connect(dsn)
    cur = conn.cursor()
    skipped_hours = []
    skipped_rows = []
    loaded = 0

    for values in rows[1:]:
        r = dict(zip(hdr, values))
        if not s(r["google_cid"]) and not s(r.get("google_maps_url")):
            # Google 매칭 실패(또는 가게 정보 없음) 행은 식당 마스터 키(google_cid)가 없어 적재할 수 없다.
            skipped_rows.append(s(r["korean_name"]) or f"(가게 정보 없음) {r['video_url']}")
            continue
        loaded += 1
        vid = video_id(r["video_url"])
        types = [t.strip() for t in (s(r["google_types"]) or s(r["google_category_code"]) or "").split(",") if t.strip()]

        cur.execute("""
            INSERT INTO videos (video_id, url, title) VALUES (%s,%s,%s)
            ON CONFLICT (video_id) DO UPDATE SET title = EXCLUDED.title""",
                    (vid, r["video_url"], r["video_title"]))

        gcid = s(r["google_cid"]) or cid(r["google_maps_url"])
        maps_url = s(r.get("google_maps_url")) or f"https://maps.google.com/?cid={gcid}"

        cur.execute("""
            INSERT INTO restaurants (google_cid, google_maps_url, google_place_id, name_official, name_ko,
                country_code, latitude, longitude,
                formatted_address, rating, rating_count, price_level, business_status, phone, website,
                editorial_summary, primary_type, primary_type_label, category_broad, category_detail,
                opening_hours_raw, places_fetched_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s, now())
            ON CONFLICT (google_cid) DO UPDATE SET
                google_place_id = EXCLUDED.google_place_id,
                latitude = EXCLUDED.latitude, longitude = EXCLUDED.longitude,
                rating = EXCLUDED.rating, rating_count = EXCLUDED.rating_count,
                price_level = EXCLUDED.price_level, business_status = EXCLUDED.business_status,
                phone = EXCLUDED.phone, website = EXCLUDED.website,
                opening_hours_raw = EXCLUDED.opening_hours_raw,
                places_fetched_at = now(), updated_at = now()
            RETURNING restaurant_id""",
                    (gcid, maps_url, s(r.get("google_place_id")), r["google_official_name"], s(r["korean_name"]),
                     r["country_code"], s(r.get("google_latitude")), s(r.get("google_longitude")),
                     s(r["google_formatted_address"]), r["google_rating"],
                     r["google_user_rating_count"], PRICE.get(r["google_price_level"]), s(r["google_business_status"]),
                     s(r["google_phone"]), s(r["google_website"]), s(r["google_editorial_summary"]),
                     s(r["google_category_code"]), s(r["google_category"]), s(r["category_broad"]),
                     s(r["category_detail"]), s(r["google_opening_hours"])))
        rid = cur.fetchone()[0]

        cur.execute("""INSERT INTO video_restaurant_mentions (video_id, restaurant_id, extracted_name, extracted_address)
                       VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING""",
                    (vid, rid, s(r["korean_name"]), s(r["address"])))

        for pos, t in enumerate(types):
            cur.execute("""INSERT INTO restaurant_types (restaurant_id, type_code, position) VALUES (%s,%s,%s)
                           ON CONFLICT DO NOTHING""", (rid, t, pos))

        raw = s(r["google_opening_hours"])
        if raw:
            parsed = parse_hours(raw)
            if parsed is None:
                skipped_hours.append(r["korean_name"])
            else:
                cur.execute("DELETE FROM restaurant_hours WHERE restaurant_id = %s", (rid,))
                cur.executemany("""INSERT INTO restaurant_hours
                    (restaurant_id, day_of_week, seq, is_closed, open_time, close_time) VALUES (%s,%s,%s,%s,%s,%s)""",
                                [(rid, *p) for p in parsed])

        cur.execute("""
            INSERT INTO michelin_status (restaurant_id, is_michelin, edition_type, latest_grade, is_active, note)
            VALUES (%s,%s,%s,%s,%s,%s)
            ON CONFLICT (restaurant_id) DO UPDATE SET is_michelin = EXCLUDED.is_michelin,
                edition_type = EXCLUDED.edition_type, latest_grade = EXCLUDED.latest_grade,
                is_active = EXCLUDED.is_active,
                note = EXCLUDED.note, checked_at = now()""",
                    (rid, to_bool(r["is_michelin"]), r["edition_type"], r["latest_grade"], to_bool(r["is_active"]),
                     s(r["note"])))

        for item in (s(r["history"]) or "").split(";"):
            if item.strip():
                edition, grade = item.strip().rsplit(":", 1)
                year = re.match(r"\d{4}", edition)  # '2017 홋카이도 특별판' 같은 에디션명은 앞 4자리 연도만 사용
                if year is None:
                    continue
                cur.execute("""INSERT INTO michelin_records (restaurant_id, year, grade) VALUES (%s,%s,%s)
                               ON CONFLICT (restaurant_id, year) DO UPDATE SET grade = EXCLUDED.grade""",
                            (rid, int(year.group()), grade))

    conn.commit()
    print(f"loaded {loaded} rows; skipped (google_cid 없음): {skipped_rows or 'none'}; hours not parsed for: {skipped_hours or 'none'}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])