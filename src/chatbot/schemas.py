"""챗봇 API의 응답 형식(Pydantic). 프론트(Next.js)와 맞추는 계약이며, FastAPI가 이 모델로 검증하고 /docs 를 만든다.

LLM이 보는 도구 결과(JSON)와는 별개다. 도구 결과를 이 형식으로 바꾸는 일은 serialize.py 가 한다.
설계: docs/CHATBOT_API_DESIGN.md 3~4절.
"""
from typing import List, Literal, Optional

from pydantic import BaseModel


class Video(BaseModel):
    video_id: str
    title: str
    url: str
    thumbnail: str   # https://img.youtube.com/vi/{video_id}/mqdefault.jpg


class Price(BaseModel):
    min: float
    max: Optional[float] = None
    currency: Optional[str] = None


class Michelin(BaseModel):
    grade: str   # 1_STAR, 2_STARS, 3_STARS, BIB_GOURMAND, SELECTED, UNKNOWN (미슐랭이 아니면 이 객체 자체가 null)
    label: str   # 화면에 보여 줄 한글 표기 (예: 미슐랭 1스타)


class Match(BaseModel):
    """취향 검색이 이 식당을 고른 근거 청크. 카드의 인용문에 쓴다."""
    type: str   # OVERVIEW, FOOD, DRINK, CHEF
    item: Optional[str] = None
    content: str


class Restaurant(BaseModel):
    restaurant_id: int
    name: str
    name_official: Optional[str] = None
    country_code: Optional[str] = None
    region: List[str] = []   # ["서울특별시", "강남구"]
    address: Optional[str] = None
    category_broad: Optional[str] = None
    category_detail: Optional[str] = None
    tags: List[str] = []
    price_level: Optional[int] = None   # 0~4 (Google 기준, 비교용)
    price: Optional[Price] = None
    rating: Optional[float] = None
    rating_count: Optional[int] = None
    michelin: Optional[Michelin] = None
    chef_name: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    google_maps_url: Optional[str] = None
    videos: List[Video] = []
    matched: List[Match] = []


class HoursInterval(BaseModel):
    open: str    # "HH:MM"
    close: str   # "HH:MM" (자정을 넘기면 open 보다 작을 수 있다)


class DayHours(BaseModel):
    day: int   # 0=일 ... 6=토
    closed: bool
    intervals: List[HoursInterval] = []


class Note(BaseModel):
    video_id: str
    title: str
    url: str
    concept: Optional[str] = None
    atmosphere: Optional[str] = None
    key_points: List[str] = []
    final_review: Optional[str] = None
    is_course: bool = False
    course_name: Optional[str] = None


class Menu(BaseModel):
    video_id: str
    item_type: Literal["FOOD", "DRINK"]
    name: str
    description: Optional[str] = None
    course_stage: Optional[str] = None
    menu_order: Optional[int] = None
    mentioned_sec: Optional[int] = None   # 영상 속 시각(초). 자막에서 추정한 값이라 몇 초 어긋날 수 있다


class RestaurantDetail(Restaurant):
    phone: Optional[str] = None
    website: Optional[str] = None
    chef_info: Optional[str] = None   # 영상에서 한 말이라 검증된 사실이 아니다
    hours: List[DayHours] = []
    notes: List[Note] = []
    menus: List[Menu] = []
