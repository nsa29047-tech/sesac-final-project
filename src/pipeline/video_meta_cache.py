"""yt-dlp로 가져온 영상 제목·소개란을 data/descriptions/{video_id}.json에 캐시한다.

channel_video_filter.py(2단계)가 가져온 소개란을 extract_restaurant_info.py(3단계)가 다시 쓰도록 해서
같은 영상에 yt-dlp를 두 번 호출하지 않게 한다.
"""
import json
import re
from pathlib import Path
from typing import Optional

import yt_dlp

ROOT = Path(__file__).resolve().parents[2]
CACHE_DIR = ROOT / "data" / "descriptions"

_ID_PATTERN = re.compile(r"(?:v=|youtu\.be/|shorts/)([A-Za-z0-9_-]{11})")


def video_id_from_url(url: str) -> Optional[str]:
    m = _ID_PATTERN.search(url)
    return m.group(1) if m else None


def _cache_path(url: str) -> Optional[Path]:
    vid = video_id_from_url(url)
    return CACHE_DIR / f"{vid}.json" if vid else None


def get_video_meta(url: str, ydl: Optional[yt_dlp.YoutubeDL] = None) -> dict:
    """{'title', 'description', 'cached'}를 돌려준다. 캐시가 있으면 yt-dlp를 호출하지 않는다.

    'cached'는 호출하는 쪽이 요청 사이 대기를 건너뛸지 판단하는 데 쓴다.
    """
    path = _cache_path(url)
    if path and path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return {"title": data.get("title", ""), "description": data.get("description") or "", "cached": True}
        except (json.JSONDecodeError, OSError):
            pass  # 깨진 캐시는 무시하고 다시 가져온다

    opts = {"skip_download": True, "extract_flat": False, "quiet": True, "no_warnings": True}
    if ydl is not None:
        info = ydl.extract_info(url, download=False)
    else:
        with yt_dlp.YoutubeDL(opts) as tmp:
            info = tmp.extract_info(url, download=False)
    meta = {"title": info.get("title", ""), "description": info.get("description") or ""}

    if path:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    return {**meta, "cached": False}
