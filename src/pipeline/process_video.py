"""새 영상 1개를 처음부터 DB 적재까지 처리하는 오케스트레이터 (n8n 자동화의 진입점).

기존 단계별 스크립트를 `--video-id` 로 차례로 호출하고, 진행 상태를 DB `video_queue` 에 기록한다.
어느 단계에서 멈춰도 같은 명령을 다시 실행하면 끝난 단계는 건너뛰고 이어서 처리한다.

단계 (STAGES):
  gate        제목·소개란을 LLM(gpt-4o-mini)으로 보고 식당 영상인지, 중국 본토 식당만 나오는지 판단   -> 아니면 skipped
  places      extract_restaurant_info.py : 가게 추출 + Google Places + 미슐랭(Parse)            -> 가게가 없으면 skipped, 매칭 실패면 needs_review
  michelin_check 제목에 미쉐린·미슐랭·N스타가 있는데 Parse 결과가 전부 NONE이면 needs_review (표기 차이로 매칭을 놓쳤을 수 있다)
  captions    yt-dlp 로 자막 종류 확인(제작자 자막 manual / 자동 생성 auto / 없음 none), 자막 저장
  extract     manual -> extract_script_split.py(자막 분석), auto·none -> extract_video_split.py(Gemini 영상 분석)  -> 메뉴가 없으면 needs_review
  postprocess postprocess_split.py (분류 재정하기, 평가어·코스 단계·음료 중복 정리)
  load        load_restaurants -> 지역(enrich_places_details + load_regions) -> load_notes -> load_chefs -> embed_chunks

결과 JSON 은 data/video_notes/ 아래 임시 폴더(`*-live`)에 쌓이고, 적재가 끝나면 _live_archive/{video_id}/ 로 옮긴다.
(postprocess_split.py 는 폴더 전체를 처리하므로, 한 번에 영상 하나만 들어 있어야 한다. 동시에 두 번 실행하지 않도록 잠금 파일을 쓴다.)

명령 (stdout 에는 마지막에 JSON 한 덩어리만 나오고, 진행 로그는 stderr 와 data/logs/process_video/{video_id}.log 에 남는다):
  uv run python src/pipeline/process_video.py init-db                      # video_queue 테이블 만들기 (한 번)
  uv run python src/pipeline/process_video.py discover [--channel URL] [--recent 10] [--dry-run]
                                                                           # 채널의 새 영상을 큐에 넣는다 (이미 처리했거나 알려진 영상은 제외)
  uv run python src/pipeline/process_video.py enqueue VIDEO_ID_OR_URL ...  # 영상을 직접 큐에 넣는다
  uv run python src/pipeline/process_video.py run VIDEO_ID_OR_URL ...      # 지정한 영상 처리
  uv run python src/pipeline/process_video.py run --next 3                 # 대기·실패 영상 3개 처리
  uv run python src/pipeline/process_video.py run ID --stop-after places   # 해당 단계까지만 (검증용)
  uv run python src/pipeline/process_video.py status                       # 상태별 건수와 사람이 볼 영상
  uv run python src/pipeline/process_video.py reset ID [--redo-from places] [--skip 사유]
                                                                           # needs_review·failed 를 다시 대기로 / 직접 제외
  uv run python src/pipeline/process_video.py michelin [--max-calls 90]    # Parse 한도로 못 채운 미슐랭을 채워 DB 에 반영

종료 코드: 0 정상(영상별 결과는 JSON 의 status), 75 다른 실행이 진행 중(JSON status=busy).
환경변수(.env): POSTGRES_URI(쓰기 권한 계정) 와 각 단계 스크립트가 쓰는 키들(OPENAI, GOOGLE_PLACES, MICHELIN_GUIDE, GEMINI).
"""

import argparse
import csv
import json
import os
import re
import shutil
import subprocess
import sys
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Literal, Optional, Tuple

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv
from openai import OpenAI
from pydantic import BaseModel, Field

sys.path.insert(0, str(Path(__file__).resolve().parent))
from video_meta_cache import get_video_meta  # noqa: E402

load_dotenv()

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
PIPELINE = ROOT / "src" / "pipeline"
DB_SCRIPTS = ROOT / "src" / "db"
STORES_CSV = DATA / "restaurants_info.csv"
NOTES_DIR = DATA / "video_notes"
LIVE_DIR = DATA / "urls" / "_live"          # 영상별 임시 파일(대상 목록, 적재용 xlsx/csv)
LOG_DIR = DATA / "logs" / "process_video"
LOCK_FILE = DATA / ".process_video.lock"
LOCK_STALE_SEC = 2 * 3600

SCRIPT_TAG = "gpt-4o-mini-script-split-v2-live"      # 제작자 자막 영상(자막 분석) 임시 결과 폴더이자 model 값
VIDEO_TAG = "gemini-3.5-flash-lite-split-v1-live"    # 자동 생성 자막·자막 없는 영상(Gemini 영상 분석)
DEFAULT_CHANNEL = "https://www.youtube.com/@bimirya/videos"

STAGES = ["gate", "places", "michelin_check", "captions", "extract", "postprocess", "load"]
STEP_TIMEOUT_SEC = 1800
MAX_ATTEMPTS = 3   # --next 가 failed 영상을 자동으로 다시 시도하는 최대 횟수 (넘으면 reset 으로 사람이 되돌린다)


class StageError(Exception):
    """재시도하면 풀릴 수 있는 오류 (status=failed)."""


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


# -------------------------------------------------------------
# 1. 영상 ID, CSV, DB 도우미
# -------------------------------------------------------------
_ID_RE = re.compile(r"(?:v=|youtu\.be/|shorts/)([A-Za-z0-9_-]{11})")


def parse_video_id(text: str) -> str:
    text = text.strip()
    m = _ID_RE.search(text)
    if m:
        return m.group(1)
    if re.fullmatch(r"[A-Za-z0-9_-]{11}", text):
        return text
    raise SystemExit(f"영상 ID 또는 URL 이 아닙니다: {text}")


def watch_url(vid: str) -> str:
    return f"https://www.youtube.com/watch?v={vid}"


def csv_rows(vid: str) -> Tuple[List[str], List[dict]]:
    """restaurants_info.csv 에서 이 영상의 행 (컬럼 목록, 행 목록)."""
    if not STORES_CSV.exists():
        return [], []
    with open(STORES_CSV, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        rows = [r for r in reader if (m := _ID_RE.search(r["video_url"])) and m.group(1) == vid]
        return list(reader.fieldnames or []), rows


def is_store(row: dict) -> bool:
    return bool(row["korean_name"].strip() and row["google_cid"].strip())


def michelin_unchecked(rows: List[dict]) -> bool:
    return any(is_store(r) and (not r["latest_grade"].strip() or "미슐랭 미조회" in r["note"] or "미슐랭 조회 실패" in r["note"]) for r in rows)


def connect():
    conn = psycopg2.connect(os.environ["POSTGRES_URI"])
    conn.autocommit = True
    return conn


def fetch_row(conn, vid: str) -> Optional[dict]:
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT * FROM video_queue WHERE video_id = %s", (vid,))
        return cur.fetchone()


def update_row(conn, vid: str, **fields) -> None:
    sets, values = ["updated_at = now()"], []
    for k, v in fields.items():
        sets.append(f"{k} = %s")
        values.append(psycopg2.extras.Json(v, dumps=lambda o: json.dumps(o, ensure_ascii=False)) if k == "gate" else v)
    with conn.cursor() as cur:
        cur.execute(f"UPDATE video_queue SET {', '.join(sets)} WHERE video_id = %s", (*values, vid))


def enqueue(conn, vids: List[str]) -> List[str]:
    """큐에 없는 영상만 pending 으로 넣고, 새로 넣은 ID 를 돌려준다."""
    added = []
    with conn.cursor() as cur:
        for vid in vids:
            cur.execute("INSERT INTO video_queue (video_id, url) VALUES (%s, %s) ON CONFLICT DO NOTHING", (vid, watch_url(vid)))
            if cur.rowcount:
                added.append(vid)
    return added


@contextmanager
def run_lock():
    """한 번에 하나의 처리만 허용한다 (임시 결과 폴더와 CSV 를 공유하므로)."""
    LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    for _ in range(2):
        try:
            fd = os.open(LOCK_FILE, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, f"{os.getpid()} {datetime.now().isoformat()}".encode())
            os.close(fd)
            break
        except FileExistsError:
            if time.time() - LOCK_FILE.stat().st_mtime > LOCK_STALE_SEC:  # 비정상 종료로 남은 잠금
                LOCK_FILE.unlink(missing_ok=True)
                continue
            print(json.dumps({"status": "busy", "message": f"다른 process_video 실행이 진행 중입니다 ({LOCK_FILE})"}, ensure_ascii=False))
            raise SystemExit(75)
    try:
        yield
    finally:
        LOCK_FILE.unlink(missing_ok=True)


# -------------------------------------------------------------
# 2. 하위 스크립트 실행
# -------------------------------------------------------------
class Ctx:
    def __init__(self, conn, row: dict):
        self.conn, self.row, self.vid, self.url = conn, row, row["video_id"], row["url"]
        self.log_path = LOG_DIR / f"{self.vid}.log"

    def update(self, **fields) -> None:
        update_row(self.conn, self.vid, **fields)
        self.row.update(fields)

    def run(self, label: str, script: Path, args: List[str], env: Optional[dict] = None) -> str:
        """하위 스크립트를 실행하고 출력을 로그 파일에 덧붙인다. 종료 코드가 0 이 아니면 StageError."""
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        cmd = [sys.executable, str(script), *args]
        log(f"[{self.vid}] {label}: {script.name} {' '.join(args)}")
        started = time.time()
        proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=STEP_TIMEOUT_SEC,
                              env={**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8", **(env or {})})
        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(f"\n===== {datetime.now():%Y-%m-%d %H:%M:%S} {label}: {' '.join(cmd[1:])} ({time.time() - started:.0f}s, exit {proc.returncode})\n")
            f.write(proc.stdout + ("\n[stderr]\n" + proc.stderr if proc.stderr.strip() else ""))
        if proc.returncode != 0:
            tail = (proc.stderr.strip() or proc.stdout.strip()).splitlines()[-3:]
            raise StageError(f"{label} 실패(exit {proc.returncode}): {' | '.join(tail)}"[:500])
        return proc.stdout

    def targets_env(self) -> dict:
        """common.load_stores 가 이 영상만 대상으로 삼도록 TARGETS_FILE 을 만든다."""
        LIVE_DIR.mkdir(parents=True, exist_ok=True)
        path = LIVE_DIR / f"targets_{self.vid}.txt"
        path.write_text(self.vid + "\n", encoding="utf-8")
        return {"TARGETS_FILE": str(path)}


# -------------------------------------------------------------
# 3. 단계 1: 제목·소개란 판단
# -------------------------------------------------------------
class GateResult(BaseModel):
    restaurants_mentioned: List[str] = Field(description="제목·소개란에 나온 음식점 이름 (없으면 빈 목록). 먼저 이것부터 채운다")
    video_type: Literal["restaurant", "lodging", "other"] = Field(
        description="restaurant: 식당·카페·술집·시장 음식점을 방문해 먹는 영상 / lodging: 호텔·료칸·리조트 숙박 후기가 중심 / other: 그 밖(요리 레시피, 브이로그, 쇼핑, 공지 등)")
    restaurant_regions: List[str] = Field(
        description="restaurants_mentioned 와 같은 순서로, 각 식당이 있는 곳을 '한국', '일본', '프랑스', '홍콩', '마카오', '대만', '중국 본토' 같은 나라·지역 이름 하나로 적는다. 모르면 '불명'")
    reason: str = Field(description="판단 근거 한 문장 (제목·소개란에서 본 단서)")


GATE_PROMPT = """당신은 미식 유튜브 채널의 영상이 '식당 정보 데이터베이스'에 쓸 만한지 판단한다.
영상 제목과 소개란만 보고 아래를 판단한다.
- video_type: 식당·카페·술집 등 음식점이 하나라도 소개되거나 방문해 먹는 영상이면 restaurant. 호텔 안의 레스토랑을 방문해 먹는 영상도 restaurant.
  판단이 애매하면 restaurant 로 둔다(뒤 단계에서 한 번 더 걸러진다). 숙박 후기(객실 등 숙소 자체)가 중심이면 lodging.
  소개된 음식점이 전혀 없는 레시피, 일상 브이로그, 쇼핑, 공지 등만 other.
- restaurant_regions: 식당마다 위치를 적는다. 중국 본토(상하이, 베이징, 광저우, 청두 등)만 '중국 본토'라고 적고, 홍콩·마카오·대만은 각각 따로 적는다."""


def judge_video(title: str, description: str) -> GateResult:
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    resp = client.beta.chat.completions.parse(
        model="gpt-4o-mini",
        messages=[{"role": "system", "content": GATE_PROMPT},
                  {"role": "user", "content": f"[제목]\n{title}\n\n[소개란]\n{description[:6000]}"}],
        response_format=GateResult, temperature=0)
    if resp.choices[0].message.parsed is None:
        raise StageError("gate 응답 파싱 실패")
    return resp.choices[0].message.parsed


def stage_gate(ctx: Ctx):
    try:
        meta = get_video_meta(ctx.url)
    except Exception as e:
        raise StageError(f"영상 정보 조회 실패(yt-dlp): {str(e)[:200]}")
    ctx.update(title=(meta["title"] or "")[:300])
    try:
        gate = judge_video(meta["title"], meta["description"])
    except StageError:
        raise
    except Exception as e:
        raise StageError(f"gate LLM 호출 실패: {str(e)[:200]}")
    ctx.update(gate=gate.model_dump())
    if gate.video_type != "restaurant" and not gate.restaurants_mentioned:  # 애매한 영상은 통과시킨다(places 단계의 숙소·중국 규칙이 다시 거른다)
        return "skipped", f"식당 영상이 아님({gate.video_type}): {gate.reason}"
    regions = [r.strip() for r in gate.restaurant_regions]
    if regions and all(r == "중국 본토" for r in regions):  # 불리언을 모델에 맡기면 근거와 어긋나게 답한 적이 있어 코드가 계산한다
        return "skipped", f"중국 본토 식당만 소개: {', '.join(gate.restaurants_mentioned)}"


# -------------------------------------------------------------
# 4. 단계 2: Places + 미슐랭
# -------------------------------------------------------------
def stage_places(ctx: Ctx):
    _, rows = csv_rows(ctx.vid)
    if not rows:  # extract_restaurant_info.py 는 CSV 에 있는 영상을 건너뛴다. 일시 오류면 행을 쓰지 않으므로 재시도된다.
        out = ctx.run("places", PIPELINE / "extract_restaurant_info.py", ["--video-id", ctx.vid, "--workers", "1"])
        _, rows = csv_rows(ctx.vid)
        if not rows:  # 이 스크립트는 오류를 출력만 하고 종료 코드 0 으로 끝나는 경우가 있다
            raise StageError(f"식당 추출 결과가 CSV 에 없음: {' | '.join(out.strip().splitlines()[-2:])}"[:400])
    if not rows:
        raise StageError("식당 추출 결과가 CSV 에 없음")
    stores = [r for r in rows if is_store(r)]
    unmatched = [r["korean_name"] for r in rows if r["korean_name"].strip() and not r["google_cid"].strip()]
    ctx.update(store_count=len(stores), michelin_pending=michelin_unchecked(rows))
    if not stores:
        notes = "; ".join(sorted({r["note"].strip() for r in rows if r["note"].strip()}))
        if unmatched:
            return "needs_review", f"Google Places 매칭 실패, 사람이 확인 필요: {', '.join(unmatched)}"
        return "skipped", notes or "가게 정보 없음"
    if unmatched:
        ctx.update(note=f"Google Places 매칭 실패로 제외된 가게: {', '.join(unmatched)}")


MICHELIN_TITLE_RE = re.compile(r"미쉐린|미슐랭|michelin|[1-3]\s*스타", re.IGNORECASE)


def michelin_mismatch(title: str, rows: List[dict]) -> List[str]:
    """제목은 미슐랭을 말하는데 Parse 가 이 영상의 식당에서 하나도 못 찾았으면 그 식당 이름들을 돌려준다."""
    if not MICHELIN_TITLE_RE.search(title or ""):
        return []
    stores = [r for r in rows if is_store(r) and r["latest_grade"].strip()]  # 미조회(빈 값)는 제외, 이미 확인된 것만 본다
    if not stores or any(r["is_michelin"].strip().lower() == "true" for r in stores):
        return []
    return [r["korean_name"] for r in stores if r["latest_grade"].strip() == "NONE"]


def stage_michelin_check(ctx: Ctx):
    _, rows = csv_rows(ctx.vid)
    names = michelin_mismatch(ctx.row.get("title") or "", rows)
    if names:
        return "needs_review", (f"제목은 미슐랭을 말하는데 Parse 결과는 NONE: {', '.join(names)}. Parse 후보를 직접 확인해 CSV·DB를 고치거나 "
                                f"(표기 차이로 매칭 실패했을 수 있음) 맞으면 `reset {ctx.vid} --accept`")


# -------------------------------------------------------------
# 5. 단계 3: 자막 종류 확인
# -------------------------------------------------------------
def stage_captions(ctx: Ctx):
    import yt_dlp
    import get_transcripts_since as ts

    try:
        with yt_dlp.YoutubeDL({"skip_download": True, "quiet": True, "no_warnings": True}) as ydl:
            segments, meta = ts.fetch_one(ydl, ctx.vid)
    except Exception as e:
        raise StageError(f"자막 조회 실패(yt-dlp): {str(e)[:200]}")
    if not segments:
        ctx.update(caption_type="none")
        return
    ts.save_transcript(ctx.vid, segments, meta)
    ctx.update(caption_type="manual" if meta["source"] == "manual_ko" else "auto")


# -------------------------------------------------------------
# 6. 단계 4: 정보 추출 (자막 분석 / Gemini 영상 분석)
# -------------------------------------------------------------
def live_tag(ctx: Ctx) -> str:
    return SCRIPT_TAG if ctx.row["caption_type"] == "manual" else VIDEO_TAG


def result_file(tag: str, vid: str, cid: str, excluded: bool = False) -> Path:
    return NOTES_DIR / ("_excluded" if excluded else "") / tag / f"{vid}__{cid}.json"


def move_stale(tag: str, vid: str) -> None:
    """임시 폴더에 다른 영상의 파일이 남아 있으면 치운다 (postprocess_split 이 폴더 전체를 처리하므로)."""
    for d in (NOTES_DIR / tag, NOTES_DIR / f"{tag}_raw"):
        for f in d.glob("*.json") if d.exists() else []:
            if not f.name.startswith(f"{vid}__"):
                dest = NOTES_DIR / "_live_stale" / d.name
                dest.mkdir(parents=True, exist_ok=True)
                shutil.move(str(f), str(dest / f.name))
                log(f"[{vid}] 다른 영상의 남은 결과를 치움: {f.name} -> _live_stale/{d.name}/")


def stage_extract(ctx: Ctx):
    tag = live_tag(ctx)
    _, rows = csv_rows(ctx.vid)
    cids = sorted({r["google_cid"].strip() for r in rows if is_store(r)})
    move_stale(tag, ctx.vid)

    def missing() -> List[str]:
        return [c for c in cids if not result_file(tag, ctx.vid, c).exists() and not result_file(tag, ctx.vid, c, excluded=True).exists()]

    if missing():
        if ctx.row["caption_type"] == "manual":
            ctx.run("extract(자막)", PIPELINE / "extract_script_split.py", ["--video-id", ctx.vid, "--tag", tag], env=ctx.targets_env())
        else:
            ctx.run("extract(Gemini)", PIPELINE / "extract_video_split.py", [f"--video-id={ctx.vid}", "--tag", tag])
    left = missing()
    if left:
        raise StageError(f"추출 결과가 없는 가게 {len(left)}곳(cid {', '.join(left)}): 재실행하면 이 가게만 다시 호출")
    saved = [c for c in cids if result_file(tag, ctx.vid, c).exists()]
    if not saved:
        return "needs_review", "모든 가게가 메뉴 없음 등으로 제외됨(data/video_notes/_excluded 참고): 영상에서 먹는 장면이 없는지 확인"
    dropped = len(cids) - len(saved)
    if dropped:
        ctx.update(note=((ctx.row.get("note") or "") + f" 메뉴가 없어 제외된 가게 {dropped}곳.").strip())


# -------------------------------------------------------------
# 7. 단계 5: 후처리
# -------------------------------------------------------------
def stage_postprocess(ctx: Ctx):
    tag = live_tag(ctx)
    out_dir, raw_dir = NOTES_DIR / tag, NOTES_DIR / f"{tag}_raw"
    move_stale(tag, ctx.vid)
    raw_dir.mkdir(parents=True, exist_ok=True)
    for f in out_dir.glob(f"{ctx.vid}__*.json"):  # postprocess_split 은 항상 _raw 에서 읽으므로 없는 파일만 복사한다
        if not (raw_dir / f.name).exists():
            shutil.copy2(f, raw_dir / f.name)
    ctx.run("postprocess", PIPELINE / "postprocess_split.py", ["--tag", tag], env=ctx.targets_env())


# -------------------------------------------------------------
# 8. 단계 6: DB 적재
# -------------------------------------------------------------
def load_restaurants_for(ctx: Ctx, label: str = "load_restaurants") -> int:
    """이 영상의 식당 행만 xlsx 로 만들어 load_restaurants.py 로 적재한다 (전체를 다시 쓰지 않는다)."""
    import pandas as pd

    df = pd.read_csv(STORES_CSV, encoding="utf-8-sig", dtype={"google_cid": str})
    df = df[df["video_url"].str.contains(ctx.vid, regex=False, na=False) & df["google_cid"].notna() & df["korean_name"].notna()]
    if df.empty:
        raise StageError("적재할 식당 행이 없음")
    LIVE_DIR.mkdir(parents=True, exist_ok=True)
    xlsx = LIVE_DIR / f"{ctx.vid}.xlsx"
    df.to_excel(xlsx, index=False)
    ctx.run(label, DB_SCRIPTS / "load_restaurants.py", [str(xlsx)])
    return len(df)


def fill_regions(ctx: Ctx) -> None:
    fieldnames, rows = csv_rows(ctx.vid)
    LIVE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = LIVE_DIR / f"{ctx.vid}.csv"
    with open(tmp, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(r for r in rows if is_store(r))
    ctx.run("regions(Details)", PIPELINE / "enrich_places_details.py", ["--input", str(tmp)])  # 이미 받은 place_id 는 건너뛴다
    ctx.run("load_regions", DB_SCRIPTS / "load_regions.py", [])


def stage_load(ctx: Ctx):
    tag = live_tag(ctx)
    notes = sorted((NOTES_DIR / tag).glob(f"{ctx.vid}__*.json"))
    if not notes:
        raise StageError(f"적재할 노트가 없음({NOTES_DIR / tag})")
    load_restaurants_for(ctx)
    fill_regions(ctx)
    ctx.run("load_notes", DB_SCRIPTS / "load_notes.py", ["--model", tag, "--video-id", ctx.vid])
    ctx.run("load_chefs", DB_SCRIPTS / "load_chefs.py", ["--notes-tag", tag])
    ctx.run("embed_chunks", DB_SCRIPTS / "embed_chunks.py", [])  # 새로 생기거나 바뀐 청크만 임베딩한다
    with ctx.conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM video_restaurant_notes WHERE video_id = %s", (ctx.vid,))
        n_db = cur.fetchone()[0]
    if n_db < len(notes):
        raise StageError(f"노트 적재 확인 실패: 파일 {len(notes)}건, DB {n_db}건")
    archive = NOTES_DIR / "_live_archive" / ctx.vid  # 다음 영상의 postprocess 에 섞이지 않도록 임시 폴더를 비운다
    for d in (NOTES_DIR / tag, NOTES_DIR / f"{tag}_raw"):
        (archive / d.name).mkdir(parents=True, exist_ok=True)
        for f in d.glob(f"{ctx.vid}__*.json"):
            shutil.move(str(f), str(archive / d.name / f.name))


STAGE_FUNCS = {"gate": stage_gate, "places": stage_places, "michelin_check": stage_michelin_check, "captions": stage_captions,
               "extract": stage_extract, "postprocess": stage_postprocess, "load": stage_load}


# -------------------------------------------------------------
# 9. 영상 1개 처리
# -------------------------------------------------------------
def summary(row: dict) -> dict:
    keys = ("video_id", "title", "status", "stage", "caption_type", "skip_reason", "error", "note", "store_count", "michelin_pending", "attempts")
    return {k: row.get(k) for k in keys}


def process_one(conn, vid: str, stop_after: Optional[str] = None) -> dict:
    row = fetch_row(conn, vid)
    if row is None:
        enqueue(conn, [vid])
        row = fetch_row(conn, vid)
    if row["status"] in ("loaded", "skipped"):
        return summary(row)
    if row["status"] == "needs_review":
        return summary(row)  # 사람이 확인한 뒤 reset 해야 다시 처리한다
    ctx = Ctx(conn, row)
    ctx.update(status="running", error=None, attempts=(row["attempts"] or 0) + 1)
    done = STAGES.index(row["stage"]) + 1 if row["stage"] in STAGES else 0
    try:
        for stage in STAGES[done:]:
            started = time.time()
            outcome = STAGE_FUNCS[stage](ctx)
            log(f"[{vid}] {stage} 완료 ({time.time() - started:.0f}초)")
            if outcome:
                status, reason = outcome
                ctx.update(status=status, skip_reason=reason, stage=stage, finished_at=datetime.now())
                return summary(ctx.row)
            ctx.update(stage=stage)
            if stage == stop_after:
                ctx.update(status="pending")
                return summary(ctx.row)
    except Exception as e:
        err = str(e) if isinstance(e, StageError) else f"{type(e).__name__}: {e}"
        log(f"[{vid}] 실패: {err}")
        ctx.update(status="failed", error=err[:1000])
        return summary(ctx.row)
    ctx.update(status="loaded", skip_reason=None, finished_at=datetime.now())
    return summary(ctx.row)


# -------------------------------------------------------------
# 10. 명령
# -------------------------------------------------------------
def known_video_ids(conn) -> set:
    """이미 처리했거나 의도적으로 제외한 영상 (큐, DB, CSV, 예전 채널 URL 목록)."""
    known = set()
    with conn.cursor() as cur:
        cur.execute("SELECT video_id FROM video_queue UNION SELECT video_id FROM videos")
        known |= {r[0] for r in cur.fetchall()}
    if STORES_CSV.exists():
        with open(STORES_CSV, "r", encoding="utf-8-sig", newline="") as f:
            known |= {m.group(1) for r in csv.DictReader(f) if (m := _ID_RE.search(r["video_url"]))}
    for name in ("channel_video_urls.txt", "excluded_channel_video_urls.txt"):
        path = DATA / "urls" / name
        if path.exists():
            known |= {m.group(1) for line in path.read_text(encoding="utf-8").splitlines() if (m := _ID_RE.search(line))}
    return known


def cmd_init_db(args) -> dict:
    sql = (DB_SCRIPTS / "migrate_video_queue.sql").read_text(encoding="utf-8")
    with connect() as conn, conn.cursor() as cur:
        cur.execute(sql)
    return {"status": "ok", "message": "video_queue 테이블 준비 완료"}


def cmd_discover(args) -> dict:
    import yt_dlp

    opts = {"extract_flat": "in_playlist", "quiet": True, "no_warnings": True, "playlistend": args.recent}
    with yt_dlp.YoutubeDL(opts) as ydl:
        entries = [e for e in ydl.extract_info(args.channel, download=False).get("entries", []) if e]
    conn = connect()
    known = known_video_ids(conn)
    new = [e["id"] for e in entries if e["id"] not in known]
    if not args.dry_run:
        enqueue(conn, new)
    return {"status": "ok", "checked": len(entries), "new": new, "enqueued": not args.dry_run}


def cmd_enqueue(args) -> dict:
    vids = [parse_video_id(v) for v in args.videos]
    return {"status": "ok", "enqueued": enqueue(connect(), vids)}


def cmd_run(args) -> dict:
    conn = connect()
    if args.next:
        with conn.cursor() as cur:
            cur.execute("SELECT video_id FROM video_queue WHERE status = 'pending' OR (status = 'failed' AND attempts < %s) ORDER BY created_at LIMIT %s",
                        (MAX_ATTEMPTS, args.next))
            vids = [r[0] for r in cur.fetchall()]
    else:
        vids = [parse_video_id(v) for v in args.videos]
    if not vids:
        return {"status": "ok", "results": [], "message": "처리할 영상 없음"}
    with run_lock():
        return {"status": "ok", "results": [process_one(conn, v, args.stop_after) for v in vids]}


def cmd_status(args) -> dict:
    with connect() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT status, count(*) AS n FROM video_queue GROUP BY status")
        counts = {r["status"]: r["n"] for r in cur.fetchall()}
        cur.execute("""SELECT video_id, title, status, stage, skip_reason, error, michelin_pending FROM video_queue
                       WHERE status IN ('needs_review', 'failed') OR michelin_pending ORDER BY updated_at DESC LIMIT 50""")
        attention = cur.fetchall()
    return {"status": "ok", "counts": counts, "attention": attention}


def cmd_reset(args) -> dict:
    vid = parse_video_id(args.video)
    conn = connect()
    if not fetch_row(conn, vid):
        raise SystemExit(f"큐에 없는 영상: {vid}")
    if args.accept:  # 사람이 확인했으니 이 단계는 통과한 것으로 보고 다음 단계부터 진행한다
        update_row(conn, vid, status="pending", skip_reason=None, error=None, finished_at=None)
    elif args.skip:
        update_row(conn, vid, status="skipped", skip_reason=args.skip, error=None, finished_at=datetime.now())
    else:
        row = fetch_row(conn, vid)
        if args.redo_from:
            redo = STAGES.index(args.redo_from)
        elif row["status"] == "needs_review" and row["stage"] in STAGES:
            redo = STAGES.index(row["stage"])  # 확인이 필요했던 단계를 다시 한다
        else:
            redo = STAGES.index(row["stage"]) + 1 if row["stage"] in STAGES else 0  # failed: 멈춘 단계부터
        stage = STAGES[redo - 1] if redo else None  # 마지막으로 끝난 단계를 redo 바로 앞으로 둔다
        update_row(conn, vid, status="pending", stage=stage, skip_reason=None, error=None, finished_at=None)
    return {"status": "ok", "video": summary(fetch_row(conn, vid))}


def cmd_michelin(args) -> dict:
    """Parse 하루 한도로 못 채운 미슐랭을 채우고, 이미 적재된 영상은 michelin_status 를 DB 에 반영한다."""
    conn = connect()
    with conn.cursor() as cur:
        cur.execute("SELECT video_id FROM video_queue WHERE michelin_pending ORDER BY created_at")
        vids = [r[0] for r in cur.fetchall()]
    if not vids:
        return {"status": "ok", "message": "미슐랭 대기 영상 없음"}
    resolved = []
    with run_lock():
        first = Ctx(conn, {"video_id": vids[0], "url": watch_url(vids[0])})
        first.run("fill_michelin", PIPELINE / "fill_michelin.py", ["--only-videos", ",".join(vids), "--max-calls", str(args.max_calls)])
        for vid in vids:
            row = fetch_row(conn, vid)
            if michelin_unchecked(csv_rows(vid)[1]):
                continue
            if row["status"] == "loaded":
                load_restaurants_for(Ctx(conn, row), "reload(michelin)")
            update_row(conn, vid, michelin_pending=False)
            resolved.append(vid)
    return {"status": "ok", "resolved": resolved, "still_pending": [v for v in vids if v not in resolved]}


def main() -> None:
    ap = argparse.ArgumentParser(description="새 영상 1개를 gate -> places -> captions -> extract -> postprocess -> load 순서로 처리한다.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init-db", help="video_queue 테이블을 만든다").set_defaults(fn=cmd_init_db)

    p = sub.add_parser("discover", help="채널의 새 영상을 큐에 넣는다")
    p.add_argument("--channel", default=DEFAULT_CHANNEL)
    p.add_argument("--recent", type=int, default=10, help="채널 최신순 앞에서 몇 개를 볼지")
    p.add_argument("--dry-run", action="store_true", help="큐에 넣지 않고 새 영상만 보여 준다")
    p.set_defaults(fn=cmd_discover)

    p = sub.add_parser("enqueue", help="영상을 직접 큐에 넣는다")
    p.add_argument("videos", nargs="+")
    p.set_defaults(fn=cmd_enqueue)

    p = sub.add_parser("run", help="영상을 처리한다")
    p.add_argument("videos", nargs="*", help="영상 ID 또는 URL")
    p.add_argument("--next", type=int, default=0, help="대기·실패 영상 N개를 오래된 순으로 처리")
    p.add_argument("--stop-after", choices=STAGES, help="이 단계까지만 처리하고 멈춘다 (검증용, 다시 run 하면 이어서)")
    p.set_defaults(fn=cmd_run)

    sub.add_parser("status", help="상태별 건수와 확인이 필요한 영상").set_defaults(fn=cmd_status)

    p = sub.add_parser("reset", help="needs_review·failed 영상을 다시 대기로 돌리거나 제외한다")
    p.add_argument("video")
    p.add_argument("--redo-from", choices=STAGES, help="이 단계부터 다시 (기본: 멈춘 단계부터)")
    p.add_argument("--skip", metavar="사유", help="처리하지 않고 skipped 로 둔다")
    p.add_argument("--accept", action="store_true", help="needs_review 를 사람이 확인해 문제없다고 보고 다음 단계부터 진행한다")
    p.set_defaults(fn=cmd_reset)

    p = sub.add_parser("michelin", help="못 채운 미슐랭을 채우고 DB 에 반영한다")
    p.add_argument("--max-calls", type=int, default=90)
    p.set_defaults(fn=cmd_michelin)

    args = ap.parse_args()
    if args.cmd == "run" and not (args.videos or args.next):
        ap.error("영상 ID 를 주거나 --next N 을 지정하세요")
    print(json.dumps(args.fn(args), ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
