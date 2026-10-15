"""강·하천 같은 선형 지형 검색.

"한강 근처"처럼 지명이 점이 아니라 길게 이어진 선일 때, 지명 좌표 한 점과 반경으로는 식당을 놓친다.
OpenStreetMap(Nominatim)에서 waterway의 선 지오메트리를 받아 식당과 선 사이의 최단 거리로 거른다.
waterway가 아닌 지명(역·건물·도시)은 None을 돌려주므로 호출하는 쪽이 기존 좌표 방식으로 처리한다.
"""
import json
import math
import re
import time
from pathlib import Path
from typing import List, Optional

import requests

SHAPE_CACHE = Path(__file__).resolve().parents[2] / "data" / "shape_cache.json"   # data/ 는 git 제외
USER_AGENT = "sesac-final-project-demo"   # Nominatim 이용 정책: 식별 가능한 User-Agent, 초당 1회 이하
_last_call = 0.0


def _cache_load() -> dict:
    try:
        return json.loads(SHAPE_CACHE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _cache_save(cache: dict) -> None:
    try:
        SHAPE_CACHE.parent.mkdir(exist_ok=True)
        SHAPE_CACHE.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


def lookup_lines(name: str, country_code: Optional[str] = None) -> Optional[List[List[List[float]]]]:
    """지명이 강·하천(waterway)이면 선 목록([[lng, lat], ...]의 리스트)을, 아니면 None을 돌려준다.
    조회에 실패하면(네트워크·한도) 캐시하지 않고 None이라 다음에 다시 시도한다."""
    global _last_call
    name = re.sub(r"\s+(강|천|하천)$", lambda m: m.group(1), name.strip())   # "센 강"은 엉뚱한 곳이 나오고 "센강"은 강으로 조회된다
    key = f"{name}|{(country_code or '').lower()}"
    cache = _cache_load()
    if key in cache:
        return cache[key]
    wait = 1.1 - (time.time() - _last_call)
    if wait > 0:
        time.sleep(wait)
    params = {"q": name, "format": "jsonv2", "polygon_geojson": 1, "polygon_threshold": 0, "limit": 1, "accept-language": "ko"}
    if country_code:
        params["countrycodes"] = country_code.lower()
    try:
        r = requests.get("https://nominatim.openstreetmap.org/search", params=params, headers={"User-Agent": USER_AGENT}, timeout=30)
        _last_call = time.time()
        if r.status_code != 200:
            return None
        results = r.json()
    except (requests.RequestException, ValueError):
        return None
    lines = None
    if results and results[0].get("category") == "waterway":
        g = results[0].get("geojson") or {}
        if g.get("type") == "LineString":
            lines = [g["coordinates"]]
        elif g.get("type") == "MultiLineString":
            lines = g["coordinates"]
    cache[key] = lines   # 강이 아닌 지명도 None으로 캐시해 같은 질문에서 다시 조회하지 않는다
    _cache_save(cache)
    return lines


def bbox(lines, margin_km: float):
    """선 전체의 경계 상자 (min_lat, max_lat, min_lng, max_lng). 후보 식당을 DB에서 미리 줄이는 데 쓴다."""
    lngs = [p[0] for l in lines for p in l]
    lats = [p[1] for l in lines for p in l]
    dlat = margin_km / 110.57
    dlng = margin_km / (111.32 * math.cos(math.radians(sum(lats) / len(lats))))
    return min(lats) - dlat, max(lats) + dlat, min(lngs) - dlng, max(lngs) + dlng


def distance_km(lat: float, lng: float, lines) -> float:
    """점과 선들 사이의 최단 거리(km). 점 근처를 평면으로 펴는 근사라 수십 km 규모에서 오차가 작다."""
    kx, ky = 111.32 * math.cos(math.radians(lat)), 110.57
    best = float("inf")
    for line in lines:
        for (x1, y1), (x2, y2) in zip(line, line[1:]):
            ax, ay, bx, by = (x1 - lng) * kx, (y1 - lat) * ky, (x2 - lng) * kx, (y2 - lat) * ky
            dx, dy = bx - ax, by - ay
            seg = dx * dx + dy * dy
            t = 0.0 if seg == 0 else max(0.0, min(1.0, -(ax * dx + ay * dy) / seg))
            best = min(best, math.hypot(ax + t * dx, ay + t * dy))
    return best
