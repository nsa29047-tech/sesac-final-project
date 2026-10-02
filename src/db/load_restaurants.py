"""restaurants_info.xlsx -> PostgreSQL 적재 (restaurant_schema.sql 기준, 재실행해도 중복 없음)

사용: uv run python src/db/load_restaurants.py data/restaurants_info.xlsx [DSN]
      DSN 을 생략하면 POSTGRES_URI 환경변수(.env)를 쓴다(접속 문자열이 명령줄·로그에 남지 않도록 생략을 권장).
필요: pip install openpyxl psycopg2-binary python-dotenv
"""
import re
import sys
from datetime import time
from urllib.parse import parse_qs, urlparse

import openpyxl
import psycopg2

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
    pieces = [x.strip() for x in norm.split("–")]
    if len(pieces) != 2:   # 구분자가 없는 형식은 파싱하지 않는다
        return None
    a, b = pieces
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
    """-> [(day, seq, open, close)] (휴무는 open/close 가 None), 파싱 불가 시 None (원문은 CSV의 google_opening_hours 에 남아 있다)"""
    out = []
    for part in raw.split(" | "):
        day, _, body = part.partition(": ")
        if day not in DAYS:
            return None
        body = body.strip()
        if body == "Closed":
            out.append((DAYS[day], 0, None, None))
            continue
        if body == "Open 24 hours":   # 하루 종일 영업: 영업 중 여부 계산이 되도록 00:00~23:59 로 저장한다
            out.append((DAYS[day], 0, time(0, 0), time(23, 59)))
            continue
        for seq, rng in enumerate(body.split(", ")):
            r = parse_range(rng)
            if r is None:          # 'Open 24 hours' 등
                return None
            out.append((DAYS[day], seq, r[0], r[1]))
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
    unchecked_michelin = []
    loaded = 0

    for values in rows[1:]:
        r = dict(zip(hdr, values))
        if not s(r["google_cid"]) and not s(r.get("google_maps_url")):
            # Google 매칭 실패(또는 가게 정보 없음) 행은 식당 마스터 키(google_cid)가 없어 적재할 수 없다.
            skipped_rows.append(s(r["korean_name"]) or f"(가게 정보 없음) {r['video_url']}")
            continue
        loaded += 1
        vid = video_id(r["video_url"])

        cur.execute("""
            INSERT INTO videos (video_id, url, title) VALUES (%s,%s,%s)
            ON CONFLICT (video_id) DO UPDATE SET title = EXCLUDED.title""",
                    (vid, r["video_url"], r["video_title"]))

        gcid = s(r["google_cid"]) or cid(r["google_maps_url"])
        maps_url = s(r.get("google_maps_url")) or f"https://maps.google.com/?cid={gcid}"

        cur.execute("""
            INSERT INTO restaurants (google_cid, google_maps_url, google_place_id, name_official, name_ko,
                country_code, latitude, longitude,
                formatted_address, rating, rating_count, business_status, phone, website,
                places_fetched_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s, now())
            ON CONFLICT (google_cid) DO UPDATE SET
                google_place_id = EXCLUDED.google_place_id,
                latitude = EXCLUDED.latitude, longitude = EXCLUDED.longitude,
                rating = EXCLUDED.rating, rating_count = EXCLUDED.rating_count,
                business_status = EXCLUDED.business_status,
                phone = EXCLUDED.phone, website = EXCLUDED.website,
                places_fetched_at = now(), updated_at = now()
            RETURNING restaurant_id""",
                    (gcid, maps_url, s(r.get("google_place_id")), r["google_official_name"], s(r["korean_name"]),
                     r["country_code"], s(r.get("google_latitude")), s(r.get("google_longitude")),
                     s(r["google_formatted_address"]), r["google_rating"],
                     r["google_user_rating_count"], s(r["google_business_status"]),
                     s(r["google_phone"]), s(r["google_website"])))
        rid = cur.fetchone()[0]

        cur.execute("""INSERT INTO video_restaurant_mentions (video_id, restaurant_id, extracted_name, extracted_address)
                       VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING""",
                    (vid, rid, s(r["korean_name"]), s(r["address"])))

        raw = s(r["google_opening_hours"])
        if raw:
            parsed = parse_hours(raw)
            if parsed is None:
                skipped_hours.append(r["korean_name"])
            else:
                cur.execute("DELETE FROM restaurant_hours WHERE restaurant_id = %s", (rid,))
                cur.executemany("""INSERT INTO restaurant_hours
                    (restaurant_id, day_of_week, seq, open_time, close_time) VALUES (%s,%s,%s,%s,%s)""",
                                [(rid, *p) for p in parsed])

        note = s(r["note"]) or ""
        if not s(r["latest_grade"]) or "미슐랭 미조회" in note or "미슐랭 조회 실패" in note:
            unchecked_michelin.append(s(r["korean_name"]))   # fill_michelin.py 로 채운 뒤 다시 적재하면 michelin_status 가 생긴다
            continue
        is_michelin = to_bool(r["is_michelin"])
        cur.execute("""
            INSERT INTO michelin_status (restaurant_id, is_michelin, latest_grade, note)
            VALUES (%s,%s,%s,%s)
            ON CONFLICT (restaurant_id) DO UPDATE SET is_michelin = EXCLUDED.is_michelin,
                latest_grade = EXCLUDED.latest_grade, note = EXCLUDED.note, checked_at = now()""",
                    (rid, is_michelin, r["latest_grade"], s(r["note"])))

    conn.commit()
    print(f"loaded {loaded} rows; skipped (google_cid 없음): {skipped_rows or 'none'}; hours not parsed for: {skipped_hours or 'none'}; 미슐랭 미확인(michelin_status 미적재): {len(unchecked_michelin)}곳")


if __name__ == "__main__":
    if len(sys.argv) > 2:
        dsn = sys.argv[2]
    else:
        import os
        from dotenv import find_dotenv, load_dotenv
        load_dotenv(find_dotenv(usecwd=True))
        dsn = os.environ["POSTGRES_URI"]
    main(sys.argv[1], dsn)