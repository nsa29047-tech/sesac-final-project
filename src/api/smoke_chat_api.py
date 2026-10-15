"""챗봇 API 스모크 시험. 서버(src/api/chat_server.py)를 먼저 띄운 뒤 실행한다.

  uv run python src/api/smoke_chat_api.py [--base http://127.0.0.1:8000] [--no-llm]

--no-llm 이면 OpenAI를 부르지 않는 시험(헬스, 상세, 검증 오류, CORS)만 한다. 기본은 실제 질문 3개(SQL 질문, 후속 질문, 잡담)를 보내므로
OpenAI 호출이 수십 번 나간다(몇 센트). 지명 좌표를 조회하는 질문(Places 하루 100회 한도)은 쓰지 않는다.
"""
import argparse
import json
import sys
import time
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8000"
results = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append(ok)
    print(f"{'ok  ' if ok else 'FAIL'}  {name}" + (f"  ({detail})" if detail else ""))


def request(method, path, body=None, headers=None, timeout=90):
    req = urllib.request.Request(BASE + path, method=method, headers={"Content-Type": "application/json", **(headers or {})},
                                 data=json.dumps(body).encode() if body is not None else None)
    try:
        return urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as e:
        return e


def json_of(resp):
    with resp:
        return json.loads(resp.read())


def stream_chat(thread_id: str, message: str) -> dict:
    """SSE 를 끝까지 읽어 이벤트 순서와 내용을 모은다."""
    resp = request("POST", "/api/chat", {"thread_id": thread_id, "message": message})
    out = {"status": resp.status, "order": [], "text": "", "restaurants": None, "tools": [], "error": None, "done": None}
    event = None
    with resp:
        for raw in resp:
            line = raw.decode("utf-8").rstrip("\n")
            if line.startswith("event: "):
                event = line[7:]
            elif line.startswith("data: "):
                data = json.loads(line[6:])
                if not out["order"] or out["order"][-1] != event:
                    out["order"].append(event)
                if event == "token":
                    out["text"] += data["text"]
                elif event == "status":
                    out["tools"].append(data["tool"])
                elif event == "restaurants":
                    out["restaurants"] = data["restaurants"]
                elif event == "error":
                    out["error"] = data
                elif event == "done":
                    out["done"] = data
    return out


def main(no_llm: bool) -> int:
    h = json_of(request("GET", "/api/health"))
    check("health", h == {"ok": True, "db": True}, str(h))

    r = request("GET", "/api/restaurants/1")
    d = json_of(r) if r.status == 200 else {}
    check("상세 200 + 구조", r.status == 200 and {"hours", "notes", "menus", "videos"} <= set(d), d.get("name", ""))
    check("상세 404", request("GET", "/api/restaurants/999999").code == 404)

    check("thread_id 검증(422)", request("POST", "/api/chat", {"thread_id": "bad id!!", "message": "안녕"}).code == 422)
    check("빈 메시지(422)", request("POST", "/api/chat", {"thread_id": "s-1", "message": ""}).code == 422)
    check("500자 초과(422)", request("POST", "/api/chat", {"thread_id": "s-2", "message": "가" * 501}).code == 422)

    ok = request("OPTIONS", "/api/chat", headers={"Origin": "http://localhost:3000", "Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "content-type"})
    check("CORS 허용 origin", ok.status == 200 and ok.headers.get("access-control-allow-origin") == "http://localhost:3000")
    bad = request("OPTIONS", "/api/chat", headers={"Origin": "http://evil.example", "Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "content-type"})
    check("CORS 다른 origin 거부", getattr(bad, "code", 200) == 400)

    if not no_llm:
        tid = f"smoke-{int(time.time())}"
        a = stream_chat(tid, "서울에 있는 미슐랭 스타 식당 알려줘")
        check("SQL 질문: 이벤트 순서", a["order"] == ["thread", "status", "token", "restaurants", "done"], str(a["order"]))
        check("SQL 질문: 도구 호출·카드", "find_restaurants" in a["tools"] and bool(a["restaurants"]) and a["done"]["answer"] == a["text"])
        check("카드가 구조화됨", all(c["michelin"] and c["videos"] and c["region"] for c in a["restaurants"]))

        b = stream_chat(tid, "그중에서 가장 평점 높은 곳 하나만 다시 알려줘")
        check("후속 질문(대화 기억)", b["error"] is None and bool(b["restaurants"]) and b["restaurants"][0]["restaurant_id"] in {c["restaurant_id"] for c in a["restaurants"]})

        c = stream_chat(f"{tid}-chat", "안녕")
        check("잡담: 도구 없이 답변", c["order"] == ["thread", "token", "restaurants", "done"] and c["restaurants"] == [], str(c["order"]))

    print(f"\n{sum(results)}/{len(results)} 통과")
    return 0 if all(results) else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=BASE)
    ap.add_argument("--no-llm", action="store_true")
    args = ap.parse_args()
    BASE = args.base
    sys.exit(main(args.no_llm))
