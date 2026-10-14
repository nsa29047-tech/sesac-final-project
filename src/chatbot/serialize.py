"""도구 결과·DB 행 -> API 응답 형식(schemas.py).

LLM이 보는 도구 결과(JSON)는 바꾸지 않고, API로 내보낼 때만 변환한다. 평가셋이 보는 LLM 입력이 달라지지 않게 하려는 것이다.
- 도구 결과의 `videos`는 "제목 url | 제목 url" 문자열이라 제목에 " | "가 있으면 쪼갤 수 없다. 영상은 DB에서 따로 조회한다(restaurant_cards).
- region(" > "로 이은 문자열)·tags(", "로 이은 문자열)·michelin(한글 라벨)은 구조화한 값으로 바꾼다.
"""
from typing import Any, Dict, Iterable, List, Optional

from .schemas import DayHours, HoursInterval, Match, Menu, Michelin, Note, Price, Restaurant, RestaurantDetail, Video
from .tools import GRADE_LABEL, run_query

LABEL_TO_GRADE = {label: grade for grade, label in GRADE_LABEL.items() if grade != "NONE"}


def thumbnail(video_id: str) -> str:
    return f"https://img.youtube.com/vi/{video_id}/mqdefault.jpg"


def to_video(video_id: str, title: str, url: str) -> Video:
    return Video(video_id=video_id, title=title, url=url, thumbnail=thumbnail(video_id))


def _michelin(label: Optional[str]) -> Optional[Michelin]:
    """한글 라벨 -> Michelin. '미슐랭 아님' 계열이거나 모르는 라벨이면 None."""
    grade = LABEL_TO_GRADE.get(label or "")
    return Michelin(grade=grade, label=label) if grade else None


def _split(text: Optional[str], sep: str) -> List[str]:
    return [p.strip() for p in (text or "").split(sep) if p.strip()]


def _tags(value: Any) -> List[str]:
    if isinstance(value, list):
        return [str(t) for t in value]
    return _split(value, ",")


def to_restaurant(raw: Dict[str, Any], videos: Optional[List[Video]] = None) -> Restaurant:
    """summarize()/도구 결과의 식당 dict 하나 -> Restaurant. 모르는 키(notes, menus 등)는 무시한다."""
    price = None
    if raw.get("price_min") is not None:
        price = Price(min=raw["price_min"], max=raw.get("price_max"), currency=raw.get("price_currency"))
    return Restaurant(
        restaurant_id=raw["restaurant_id"],
        name=raw.get("name") or raw.get("name_official") or "",
        name_official=raw.get("name_official"),
        country_code=raw.get("country_code"),
        region=_split(raw.get("region"), ">"),
        address=raw.get("address") or raw.get("formatted_address"),
        category_broad=raw.get("category_broad"),
        category_detail=raw.get("category_detail"),
        tags=_tags(raw.get("tags")),
        price_level=raw.get("price_level"),
        price=price,
        rating=raw.get("rating"),
        rating_count=raw.get("rating_count"),
        michelin=_michelin(raw.get("michelin")),
        chef_name=raw.get("chef_name"),
        latitude=raw.get("latitude"),
        longitude=raw.get("longitude"),
        google_maps_url=raw.get("google_maps_url"),
        videos=videos or [],
        matched=[Match(**m) for m in raw.get("matched") or []],
    )


def videos_of(restaurant_ids: Iterable[int]) -> Dict[int, List[Video]]:
    """식당별 영상 목록(영상 분석 노트가 있는 영상). 식당 여러 곳을 쿼리 한 번으로 가져온다."""
    ids = list(restaurant_ids)
    if not ids:
        return {}
    rows = run_query("""SELECT n.restaurant_id, v.video_id, v.title, v.url
                        FROM video_restaurant_notes n JOIN videos v USING (video_id)
                        WHERE n.restaurant_id = ANY(%s) ORDER BY n.restaurant_id, n.video_id""", (ids,))
    out: Dict[int, List[Video]] = {}
    for r in rows:
        out.setdefault(r["restaurant_id"], []).append(to_video(r["video_id"], r["title"], r["url"]))
    return out


def restaurant_cards(raws: List[Dict[str, Any]]) -> List[Restaurant]:
    """카드·지도용 식당 목록(순서 유지). 영상 목록은 여기서 한 번에 조회한다."""
    by_id = videos_of(r["restaurant_id"] for r in raws)
    return [to_restaurant(r, by_id.get(r["restaurant_id"], [])) for r in raws]


def _hhmm(t: Any) -> str:
    return f"{t:%H:%M}"


def to_hours(rows: List[Dict[str, Any]]) -> List[DayHours]:
    """restaurant_hours 행 -> 요일별 영업시간(일요일 0 ~ 토요일 6 순). 휴무는 open_time 이 NULL 인 행이다."""
    by_day: Dict[int, DayHours] = {}
    for r in rows:
        day = by_day.setdefault(r["day_of_week"], DayHours(day=r["day_of_week"], closed=False))
        if r["open_time"] is None:
            day.closed = True
        else:
            day.intervals.append(HoursInterval(open=_hhmm(r["open_time"]), close=_hhmm(r["close_time"])))
    return [by_day[d] for d in sorted(by_day)]


def to_detail(d: Dict[str, Any]) -> RestaurantDetail:
    """detail.fetch_detail() 결과 -> RestaurantDetail. 영상 목록은 노트가 있는 영상에서 만든다."""
    videos = [to_video(n["video_id"], n["title"], n["url"]) for n in d["notes"]]
    base = to_restaurant(d, videos).model_dump()
    return RestaurantDetail(
        **base,
        phone=d.get("phone"),
        website=d.get("website"),
        chef_info=d.get("chef_info"),
        hours=to_hours(d.get("hours") or []),
        notes=[Note(video_id=n["video_id"], title=n["title"], url=n["url"], concept=n.get("concept"), atmosphere=n.get("atmosphere"),
                    key_points=n.get("key_points") or [], final_review=n.get("final_review"),
                    is_course=bool(n.get("is_course")), course_name=n.get("course_name")) for n in d["notes"]],
        menus=[Menu(video_id=m["video_id"], item_type=m["item_type"], name=m["name"], description=m.get("description"),
                    course_stage=m.get("course_stage"), menu_order=m.get("menu_order"), mentioned_sec=m.get("mentioned_sec"))
               for m in d["menus"]],
    )
