"""맛집 챗봇 HTTP API (Next.js 프론트가 호출한다). 설계: CHATBOT_API_DESIGN.md

- POST /api/chat                SSE 스트리밍. 이벤트 순서: status(도구 호출, 0회 이상) -> token(답변 조각, 여러 번) -> restaurants(카드·지도용) -> done. 오류면 error.
- GET  /api/restaurants/{id}    식당 상세(팝업용) JSON
- GET  /api/health              서버·DB 확인

로컬 데모용이다: 기본은 127.0.0.1 에만 열고, CORS 는 http://localhost:3000 만 허용한다. 인증·요청 제한은 없다(외부에 공개하면 추가해야 한다).
DB 는 읽기 전용 계정(READONLY_POSTGRES_URI)으로만 접속한다. 쓰기 권한 접속 문자열(POSTGRES_URI)은 이 프로세스의 환경에서 지운다.
대화 기억은 서버 메모리(MemorySaver)라 서버를 재시작하면 사라지고, 서버 프로세스는 1개로 띄운다.

실행:  uv run python src/api/chat_server.py [--host 127.0.0.1] [--port 8000]
"""
import argparse
import asyncio
import json
import logging
import os
import re
import sys
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from langchain_core.messages import AIMessage, AIMessageChunk, ToolMessage
from langgraph.checkpoint.memory import MemorySaver
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "src" / "db"))
load_dotenv(ROOT / ".env")

import rag_search as rs  # noqa: E402
from chatbot.cards import collect, pick  # noqa: E402
from chatbot.detail import fetch_detail  # noqa: E402
from chatbot.graph import build_graph  # noqa: E402
from chatbot.schemas import RestaurantDetail  # noqa: E402
from chatbot.serialize import restaurant_cards, to_detail  # noqa: E402
from chatbot.tools import run_query, summarize  # noqa: E402

os.environ.pop("POSTGRES_URI", None)   # 챗봇 서버는 쓰기 권한 접속 문자열을 쓰지 않는다(rag_search.connect 도 READONLY_POSTGRES_URI 만 본다)

log = logging.getLogger("chat_server")
MESSAGE_MAX = 500          # 질문 길이 제한(글자)
TIMEOUT_SEC = 60           # 한 질문의 전체 응답 시간 제한
ALLOWED_ORIGINS = [o.strip() for o in os.getenv("CHAT_ALLOWED_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000").split(",") if o.strip()]
THREAD_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

graph = None
_active: set = set()          # 지금 답변 중인 thread_id (같은 대화에서 동시에 두 질문이 돌지 않게)
_active_lock = threading.Lock()


@asynccontextmanager
async def lifespan(app: FastAPI):
    global graph
    if not os.environ.get("READONLY_POSTGRES_URI"):
        raise RuntimeError("READONLY_POSTGRES_URI 가 없습니다(.env). 챗봇 서버는 읽기 전용 계정으로만 DB에 접속합니다.")
    rs.enable_pool()
    graph = build_graph(MemorySaver(), cards=True)   # cards=True: 화면이 카드를 그리므로 답변은 요약과 추천 이유만
    yield


app = FastAPI(title="sesac chatbot API", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=ALLOWED_ORIGINS, allow_methods=["GET", "POST"], allow_headers=["Content-Type"])


class ChatRequest(BaseModel):
    thread_id: str = Field(description="대화 구분 값(클라이언트가 만든 UUID 등). 새 대화는 새 값")
    message: str = Field(min_length=1, max_length=MESSAGE_MAX)


def sse(event: str, data: Dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _args_of(call: Dict[str, Any]) -> Dict[str, Any]:
    return {k: v for k, v in call["args"].items() if v not in (None, "", [])}


def run_graph(question: str, thread_id: str, put, cancelled: threading.Event) -> None:
    """작업 스레드: 그래프를 스트리밍하며 이벤트를 put(event, data)으로 내보낸다. Streamlit 의 stream_answer 와 같은 방식이다."""
    text, found, map_ids = "", {}, []
    try:
        for mode, data in graph.stream({"messages": [("user", question)]}, {"configurable": {"thread_id": thread_id}},
                                       stream_mode=["messages", "updates"]):
            if cancelled.is_set():
                return
            if mode == "messages":
                chunk, meta = data
                # 라우터의 구조화 출력 같은 내부 호출은 거르고 답변 노드의 토큰만 쓴다
                if meta.get("langgraph_node") in ("agent", "chat") and isinstance(chunk, AIMessageChunk) and chunk.content:
                    text += chunk.content
                    put("token", {"text": chunk.content})
            else:
                for node, update in data.items():
                    for m in (update or {}).get("messages", []):
                        if node == "agent" and isinstance(m, AIMessage) and m.tool_calls:
                            for call in m.tool_calls:
                                put("status", {"tool": call["name"], "args": _args_of(call)})
                        elif node == "tools" and isinstance(m, ToolMessage):
                            collect(m.content, found)
                            artifact = m.artifact if isinstance(m.artifact, dict) else {}
                            for i in artifact.get("map_ids", []):   # find_restaurants가 조건에 맞는 전체 식당을 지도용으로 따로 알려 준다
                                if i not in map_ids:
                                    map_ids.append(i)
        cards = restaurant_cards(pick(found, text))   # 답변에 이름이 나온 식당만, 처음 언급된 순서로
        shown = {c.restaurant_id for c in cards}
        extra = restaurant_cards(summarize([i for i in map_ids if i not in shown])) if cards else []   # 카드로 소개하지 않았지만 조건에 맞는 식당(지도에만 표시)
        put("restaurants", {"restaurants": [c.model_dump(mode="json") for c in cards], "map_extra": [c.model_dump(mode="json") for c in extra]})
        put("done", {"answer": text})
    except Exception as e:   # 키·DB·OpenAI 오류 등. 내부 정보는 응답에 넣지 않고 서버 로그에만 남긴다
        log.exception("chat failed")
        put("error", {"code": "internal", "message": f"답변을 만드는 중 오류가 났습니다: {type(e).__name__}"})


@app.post("/api/chat")
async def chat(body: ChatRequest, request: Request):
    if not THREAD_ID_RE.match(body.thread_id):
        raise HTTPException(status_code=422, detail="thread_id 는 영문·숫자·-_ 64자 이내여야 합니다")
    with _active_lock:
        if body.thread_id in _active:
            raise HTTPException(status_code=409, detail="이 대화는 이미 답변 중입니다")
        _active.add(body.thread_id)

    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()
    cancelled = threading.Event()

    def put(event: str, data: Dict[str, Any]) -> None:
        loop.call_soon_threadsafe(queue.put_nowait, (event, data))

    worker = threading.Thread(target=run_graph, args=(body.message, body.thread_id, put, cancelled), daemon=True)

    async def events():
        deadline = time.monotonic() + TIMEOUT_SEC
        try:
            worker.start()
            yield sse("thread", {"thread_id": body.thread_id})
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    cancelled.set()
                    yield sse("error", {"code": "timeout", "message": f"{TIMEOUT_SEC}초 안에 답변하지 못했습니다"})
                    return
                try:
                    event, data = await asyncio.wait_for(queue.get(), timeout=min(remaining, 1.0))
                except asyncio.TimeoutError:
                    if await request.is_disconnected():   # 브라우저가 연결을 끊었다(새 질문·페이지 이동): 그래프 실행을 멈춘다
                        cancelled.set()
                        return
                    continue
                yield sse(event, data)
                if event in ("done", "error"):
                    return
        finally:
            cancelled.set()
            with _active_lock:
                _active.discard(body.thread_id)

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/api/restaurants/{restaurant_id}", response_model=RestaurantDetail)
def restaurant_detail(restaurant_id: int):
    d = fetch_detail(restaurant_id)
    if not d:
        raise HTTPException(status_code=404, detail="식당을 찾지 못했습니다")
    return to_detail(d)


@app.get("/api/health")
def health() -> Dict[str, Any]:
    try:
        ok = run_query("SELECT 1 AS ok")[0]["ok"] == 1
    except Exception:
        log.exception("health: db failed")
        ok = False
    return {"ok": ok, "db": ok}


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="맛집 챗봇 HTTP API (로컬 데모용)")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    args = ap.parse_args()
    uvicorn.run(app, host=args.host, port=args.port)
