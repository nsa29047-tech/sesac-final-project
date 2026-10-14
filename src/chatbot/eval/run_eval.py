"""챗봇 평가: 케이스를 에이전트에 돌리고 도구 선택, 결과 정확도, 근거 없는 추천(환각)을 점검한다.

사용:
  uv run python src/chatbot/eval/run_eval.py                  # 전체
  uv run python src/chatbot/eval/run_eval.py --ids sql-3star taste-lamb --workers 1
  uv run python src/chatbot/eval/run_eval.py --kind taste

점검 항목(케이스마다 PASS/FAIL, 이유 표시)
  tool      기대한 도구를 하나라도 호출했는가 (chitchat은 도구 호출이 없어야 함)
  result    kind별 정답 판정 (cases.py 설명 참고)
  grounded  답변에 나온 식당 이름이 모두 도구 결과에 있는가. DB 식당 이름이 도구 결과에 없는데 답변에 있으면 근거 없는 추천
결과는 data/eval/eval_YYYYMMDD_HHMMSS.json 에 저장한다(data/ 는 git 제외). 호출은 케이스당 gpt-4o-mini 2~4회 + 임베딩이라 41개에 약 $0.1 이하.
"""
import argparse
import json
import re
import sys
import uuid
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "src" / "db"))

from langchain_core.messages import AIMessage, ToolMessage  # noqa: E402
from langgraph.checkpoint.memory import MemorySaver  # noqa: E402

import rag_search as rs  # noqa: E402
from chatbot.eval.cases import CASES  # noqa: E402
from chatbot.graph import build_graph  # noqa: E402

NONE_WORDS = ("없", "찾지 못", "찾을 수 없", "확인되지")


def sql_ids(sql):
    conn = rs.connect()
    try:
        conn.set_session(readonly=True)
        with conn.cursor() as cur:
            cur.execute(sql)           # 파라미터를 넘기지 않으므로 % 는 그대로 쓴다
            return [r[0] for r in cur.fetchall()]
    finally:
        conn.close()


def all_names():
    conn = rs.connect()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT restaurant_id, name_ko, name_official FROM restaurants")
            return cur.fetchall()
    finally:
        conn.close()


def run_case(app, case):
    out = app.invoke({"messages": [("user", case["q"])]}, {"configurable": {"thread_id": str(uuid.uuid4())}})
    tools, returned = [], {}
    for m in out["messages"]:
        if isinstance(m, AIMessage):
            tools += [t["name"] for t in (m.tool_calls or [])]
        elif isinstance(m, ToolMessage):
            try:
                data = json.loads(m.content)
            except ValueError:
                continue
            for r in data.get("restaurants", []) if isinstance(data, dict) else []:
                returned[r["restaurant_id"]] = [r.get("name"), r.get("name_official"), r.get("region"), r.get("address")]
    return tools, returned, out["messages"][-1].content


def judge(case, tools, returned, answer, names):
    """(통과 여부, 이유, 세부 지표)"""
    reasons, info = [], {}
    kind = case["kind"]
    ids = set(returned)
    info["returned"] = len(ids)

    # 도구 선택
    if kind == "chitchat":
        tool_ok = not tools
    else:
        tool_ok = any(t in case["tools"] for t in tools)
    if not tool_ok:
        reasons.append(f"도구 선택: 호출={tools or '없음'}, 기대={case.get('tools') or '없음'}")

    # 근거 없는 추천: 답변에 나온 DB 식당 이름이 도구 결과에 없음
    shown = {n.lower() for v in returned.values() for n in v if n}   # 이름·지역·주소 (식당 이름이 지역명인 경우 라사르테 등을 근거로 인정)
    low = answer.lower()
    bold = " ".join(re.findall(r"\*\*(.+?)\*\*", answer)).lower()
    gl = bold or low   # 추천 목록의 식당 이름은 굵게 표시된다. 지역 설명에 나온 번역어(라사르테 등)는 이름으로 세지 않는다
    ungrounded = sorted({n for _, ko, off in names for n in (ko, off) if n and len(n) >= 3 and n.lower() in gl and n.lower() not in shown
                         and not any(n.lower() in s for s in shown)})
    grounded = not ungrounded
    if not grounded:
        reasons.append(f"근거 없는 식당 언급: {ungrounded[:3]}")

    result_ok = True
    if kind == "sql":
        gold = set(sql_ids(case["gold_sql"]))
        info["gold"] = len(gold)
        if not gold:
            return None, "낡은 케이스(정답이 0건)", info
        outside = ids - gold
        info["precision"] = round(1 - len(outside) / len(ids), 2) if ids else None
        if not ids:
            result_ok = False; reasons.append(f"결과 없음(정답 {len(gold)}곳)")
        elif outside:
            result_ok = False; reasons.append(f"조건 밖 식당 {len(outside)}/{len(ids)}곳: {[returned[i][0] for i in list(outside)[:3]]}")
    elif kind == "taste":
        hit = set(sql_ids(case["hit_sql"]))
        info["hit_gold"] = len(hit)
        if not hit:
            return None, "낡은 케이스(정답 후보가 0건)", info
        if not ids:
            result_ok = False; reasons.append(f"결과 없음(후보 {len(hit)}곳)")
        else:
            info["hit_ratio"] = round(len(ids & hit) / len(ids), 2)
            if not (ids & hit):
                result_ok = False; reasons.append("정답 후보가 하나도 없음")
        if case.get("must_sql") and ids:
            outside = ids - set(sql_ids(case["must_sql"]))
            if outside:
                result_ok = False; reasons.append(f"하드 조건 위반 {len(outside)}곳: {[returned[i][0] for i in list(outside)[:3]]}")
    elif kind == "none":
        if sql_ids(case["empty_sql"]):
            return None, "낡은 케이스(DB에 해당 항목이 생김)", info
        mentioned = [n for _, ko, off in names for n in (ko, off) if n and len(n) >= 3 and n.lower() in low]
        if mentioned:
            result_ok = False; reasons.append(f"없어야 하는데 식당 언급: {mentioned[:3]}")
        if not any(w in answer for w in NONE_WORDS):
            result_ok = False; reasons.append("'없다'는 답이 아님")
    elif kind == "info":
        vals = case.get("contains") or [str(v) for v in sql_ids(case["contains_sql"])]
        if not any(v and v in answer for v in vals):
            result_ok = False; reasons.append(f"답변에 기대 값 없음(예: {vals[:3]})")
    ok = tool_ok and result_ok and grounded
    return ok, "; ".join(reasons), info


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ids", nargs="*")
    ap.add_argument("--kind")
    ap.add_argument("--no-geo", action="store_true", help="지명 좌표 조회(Places)가 필요한 케이스는 건너뛴다. Places 일일 한도(100회) 보호용")
    ap.add_argument("--workers", type=int, default=4)
    a = ap.parse_args()
    cases = [c for c in CASES if (not a.ids or c["id"] in a.ids) and (not a.kind or c["kind"] == a.kind) and not (a.no_geo and c.get("geo"))]
    app = build_graph(MemorySaver())
    names = all_names()
    print(f"{len(cases)}개 케이스 실행 (워커 {a.workers})")

    def work(case):
        try:
            tools, returned, answer = run_case(app, case)
            ok, why, info = judge(case, tools, returned, answer, names)
        except Exception as e:     # 한 케이스의 오류가 전체를 멈추지 않게
            tools, returned, answer, ok, why, info = [], {}, "", False, f"오류: {e!r}", {}
        return dict(id=case["id"], kind=case["kind"], question=case["q"], ok=ok, why=why, tools=tools, info=info, answer=answer)

    with ThreadPoolExecutor(a.workers) as ex:
        results = list(ex.map(work, cases))

    by_kind = defaultdict(lambda: [0, 0, 0])   # 통과, 실패, 낡음
    for r in results:
        mark = "STALE" if r["ok"] is None else ("PASS" if r["ok"] else "FAIL")
        by_kind[r["kind"]][2 if r["ok"] is None else (0 if r["ok"] else 1)] += 1
        print(f"[{mark}] {r['id']:<24} {r['question']}" + (f"\n        -> {r['why']}" if r["why"] else ""))
    print("\n== 요약 ==")
    tp = tf = 0
    for k, (p, f, s) in by_kind.items():
        print(f"{k:<9} 통과 {p} / 실패 {f}" + (f" / 낡음 {s}" if s else ""))
        tp += p; tf += f
    print(f"전체      통과 {tp} / {tp + tf}  ({tp / max(1, tp + tf):.0%})")

    outdir = ROOT / "data" / "eval"
    outdir.mkdir(parents=True, exist_ok=True)
    path = outdir / f"eval_{datetime.now():%Y%m%d_%H%M%S}.json"
    path.write_text(json.dumps(results, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print(f"저장: {path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
