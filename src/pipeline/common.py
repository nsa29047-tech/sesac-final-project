"""자막 추출 단계(extract_script_split, postprocess_split)가 함께 쓰는 공통 코드.

예전에는 이 코드가 extract_script_notes / compare_prompt_v3 / extract_script_v7 / extract_script_course / chunk_transcripts 에
흩어져 있어서 실험 파일을 지울 수 없었다. 지금 파이프라인이 실제로 쓰는 것만 모았다.

- 설정: 경로, 모델, 가격, OpenAI 클라이언트
- 가게 목록: load_stores, describe
- 시각: hms_to_sec, fix_times, TIME_RULES
- 문자열: key_of, strip_generic(평가어 제거), Drink 스키마
- 영상 구간 나누기: video_parts (여러 가게 영상을 가게별 자막 구간으로 나눔)
- 가게 단위 판단: detect_course(코스 여부), classify_cuisine(음식 분류), chefs_for_store(셰프)
"""
import csv
import json
import os
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Literal, Optional

from dotenv import load_dotenv
from openai import OpenAI
from pydantic import BaseModel, Field

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "db"))   # category_rules (분류 표기 목록·정리 규칙)
from category_rules import VOCAB  # noqa: E402
from get_transcripts_since import OUTPUT_DIR as TRANSCRIPT_DIR, format_timestamp

load_dotenv()

ROOT = Path(__file__).resolve().parents[2]
STORES_CSV = ROOT / "data" / "restaurants_info.csv"
# 대상 영상 ID 목록. 2024년 영상까지 넓힐 때는 환경변수 TARGETS_FILE=data/urls/transcript_targets_20240101.txt 로 바꾼다.
TARGETS_FILE = Path(os.getenv("TARGETS_FILE") or ROOT / "data" / "urls" / "transcript_targets_20250101.txt")
MODEL = "gpt-4o-mini"
PRICE_PER_M = (0.15, 0.60)  # (입력, 출력) USD / 1M 토큰
SNAP_TOLERANCE_SEC = 5  # 모델이 적은 시각이 실제 자막 시각과 이만큼 이내로 어긋나면 가장 가까운 자막 시각으로 맞춘다
MAX_CONSECUTIVE_FAILS = 3

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))


# -------------------------------------------------------------
# 1. 가게 목록
# -------------------------------------------------------------
def video_id_of(url: str) -> str:
    return re.search(r"v=([0-9A-Za-z_-]{11})", url).group(1)


def load_stores() -> Dict[str, List[dict]]:
    """대상 영상별 가게 목록(google_cid 있는 가게만, 영상 내 중복 cid 제거)."""
    targets = {l.strip() for l in TARGETS_FILE.read_text(encoding="utf-8").splitlines() if l.strip()}
    by_video: Dict[str, List[dict]] = defaultdict(list)
    seen = set()
    with open(STORES_CSV, "r", encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            vid = video_id_of(row["video_url"])
            cid = row["google_cid"].strip()
            if vid in targets and row["korean_name"].strip() and cid and (vid, cid) not in seen:
                seen.add((vid, cid))
                by_video[vid].append(row)
    return by_video


def describe(store: dict) -> str:
    return " / ".join(p for p in (store["korean_name"], store["address"]) if p)


def describe_full(store: dict) -> str:
    """구간 나누기용. 자막에서는 공식 상호명으로 불리는 경우가 있어 google_official_name 도 함께 준다."""
    return " / ".join(p for p in (store["korean_name"], store["google_official_name"], store["address"]) if p)


# -------------------------------------------------------------
# 2. 시각
# -------------------------------------------------------------
TIME_RULES = """
[메뉴 시각]
- 자막은 "[HH:MM:SS] 텍스트" 형식이다. 메뉴마다 mentioned_at에 그 음식이 실제로 나오거나 먹는 장면으로 판단되는 자막의 시각을 적는다.
- "나왔습니다", "나왔어요", "먹어볼게요", "한입", "서빙" 같은 서빙·시식 단서가 있는 자막의 시각을 우선하고 time_basis를 served_cue로 한다.
- 주문하면서 이름만 말하는 구간, 영상 초반에 오늘 먹을 메뉴를 요약하는 구간, 다른 식당과 비교하는 언급은 음식이 나온 시점이 아니다. 피한다.
- 서빙·시식 단서가 없고 이름 언급만 있으면 처음 언급된 자막의 시각을 적고 time_basis를 mention_only로 한다.
- 시각은 자막에 실제로 있는 [HH:MM:SS]를 그대로 복사한다. 계산하거나 지어내지 않는다. 근거가 없으면 mentioned_at은 null, time_basis는 none이다.
- 메뉴의 evidence에는 근거 자막 원문만 적고 시각은 붙이지 않는다.
"""


def hms_to_sec(text: Optional[str]) -> Optional[int]:
    m = re.fullmatch(r"\s*\[?(\d{1,2}):(\d{2}):(\d{2})\]?\s*", text or "")
    return int(m[1]) * 3600 + int(m[2]) * 60 + int(m[3]) if m else None


def fix_times(notes: dict, starts: List[int]) -> int:
    """모델이 적은 mentioned_at 을 실제 자막 시각에 맞춘다. 맞출 수 없으면 null. 반환값은 버려진 시각 수."""
    dropped = 0
    for menu in notes["menus"]:
        if menu.get("mentioned_at") is None:
            menu["time_basis"] = "none"
            continue
        sec = hms_to_sec(menu["mentioned_at"])
        nearest = min(starts, key=lambda s: abs(s - sec)) if sec is not None and starts else None
        if nearest is None or abs(nearest - sec) > SNAP_TOLERANCE_SEC:
            menu["mentioned_at"], menu["time_basis"] = None, "none"
            dropped += 1
        else:
            menu["mentioned_at"] = f"{nearest // 3600:02d}:{nearest % 3600 // 60:02d}:{nearest % 60:02d}"
    return dropped


# -------------------------------------------------------------
# 3. 문자열 규칙
# -------------------------------------------------------------
class Drink(BaseModel):
    name: str
    review: Optional[str] = Field(None, description="페어링이나 시음 평")


def key_of(name: str) -> str:
    """이름 비교용 키: 한글·영문·숫자만 남긴다."""
    return re.sub(r"[^0-9A-Za-z가-힣]", "", name)


EVAL_WORD = r"(?:맛있|훌륭|좋|최고|완벽|대단|괜찮|죽인|죽여|죽이|미쳤|대박|인상적)[가-힣]*"
EVAL_NOUN = r"(?:좋은|훌륭한|맛있는|대단한|괜찮은|최고의|멋진)\s*[가-힣]{1,6}\s*(?:이다|이에요|입니다|이네요|이야)"  # "좋은 술이다" 같은 평가형
INTENSIFIER = r"(?:(?:정말|진짜|너무|아주|매우|되게|역시|엄청|딱|완전|몹시)\s*)?"
EVAL_ATTR = rf"[가-힣 ]{{1,12}}(?:이|가|은|는|도)\s*{INTENSIFIER}(?:{EVAL_WORD})"  # "농도가 딱 좋다" 같은 속성+평가어
EVAL_TASTE = r"(?:계속 찾게 되는|처음 먹어보는|처음 느껴보는|처음 접하는|잊을 수 없는|특별한|독특한)\s*맛(?:이다|이에요|입니다)"  # 단맛·짠맛 같은 구체적 맛은 건드리지 않는다
EVAL_MISC = rf"(?:{INTENSIFIER}{EVAL_WORD}\s*(?:맛|느낌|것)(?:이다|이에요|입니다)|맛이\s*(?:남다르|다르|특별하)[가-힣]*|맛있어\s*보[가-힣]*|유명한\s*[가-힣]{{1,8}}(?:이다|이에요|입니다)|다양한\s*맛이\s*조화[가-힣]*)"  # "완벽한 맛이다", "맛이 다르다" 같은 내용 없는 평가
INTENSIFIER_ANY = r"(?:정말|진짜|너무|아주|매우|되게|엄청|딱|완전|몹시)\s+"


def strip_generic(text: Optional[str]) -> Optional[str]:
    """끝에 붙은 평가어("~하고 맛있다")를 걷어내고, 평가어만 남으면 None 으로 만든다."""
    if not text:
        return None
    t = text.strip()
    t = re.sub(rf"(?:고|며|으며|면서|지만)\s*{INTENSIFIER}{EVAL_WORD}[.!?\s]*$", "다.", t)
    if re.fullmatch(rf"\s*{INTENSIFIER}(?:{EVAL_WORD}|{EVAL_NOUN}|{EVAL_ATTR}|{EVAL_TASTE}|{EVAL_MISC})[.!?\s]*", t):
        return None
    return re.sub(INTENSIFIER_ANY, "", t)


# -------------------------------------------------------------
# 4. 영상 구간 나누기 (여러 가게 영상을 가게별 자막 구간으로)
# -------------------------------------------------------------
BOUNDARY_DIR = ROOT / "data" / "transcript_chunks" / "_boundaries"  # 구간 경계 캐시. 재실행 시 다시 호출하지 않는다
DESC_DIR = ROOT / "data" / "transcript_chunks" / "_descriptions"  # 구간 나누기 힌트로 쓰는 영상 소개란(yt-dlp로 한 번만 받아 저장)
MIN_SEGMENT_SEC = 30  # 이보다 짧은 구간은 가게 소개가 아니라 영상 끝 요약 같은 한두 줄이라 앞 구간에 합친다
REFINE_BEFORE_SEC, REFINE_AFTER_SEC = 90, 10


class Segment(BaseModel):
    store: int = Field(description="이 구간에서 소개하는 가게 번호(사용자가 준 번호 그대로)")
    start: str = Field(description="이 구간이 시작되는 자막 줄 앞의 [HH:MM:SS]를 그대로 복사한다(대괄호 제외)")


class Segments(BaseModel):
    segments: List[Segment] = Field(default_factory=list)


BOUNDARY_PROMPT = """너는 여러 식당을 소개하는 유튜브 영상의 자막을 식당별 구간으로 나누는 편집자야. 자막은 "[HH:MM:SS] 텍스트" 형식이고, 영상에서 소개하는 가게 목록이 번호와 함께 주어진다.

규칙:
- 영상이 한 가게를 소개하다가 다른 가게로 넘어가는 지점마다 구간을 나눈다. 구간은 시간 순서대로 적고, 각 구간에 그 구간에서 소개하는 가게 번호와 시작 시각을 적는다.
- 같은 가게가 나중에 다시 나오면 구간을 새로 만들어 같은 번호를 적는다.
- 시작 시각은 자막에 실제로 있는 [HH:MM:SS]를 그대로 복사한다. 계산하거나 지어내지 않는다.
- 영상 초반의 인사·전체 소개는 첫 번째 가게 구간에 포함한다. 가게가 바뀌는 지점은 새 가게를 처음 언급하거나 이동·입장하는 자막으로 본다.
- 목록의 모든 가게가 최소 한 번은 구간으로 나와야 한다.
- 영상 소개란이 함께 주어지면 챕터 시각("00:28 가게 이름")과 가게 정보를 힌트로 쓴다. 챕터 시각은 영상 편집 기준이라 자막 시각과 몇 초~수십 초 어긋날 수 있으니, 그 시각 근처의 자막에서 실제로 가게가 바뀌는 줄을 찾아 시작 시각으로 적는다. 소개란에 나온 가게 이름이 자막에서는 비슷한 발음이나 오타로 적혀 있을 수 있다.
- 가게가 아닌 챕터(출발, 이동, 숙소 등)는 바로 앞 가게 구간에 포함한다. 챕터에 없는 이동·이야기도 마찬가지다."""


def get_description(vid: str) -> str:
    """영상 소개란. 처음 한 번만 yt-dlp로 받아 저장한다(API 비용 없음)."""
    path = DESC_DIR / f"{vid}.txt"
    if not path.exists():
        from extract_restaurant_info import get_youtube_description  # 식당 정보 추출 단계에서 쓴 것과 같은 함수
        DESC_DIR.mkdir(parents=True, exist_ok=True)
        path.write_text(get_youtube_description(f"https://www.youtube.com/watch?v={vid}")["description"], encoding="utf-8")
    return path.read_text(encoding="utf-8")


def _norm(text: str) -> str:
    return re.sub(r"[\s\-_.,()\[\]]+", "", (text or "").lower())


def chapter_segments(description: str, stores: List[dict]) -> Optional[List[dict]]:
    """소개란 챕터("MM:SS 제목")에서 가게별 구간을 만든다. 모든 가게가 챕터 제목에 있어야 하고, 하나라도 못 찾으면 None.
    가게가 아닌 챕터(출발, 이동, 숙소 등)는 새 구간을 만들지 않고 앞 가게 구간에 포함한다."""
    chapters = []
    for line in description.splitlines():
        m = re.match(r"\s*(?:(\d{1,2}):)?(\d{1,2}):(\d{2})\s+(.+)$", line)
        if m:
            chapters.append((int(m[1] or 0) * 3600 + int(m[2]) * 60 + int(m[3]), m[4].strip()))
    segs = []
    for sec, title in sorted(chapters):
        for i, st in enumerate(stores):
            names = [_norm(st["korean_name"]), _norm(st["google_official_name"])]
            if any(n and (n in _norm(title) or _norm(title) in n) for n in names):
                if not segs or segs[-1]["store"] != i:
                    segs.append({"store": i, "start_sec": sec})
                break
    if {x["store"] for x in segs} != set(range(len(stores))):
        return None
    return segs


class StartLine(BaseModel):
    line: Optional[int] = Field(None, description="새 가게의 소개가 시작되는 첫 줄 번호(사용자가 준 번호 그대로). 모르겠으면 null")


REFINE_PROMPT = """너는 여러 식당을 소개하는 영상의 자막에서 가게가 바뀌는 지점을 정밀하게 찾는 편집자야. 이전 가게와 새 가게의 이름, 그리고 전환점 근처의 자막 줄이 번호와 함께 주어진다.
새 가게의 소개가 처음 시작되는 줄의 번호를 line 에 적는다.
- 가게 이름이 자막에 처음 나오는 줄이 아니라, 그 앞에서 새 가게로 넘어가는 말(이동·도착, 위치·역사·분위기 소개, 이전 가게 마무리 직후의 새 가게 이야기)이 시작되는 줄이다.
- 이전 가게를 마무리하는 말(총평, 다음 가게로 가자는 말)은 이전 가게 쪽이다. 새 가게 소개가 가게 이름 줄보다 뒤에 시작되면 이름 줄을 고른다.
- 날짜나 시간대가 바뀌는 말("2일차 아침", "점심으로 이동")과 새 장소로 이동·도착하는 말도 새 가게 구간의 시작이다. 이전 가게를 마무리하고 하루를 끝내는 말만 이전 가게 쪽이다.
- 판단이 어려우면 null 을 적는다."""


def refine_starts(stores: List[dict], lines: List[dict], segs: List[dict], usage: list) -> List[dict]:
    """모델이 고른 구간 시작 시각은 가게 이름이 나오는 자막 줄인 경우가 많은데, 실제 소개는 그 앞 20~30초부터 시작한다(예: "구시가지에 있는, 서서 먹는 곳인데"
    다음에 이름 줄). 각 경계 앞 90초 안에서 새 가게 소개가 시작되는 줄을 다시 고르게 해 시작 시각을 앞으로 당긴다. 앞으로 당기는 경우만 받아들인다."""
    out = [dict(segs[0])]
    for prev, seg in zip(segs, segs[1:]):
        t = seg["start_sec"]
        cands = [l for l in lines if max(prev["start_sec"], t - REFINE_BEFORE_SEC) <= l["start"] <= t + REFINE_AFTER_SEC]
        new = dict(seg)
        if len(cands) >= 2:
            shown = "\n".join(f"{i}. [{format_timestamp(l['start'])}] {l['text']}" for i, l in enumerate(cands, 1))
            r = client.beta.chat.completions.parse(
                model=MODEL, temperature=0, seed=42, response_format=StartLine,
                messages=[{"role": "system", "content": REFINE_PROMPT},
                          {"role": "user", "content": f"[이전 가게]\n- {describe_full(stores[prev['store']])}\n\n[새 가게]\n- {describe_full(stores[seg['store']])}\n\n[자막 줄]\n{shown}"}])
            usage[0] += r.usage.prompt_tokens
            usage[1] += r.usage.completion_tokens
            n = r.choices[0].message.parsed.line
            if n and 1 <= n <= len(cands) and prev["start_sec"] < int(cands[n - 1]["start"]) <= t:
                new["start_sec"] = int(cands[n - 1]["start"])
        out.append(new)
    return out


def find_boundaries(vid: str, stores: List[dict], lines: List[dict], force: bool) -> List[dict]:
    """식당별 구간 [{store(0부터), start_sec}]. 모델 호출 결과를 저장해 재사용한다. 검증 실패하면 ValueError."""
    path = BOUNDARY_DIR / f"{vid}.json"
    if path.exists() and not force:
        return json.loads(path.read_text(encoding="utf-8"))["segments"]

    description = get_description(vid)[:3000]
    segs = chapter_segments(description, stores)
    if segs:  # 소개란 챕터에 모든 가게가 있으면 모델 없이 그 시각을 쓴다
        BOUNDARY_DIR.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"video_id": vid, "method": "description_chapters", "segments": segs}, ensure_ascii=False, indent=1),
                        encoding="utf-8")
        return segs

    transcript = "\n".join(f"[{format_timestamp(l['start'])}] {l['text']}" for l in lines)
    store_list = "\n".join(f"{i}. {describe_full(s)}" for i, s in enumerate(stores, 1))
    resp = client.beta.chat.completions.parse(
        model=MODEL, temperature=0, seed=42, response_format=Segments,
        messages=[
            {"role": "system", "content": BOUNDARY_PROMPT},
            {"role": "user", "content": f"[가게 목록]\n{store_list}\n\n[영상 소개란]\n{description}\n\n[자막]\n{transcript}"},
        ],
    )
    parsed = resp.choices[0].message.parsed
    starts = {int(l["start"]) for l in lines}
    found = []
    for s in parsed.segments:
        sec = hms_to_sec(s.start)
        if sec is None or not 1 <= s.store <= len(stores):
            continue
        found.append((min(starts, key=lambda x: abs(x - sec)), s.store - 1))  # 모델이 적은 시각을 실제 자막 줄 시각에 맞춘다
    segs = []
    for sec, store in sorted(found):  # 모델이 시간순으로 안 적는 경우가 있어 시각순으로 다시 정렬한다
        if segs and (segs[-1]["store"] == store or segs[-1]["start_sec"] == sec):  # 같은 가게의 연속 구간은 합치고, 같은 시각이면 앞의 것을 둔다
            continue
        segs.append({"store": store, "start_sec": sec})
    missing = set(range(len(stores))) - {s["store"] for s in segs}
    if missing:
        raise ValueError(f"{vid}: 구간에 없는 가게 {sorted(missing)} (모델 응답 {len(parsed.segments)}개 중 유효 {len(segs)}개)")
    usage = [resp.usage.prompt_tokens, resp.usage.completion_tokens]
    segs = refine_starts(stores, lines, segs, usage)
    BOUNDARY_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"video_id": vid, "method": "llm+refine", "segments": segs, "usage": usage}, ensure_ascii=False, indent=1), encoding="utf-8")
    return segs


def merge_short_segments(segs: List[dict], video_end: float) -> List[dict]:
    """너무 짧은 구간(예: 영상 끝에서 가게 이름을 한 줄씩 나열하는 요약)을 앞 구간에 합친다. 첫 구간은 합치지 않고, 합친 뒤 같은 가게가 연속되면 하나로 묶는다."""
    out = []
    for i, seg in enumerate(segs):
        end = segs[i + 1]["start_sec"] if i + 1 < len(segs) else video_end
        if out and end - seg["start_sec"] < MIN_SEGMENT_SEC:
            continue
        if out and out[-1]["store"] == seg["store"]:
            continue
        out.append(seg)
    return out


def video_parts(vid: str, stores: List[dict], lines: List[dict], force: bool = False) -> List[tuple]:
    """영상 자막을 식당별 구간으로 나눈다. [(가게 dict, 그 구간의 자막 줄들)]. 식당이 1곳이면 전체 자막, 여러 곳이면 구간 경계로 나눈다."""
    if len(stores) == 1:
        return [(stores[0], lines)]
    segs = merge_short_segments(find_boundaries(vid, stores, lines, force), lines[-1]["start"])
    parts = []
    for i, seg in enumerate(segs):
        end = segs[i + 1]["start_sec"] if i + 1 < len(segs) else float("inf")
        lo = 0 if i == 0 else seg["start_sec"]  # 첫 구간은 영상 처음부터 시작해 인사·전체 소개도 첫 가게에 포함한다
        parts.append((stores[seg["store"]], [l for l in lines if lo <= l["start"] < end]))
    return parts


# -------------------------------------------------------------
# 5. 가게 단위 판단: 코스 여부, 음식 분류
# -------------------------------------------------------------
def call(system: str, user: str, schema, usage: dict, model: str = MODEL):
    r = client.beta.chat.completions.parse(
        model=model, temperature=0, seed=42, response_format=schema,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}])
    usage["prompt_tokens"] += r.usage.prompt_tokens
    usage["output_tokens"] += r.usage.completion_tokens
    usage["calls"] += 1
    return r.choices[0].message.parsed.model_dump()


COURSE_WORDS = re.compile(r"코스|테이스팅|오마카세|세트")


class CourseCheck(BaseModel):
    is_course: bool = Field(description="이 가게에서 코스(테이스팅 메뉴, 코스 요리, 세트, 오마카세)로 식사했으면 true. 단품을 골라 주문했거나 코스가 아닌 이야기(다른 가게, 일반론)면 false")
    course_name: Optional[str] = Field(None, description="코스 이름. 자막에서 부른 이름(예: 디너 코스, 런치 코스)을 쓰고 이름이 없으면 '코스'. is_course 가 false면 null")
    line: Optional[int] = Field(None, description="판단의 근거가 된 줄 번호(사용자가 준 번호 그대로). is_course 가 false면 null")


COURSE_PROMPT = """너는 식당 방문 영상의 자막에서 이 가게의 식사가 코스였는지 판단하는 검수자야. [대상 가게]의 자막 중 '코스', '테이스팅', '오마카세', '세트'라는 말이 들어 있는 줄이 번호와 함께 주어진다.
- 이 가게에서 코스로 식사했다는 말(예: "코스요리 시작", "디너 코스로 먹는다")이 있으면 is_course 를 true로 하고 근거 줄 번호를 line 에 적는다.
- 코스가 아닌 이야기(다른 가게 이야기, "코스가 아니라 단품", 여행 코스, 일반적인 설명)뿐이면 false다.
- 근거가 불분명하면 false다. 자막에 없는 사실을 지어내지 않는다."""


def detect_course(lines: List[dict], store: dict, usage: dict) -> Optional[dict]:
    """코스 여부를 가게 단위로 한 번만 판단한다. 요리별로 모델에 맡기면 코스를 놓치거나 단품에 붙이는 일이 잦아서,
    코드가 '코스'류 단어가 있는 자막 줄만 골라 보여 주고 그 줄만 보고 답하게 한다. 근거 줄은 코드가 직접 가져온다."""
    cands = [l for l in lines if COURSE_WORDS.search(l["text"])][:12]
    if not cands:
        return None
    shown = "\n".join(f"{i}. [{format_timestamp(l['start'])}] {l['text']}" for i, l in enumerate(cands, 1))
    c = call(COURSE_PROMPT, f"[대상 가게]\n- {store['korean_name']} / {store['address']}\n\n[자막 줄]\n{shown}", CourseCheck, usage)
    if not (c["is_course"] and c["line"] and 1 <= c["line"] <= len(cands)):
        return None
    ev = cands[c["line"] - 1]
    return {"name": (c["course_name"] or "코스").strip(), "evidence": ev["text"], "evidence_sec": int(ev["start"])}


class Cuisine(BaseModel):
    broad: Literal["한식", "일식", "중식", "양식", "동남아식", "인도식", "중동식", "기타"] = Field(description="이 식당이 내는 음식의 대분류")
    detail: Optional[str] = Field(None, description="세부 분류를 딱 하나, 짧은 구로(쉼표로 여러 개 쓰지 않는다. 미슐랭 등급은 쓰지 않는다). 근거가 부족하면 null")
    tags: List[str] = Field(default_factory=list, description="대표 음식 키워드 최대 5개. 술·와인·재료 일반명은 넣지 않는다")


CUISINE_PROMPT = """너는 식당이 내는 음식의 종류를 분류하는 편집자야. 영상 제목, 가게 이름·주소·국가, 이 가게에서 나온 메뉴 이름이 주어진다.
- broad: 식당이 있는 나라가 아니라 식당이 내는 음식으로 정한다. 한식, 일식, 중식, 양식(프랑스·이탈리아·스페인 등 유럽과 아메리카 요리 전체, 스테이크 포함), 동남아식, 인도식, 중동식, 기타 중에서 고른다.
  나라만 보고 정하지 않는다. 식당 컨셉·소개에 나온 말(예: "모던 ○○", "○○ 가정식")이 가장 중요한 근거이고, 그 다음이 영상 제목과 메뉴의 재료·조리법이다.
  재료나 조리법이 그 나라 음식이면(예: 장·젓갈·전·만두를 다른 식재료와 섞어 낸 현대적인 요리) 그 나라 음식으로 본다.
  어느 큰 분류에도 맞지 않으면(해산물 전문점, 베이커리 등) 무리하게 끼워 넣지 말고 기타로 한다. '기타'는 '그 밖의 음식'이 아니라 나라로 나눌 수 없는 식당이다.
- detail: 이 식당을 대표하는 세부 분류를 딱 하나만 짧은 명사구로 쓴다. 쉼표로 여러 개를 쓰지 않는다. 음식의 나라·종류가 보이면 그것을 대표로 하고 형태(파인다이닝, 코스 요리, 레스토랑)는 음식 종류를 알 수 없을 때만 쓴다.
  검색이 되려면 표기가 같아야 하므로 아래 예시에 맞는 말이 있으면 그 표기를 그대로 쓴다(프랑스 요리·프렌치 레스토랑은 '프렌치', 이탈리아 요리는 '이탈리안'). 맞는 게 없을 때만 짧은 일반 명사 하나로 쓴다. 타파스를 주로 내는 바는 '스페인 요리'가 아니라 '타파스 바'로 한다.
  '시그니처 요리' 같은 분류가 아닌 말은 쓰지 않는다. 근거가 부족하면 null이다.
- tags: 대표로 고르지 않은 다른 후보와 대표 음식·형태 키워드를 최대 5개. 술·와인·재료 일반명은 넣지 않는다.
- 미슐랭·미쉐린·별 개수 같은 말은 detail에도 tags에도 쓰지 않는다(미슐랭은 따로 관리한다).

세부 분류 예시(대분류별):
""" + VOCAB


def classify_cuisine(store: dict, merged: dict, usage: dict) -> dict:
    menus = "\n".join(f"- {m['name']}" + (f": {m['cooking_features'][:50]}" if m.get("cooking_features") else "") for m in merged["menus"][:30]) or "(없음)"
    points = "\n".join(f"- {k}" for k in merged.get("key_points", [])[:4])
    user = (f"[영상 제목]\n{store['video_title']}\n\n[가게]\n- {store['korean_name']} / {store['address']} / 국가 {store.get('country_code', '')}\n\n"
            f"[식당 컨셉·소개]\n{merged.get('concept') or '(없음)'}\n{points}\n\n[나온 메뉴]\n{menus}")
    return call(CUISINE_PROMPT, user, Cuisine, usage)


# -------------------------------------------------------------
# 6. 셰프 추출 (가게 구간 자막 -> 대표 셰프, 확신도는 코드로 계산)
# -------------------------------------------------------------
CHEF_WORDS = re.compile(r"셰프|쉐프|세프|주방장|chef|Chef|CHEF")


class Chef(BaseModel):
    name: str = Field(description="셰프 이름. '셰프님' 같은 칭호는 빼고 한국어 표기로 적는다. 영상 제목·[대상 가게]에 적힌 표기를 우선한다")
    role: Literal["owner_chef", "head_chef", "chef", "pastry_chef", "other"] = Field(
        description="owner_chef: 오너 셰프·대표 / head_chef: 총괄·헤드 셰프 / chef: 이 가게 주방의 셰프 / pastry_chef: 파티시에 / other"
    )
    background: Optional[str] = Field(
        None, description="유튜버가 말한 경력·수상·방송 출연·요리 철학을 1~2문장으로. '영상에서 ~라고 소개' 형태로 쓰지 않고 내용만 적는다. 없으면 null"
    )
    evidence: str = Field(description="이 셰프가 이 가게의 셰프라고 판단한 근거 자막 원문 1~2줄(시각 [HH:MM:SS] 포함)")
    confidence: Literal["high", "medium", "low"] = Field(
        description="high: 이름이 영상 제목이나 [대상 가게]에 있거나 자막에서 분명하다 / medium: 제목에는 없지만 자막에서 2번 이상 같은 표기로 나온다 / "
        "low: 한 번만 나오거나 표기가 깨져 보이거나 이 가게의 셰프인지 불확실하다"
    )


class ChefResult(BaseModel):
    chefs: List[Chef] = Field(default_factory=list, description="이 가게의 셰프. 없으면 빈 목록")


CHEF_PROMPT = """너는 미식 유튜브 영상 자막(STT)에서 식당의 셰프 정보를 뽑는 분석가야. 자막은 "[HH:MM:SS] 텍스트" 형식이다.

규칙:
- 사용자가 지정한 [대상 가게]의 셰프(오너 셰프, 총괄·헤드 셰프, 대표 셰프)만 뽑는다.
- 유튜버와 함께 간 다른 셰프, 지인, 다른 식당의 셰프, 비교로 언급된 셰프, 인사말에 나오는 채널 운영자는 이 가게의 셰프가 아니므로 제외한다.
- 자막에는 음성 인식 오류가 있어 사람 이름이 자주 깨진다(예: 성만 맞고 이름이 다르게 적힘). 영상 제목과 [대상 가게] 표기가 있으면 그 표기를 우선하고,
  자막에서만 나오는 이름은 같은 표기로 여러 번 나오는지 보고 확신도를 정한다. 이름을 추측해서 만들거나 외국 셰프 이름을 일반 지식으로 보충하지 않는다.
- 경력·수상·방송 출연 등은 영상 제목이나 자막에 나온 내용만 적는다(제목의 "정식당 출신 셰프" 같은 표현도 근거다). 없으면 background는 null이다.
- 셰프 이름은 반드시 제목이나 자막에 실제로 나온 글자만 쓴다. 이름을 알 수 없으면 항목을 만들지 말고 chefs를 빈 목록으로 둔다(name에 null, 없음 같은 값을 넣지 않는다).
- 셰프 언급이 없거나 이 가게의 셰프가 누구인지 알 수 없으면 chefs를 빈 목록으로 둔다.
"""


GARBAGE_NAMES = {"null", "none", "n/a", "na", "없음", "미상", "unknown", "-", ""}
# 직함·호칭·채널 운영자는 셰프 이름이 아니다(src/db/load_chefs.py 의 GENERIC 과 같은 목록. 거기서도 한 번 더 거른다)
GENERIC_NAMES = {"사장님", "사장", "대표님", "대표", "오너", "주방장", "선생님", "프렌치", "프렌치셰프", "요리사", "쉐프", "세프", "비밀이야", "비밀아", "비밀리아"}
GENERIC_SUFFIX = ("사장님", "대표님", "셰프님", "방탈")


def _chef_norm(text: str) -> str:
    return re.sub(r"[\s·.,'\"()\[\]]", "", text).replace("셰프님", "").replace("셰프", "")


def assess(chefs: List[dict], title: str, segments: List[dict], source: str) -> List[dict]:
    """모델이 적은 셰프를 검증하고 확신도를 코드로 다시 계산한다. 가짜 이름·자막과 제목에 없는 이름은 버린다.
    high: 제목에 이름이 있거나(제작자 자막이면 자막에서 2번 이상) / medium: 자막에서 2번 이상(자동 자막), 제작자 자막에서 1번 / low: 그 외.
    모델의 확신도는 model_confidence 로 남기고 쓰지 않는다."""
    texts = [_chef_norm(seg["text"]) for seg in segments]
    kept = []
    for chef in chefs:
        name = chef["name"].strip()
        key = _chef_norm(name)
        if name.lower() in GARBAGE_NAMES or len(key) < 2 or len(key) > 12:
            continue
        if name.replace(" ", "") in GENERIC_NAMES or key in GENERIC_NAMES or name.endswith(GENERIC_SUFFIX):
            continue  # "대표님", "사장님" 같은 호칭이나 채널 운영자
        in_title = key in _chef_norm(title)
        mentions = sum(1 for t in texts if key in t)
        if not in_title and mentions == 0:
            continue  # 제목에도 자막에도 없는 이름은 모델이 지어낸 것
        if in_title or (source == "manual_ko" and mentions >= 2):
            confidence = "high"
        elif mentions >= 2 or source == "manual_ko":
            confidence = "medium"
        else:
            confidence = "low"
        kept.append({**chef, "name": name, "model_confidence": chef["confidence"], "confidence": confidence,
                     "name_in_title": in_title, "name_mentions": mentions})
    return kept


def chefs_for_store(store: dict, others: List[dict], lines: List[dict], source: str, usage: dict) -> List[dict]:
    """식당 구간 자막(lines)에서 이 가게의 셰프를 뽑아 검증한다(extract_script_split.py 가 추출 단계에서 쓴다). 셰프 단어가 제목·자막에 없으면 호출하지 않는다.
    이름 언급 횟수는 이 구간 자막 안에서만 세므로, 식당이 여러 곳인 영상에서 다른 가게 구간의 언급이 확신도를 올리지 않는다."""
    if not (CHEF_WORDS.search(store["video_title"]) or any(CHEF_WORDS.search(l["text"]) for l in lines)):
        return []
    others_text = "\n".join(f"- {describe(o)}" for o in others) or "(없음)"
    transcript = "\n".join(f"[{format_timestamp(l['start'])}] {l['text']}" for l in lines)
    response = client.beta.chat.completions.parse(
        model=MODEL, temperature=0, seed=42, response_format=ChefResult,
        messages=[
            {"role": "system", "content": CHEF_PROMPT},
            {"role": "user", "content": (
                f"[영상 제목]\n{store['video_title']}\n\n[대상 가게]\n- {describe(store)}\n\n"
                f"[같은 영상의 다른 가게 (이 가게들의 셰프는 제외)]\n{others_text}\n\n"
                f"다음 자막에서 [대상 가게]의 셰프 정보를 추출해줘:\n\n{transcript}")},
        ],
    )
    usage["prompt_tokens"] += response.usage.prompt_tokens
    usage["output_tokens"] += response.usage.completion_tokens
    usage["calls"] += 1
    return assess([c.model_dump() for c in response.choices[0].message.parsed.chefs], store["video_title"], lines, source)
