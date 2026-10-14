"""n8n(Docker)이 호출하는 파이프라인 HTTP API. process_video.py 를 감싼 얇은 서버다.

n8n 컨테이너 안에는 이 프로젝트의 Python 환경(uv, .venv, 키, data/)이 없으므로, 호스트에서 이 서버를 띄우고
n8n 은 http://host.docker.internal:8765 로 호출한다. 처리 로직은 process_video.py 에 있고 여기서는 부르기만 한다.

- 오래 걸리는 작업(영상 처리 최대 20분, 미슐랭 채우기)은 하위 프로세스로 띄우고 바로 돌려준다(202). 결과는 GET /job 으로 확인한다.
  한 번에 작업 하나만 돌린다(이미 실행 중이면 409). process_video.py 의 잠금 파일과도 맞물려 CLI 로 직접 돌리는 중에도 겹치지 않는다.
- 모든 요청은 헤더 `X-API-Key` 가 환경변수 PIPELINE_API_TOKEN 과 같아야 한다(.env). 토큰이 없으면 서버가 시작되지 않는다.

실행:  uv run python src/api/pipeline_server.py [--host 127.0.0.1] [--port 8765]
       Docker Desktop 에서 host.docker.internal 로 닿지 않으면 --host 0.0.0.0 (토큰이 있어도 같은 네트워크에 열리니 주의).
"""

import argparse
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Optional

import uvicorn
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src" / "pipeline"))
load_dotenv(ROOT / ".env")
import process_video as pv  # noqa: E402

TOKEN = os.getenv("PIPELINE_API_TOKEN")
JOB_LOG = pv.DATA / "logs" / "api_job.log"
STAGE_ORDER = pv.STAGES


def require_token(x_api_key: Optional[str] = Header(default=None)) -> None:
    if not TOKEN or x_api_key != TOKEN:
        raise HTTPException(status_code=401, detail="X-API-Key 가 올바르지 않습니다")


app = FastAPI(title="sesac pipeline API", dependencies=[Depends(require_token)])

_job_lock = threading.Lock()
_job: dict = {}   # proc, args, started_at, out_path


def _external_lock_held() -> bool:
    return pv.LOCK_FILE.exists() and time.time() - pv.LOCK_FILE.stat().st_mtime < pv.LOCK_STALE_SEC


def _job_view() -> dict:
    proc = _job.get("proc")
    running = proc is not None and proc.poll() is None
    view = {"running": running, "external_lock": (not running) and _external_lock_held(),
            "args": _job.get("args"), "started_at": _job.get("started_at")}
    if proc is not None and not running:
        view["exit_code"] = proc.returncode
        out = Path(_job["out_path"])
        lines = out.read_text(encoding="utf-8", errors="replace").strip().splitlines() if out.exists() else []
        try:
            view["result"] = json.loads(lines[-1]) if lines else None   # process_video.py 는 마지막 줄에 JSON 한 덩어리를 낸다
        except json.JSONDecodeError:
            view["result"] = None
            view["output_tail"] = lines[-5:]
    return view


def _start_job(args: list) -> dict:
    with _job_lock:
        if _job_view()["running"] or _external_lock_held():
            raise HTTPException(status_code=409, detail="다른 작업이 진행 중입니다 (GET /job 으로 확인)")
        JOB_LOG.parent.mkdir(parents=True, exist_ok=True)
        out_path = JOB_LOG.with_name("api_job_out.txt")
        with open(JOB_LOG, "a", encoding="utf-8") as log:
            log.write(f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} process_video.py {' '.join(args)}\n")
        proc = subprocess.Popen(
            [sys.executable, str(ROOT / "src" / "pipeline" / "process_video.py"), *args], cwd=ROOT,
            stdout=open(out_path, "w", encoding="utf-8"), stderr=open(JOB_LOG, "a", encoding="utf-8"),
            env={**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"})
        _job.clear()
        _job.update(proc=proc, args=args, started_at=time.strftime("%Y-%m-%d %H:%M:%S"), out_path=str(out_path))
        return _job_view()


def _eligible_count() -> int:
    conn = pv.connect()
    with conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM video_queue WHERE status = 'pending' OR (status = 'failed' AND attempts < %s)", (pv.MAX_ATTEMPTS,))
        return cur.fetchone()[0]


# ------------------------------------------------------------- 요청 모델
class DiscoverBody(BaseModel):
    channel: str = pv.DEFAULT_CHANNEL
    recent: int = Field(10, ge=1, le=50, description="채널 최신순 앞에서 몇 개를 볼지")
    dry_run: bool = False


class RunBody(BaseModel):
    video_id: Optional[str] = Field(None, description="지정하면 이 영상, 없으면 대기·실패(재시도 가능) 영상 중 가장 오래된 것")
    stop_after: Optional[str] = None


class ResetBody(BaseModel):
    accept: bool = False
    skip: Optional[str] = None
    redo_from: Optional[str] = None


class MichelinBody(BaseModel):
    max_calls: int = Field(90, ge=1, le=100)


# ------------------------------------------------------------- 엔드포인트
@app.get("/health")
def health() -> dict:
    return {"ok": True}


@app.post("/discover")
def discover(body: DiscoverBody) -> dict:
    """채널의 새 영상을 찾아 큐에 넣는다 (yt-dlp, 수 초)."""
    return pv.cmd_discover(SimpleNamespace(channel=body.channel, recent=body.recent, dry_run=body.dry_run))


@app.post("/run", status_code=202)
def run(body: RunBody) -> dict:
    """영상 하나를 처리하는 작업을 시작한다. 끝났는지는 GET /job (running=false) 으로 본다."""
    if body.video_id:
        args = ["run", pv.parse_video_id(body.video_id)]
    else:
        remaining = _eligible_count()
        if remaining == 0:
            return {"started": False, "reason": "처리할 영상 없음", "remaining": 0}
        args = ["run", "--next", "1"]
    if body.stop_after:
        args += ["--stop-after", body.stop_after]
    job = _start_job(args)
    return {"started": True, "job": job}


@app.post("/michelin", status_code=202)
def michelin(body: MichelinBody) -> dict:
    """Parse 하루 한도로 못 채운 미슐랭을 채우는 작업을 시작한다 (최대 약 20분)."""
    conn = pv.connect()
    with conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM video_queue WHERE michelin_pending")
        if cur.fetchone()[0] == 0:
            return {"started": False, "reason": "미슐랭 대기 영상 없음"}
    return {"started": True, "job": _start_job(["michelin", "--max-calls", str(body.max_calls)])}


@app.get("/job")
def job() -> dict:
    """현재(또는 마지막) 작업 상태. running=false 가 되면 result 에 process_video.py 의 JSON 결과가 들어 있다."""
    return _job_view()


@app.get("/status")
def status() -> dict:
    """상태별 건수와 사람이 봐야 할 영상 (needs_review, failed, 미슐랭 대기)."""
    out = pv.cmd_status(None)
    out["eligible_to_run"] = _eligible_count()
    return out


@app.get("/videos/{video_id}")
def video(video_id: str) -> dict:
    conn = pv.connect()
    row = pv.fetch_row(conn, pv.parse_video_id(video_id))
    if row is None:
        raise HTTPException(status_code=404, detail="큐에 없는 영상")
    return pv.summary(row)


@app.post("/videos/{video_id}/reset")
def reset(video_id: str, body: ResetBody) -> dict:
    """needs_review·failed 영상을 사람이 확인한 뒤 되돌린다 (accept: 문제없음으로 진행 / skip: 제외 / redo_from: 특정 단계부터)."""
    if body.redo_from and body.redo_from not in STAGE_ORDER:
        raise HTTPException(status_code=422, detail=f"redo_from 은 {STAGE_ORDER} 중 하나")
    return pv.cmd_reset(SimpleNamespace(video=video_id, accept=body.accept, skip=body.skip, redo_from=body.redo_from))


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="n8n 이 호출하는 파이프라인 HTTP API")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    args = ap.parse_args()
    if not TOKEN:
        sys.exit("PIPELINE_API_TOKEN 이 .env 에 없습니다. 긴 무작위 문자열로 하나 만들어 넣으세요 (값은 채팅·커밋에 남기지 않는다).")
    uvicorn.run(app, host=args.host, port=args.port)
